import asyncio
import json
import os
from dataclasses import dataclass
from typing import Any
from urllib import error, request

import anthropic
import docker
from dotenv import load_dotenv

load_dotenv()


anthropic_client = anthropic.AsyncAnthropic()
docker_client = docker.from_env()


@dataclass
class AsyncOllamaClient:
    base_url: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    timeout_seconds: int = 300

    def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            url=f"{self.base_url.rstrip('/')}{path}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as resp:
                data = resp.read().decode("utf-8")
                return json.loads(data)
        except error.HTTPError as e:
            # Surface Ollama server errors as structured responses instead of crashing the agent loop.
            return {
                "error": "HTTPError",
                "status": e.code,
                "reason": e.reason,
                "body": e.read().decode("utf-8", errors="ignore"),
            }
        except error.URLError as e:
            return {
                "error": "URLError",
                "reason": str(e.reason),
            }

    async def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": stream,
        }
        if tools:
            payload["tools"] = tools
        return await asyncio.to_thread(self._post_json, "/api/chat", payload)


ollama_client = AsyncOllamaClient()
