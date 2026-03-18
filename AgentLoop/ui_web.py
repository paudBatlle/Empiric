import asyncio
from contextlib import suppress
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from agent import EventText, EventToolResult, EventToolUse
from app_runtime import (
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_PROVIDER,
    build_model_name,
    create_orchestrated_loop,
    warmup_ollama_if_needed,
)
from tools import start_python_dev_container


FRONTEND_DIST_DIR = Path(__file__).resolve().parent.parent / "frontend" / "dist"

app = FastAPI(title="Empiric Web UI Backend")


class WebSocketSession:
    def __init__(self, websocket: WebSocket) -> None:
        self.websocket = websocket
        self.send_lock = asyncio.Lock()
        self.loop_lock = asyncio.Lock()
        self.orchestrated_loop = None
        self.current_model = ""
        self.running_task: asyncio.Task[None] | None = None
        self.pending_answer_future: asyncio.Future[str] | None = None
        self.worker_events: asyncio.Queue[object] = asyncio.Queue()

    async def send(self, payload: dict[str, Any]) -> None:
        async with self.send_lock:
            await self.websocket.send_json(payload)

    async def display_to_user(self, text: str) -> None:
        await self.send({"type": "display", "text": text})

    async def ask_user(self, query: str) -> str:
        wait_future = asyncio.get_running_loop().create_future()
        self.pending_answer_future = wait_future
        await self.send({"type": "ask_user", "query": query})
        try:
            answer = await wait_future
        finally:
            if self.pending_answer_future is wait_future:
                self.pending_answer_future = None
        return answer

    def _emit_worker_event(self, event: object) -> None:
        # Worker events fire from a sync callback; queue to preserve ordering.
        self.worker_events.put_nowait(event)

    async def _send_event(self, event: object) -> None:
        if isinstance(event, EventText):
            await self.send({"type": "text", "source": event.source, "text": event.text})
            return
        if isinstance(event, EventToolUse):
            await self.send(
                {
                    "type": "tool_use",
                    "source": event.source,
                    "tool": event.tool.__class__.__name__,
                }
            )
            return
        if isinstance(event, EventToolResult):
            await self.send(
                {
                    "type": "tool_result",
                    "source": event.source,
                    "result": event.result or "",
                }
            )

    async def _drain_worker_events(self) -> None:
        while not self.worker_events.empty():
            event = self.worker_events.get_nowait()
            await self._send_event(event)

    async def create_session(self, provider: str, model: str) -> None:
        async with self.loop_lock:
            if self.running_task and not self.running_task.done():
                raise RuntimeError(
                    "A request is still running. Wait for it to finish before starting a new session."
                )

            selected_model = build_model_name(provider, model)
            await asyncio.to_thread(warmup_ollama_if_needed, selected_model)
            loop_obj = create_orchestrated_loop(
                model=selected_model,
                ask_user=self.ask_user,
                display_to_user=self.display_to_user,
            )
            loop_obj.set_worker_event_callback(self._emit_worker_event)

            self.orchestrated_loop = loop_obj
            self.current_model = selected_model

    async def ensure_default_session(self) -> None:
        if self.orchestrated_loop is not None:
            return
        default_model = (
            DEFAULT_OLLAMA_MODEL if DEFAULT_PROVIDER.strip().lower() == "ollama" else ""
        )
        await self.create_session(DEFAULT_PROVIDER, default_model)

    async def submit_user_message(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        await self.ensure_default_session()
        assert self.orchestrated_loop is not None
        if self.running_task and not self.running_task.done():
            await self.send(
                {
                    "type": "system",
                    "text": "A request is already running. Please wait for completion.",
                }
            )
            return
        self.orchestrated_loop.add_user_message(text)
        self.running_task = asyncio.create_task(self._run_loop())

    async def _run_loop(self) -> None:
        assert self.orchestrated_loop is not None
        try:
            async for event in self.orchestrated_loop.run():
                await self._drain_worker_events()
                await self._send_event(event)
            await self._drain_worker_events()
        except Exception as exc:  # noqa: BLE001
            await self.send({"type": "system", "text": f"Request failed: {exc}"})
        finally:
            await self.send({"type": "done"})

    async def submit_answer(self, answer: str) -> None:
        if self.pending_answer_future is None or self.pending_answer_future.done():
            await self.send({"type": "system", "text": "No pending question to answer."})
            return
        self.pending_answer_future.set_result(answer or "")

    async def close(self) -> None:
        if self.pending_answer_future and not self.pending_answer_future.done():
            self.pending_answer_future.set_result("")
        if self.running_task:
            self.running_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.running_task


@app.on_event("startup")
async def startup_event() -> None:
    try:
        start_python_dev_container("python-dev")
    except RuntimeError:
        # Keep web UI available even when Docker-backed tools are unavailable.
        pass


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/default-model")
async def default_model() -> JSONResponse:
    provider = DEFAULT_PROVIDER.strip().lower() or "ollama"
    model = DEFAULT_OLLAMA_MODEL if provider == "ollama" else ""
    return JSONResponse({"provider": provider, "model": model})


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    session = WebSocketSession(websocket)
    await session.send({"type": "session_ready"})

    try:
        while True:
            msg = await websocket.receive_json()
            msg_type = str(msg.get("type", "")).strip()

            if msg_type == "new_session":
                provider = str(msg.get("provider", DEFAULT_PROVIDER)).strip().lower()
                model = str(msg.get("model", "")).strip()
                try:
                    await session.create_session(provider, model)
                except Exception as exc:  # noqa: BLE001
                    await session.send({"type": "system", "text": f"Failed to start session: {exc}"})
                else:
                    await session.send(
                        {
                            "type": "system",
                            "text": f"Started new session with model `{session.current_model}`.",
                        }
                    )
                continue

            if msg_type == "user_message":
                await session.submit_user_message(str(msg.get("text", "")))
                continue

            if msg_type == "answer":
                await session.submit_answer(str(msg.get("answer", "")))
                continue

            await session.send(
                {"type": "system", "text": f"Unsupported message type `{msg_type}`."}
            )
    except WebSocketDisconnect:
        await session.close()


if FRONTEND_DIST_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST_DIR), html=True), name="frontend")


def main() -> None:
    uvicorn.run("ui_web:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    main()
