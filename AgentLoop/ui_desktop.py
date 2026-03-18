import asyncio
import queue
import threading
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from agent import EventText, EventToolResult, EventToolUse
from app_runtime import (
    DEFAULT_CLAUDE_MODEL,
    DEFAULT_OLLAMA_MODEL,
    build_model_name,
    create_orchestrated_loop,
    warmup_ollama_if_needed,
)
from tools import start_python_dev_container


class DesktopAgentApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Empiric Assistant")
        self.root.geometry("980x700")
        self.root.minsize(760, 560)
        self.root.configure(bg="#f4f6fb")

        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.loop_lock = threading.Lock()
        self.current_loop = None
        self.current_model = ""
        self.is_running = False

        self.provider_var = tk.StringVar(value="claude")
        self.model_var = tk.StringVar(value=DEFAULT_CLAUDE_MODEL)
        self.status_var = tk.StringVar(value="Ready")

        self._configure_styles()
        self._build_layout()
        self._bind_events()
        self._set_busy_state(False)
        self._append_message(
            "Assistant",
            "Welcome. Ask in plain language and I will handle the technical steps for you.",
            "assistant",
        )

        # Keep behavior aligned with the terminal app.
        try:
            start_python_dev_container("python-dev")
        except RuntimeError as exc:
            self._append_message(
                "System",
                f"Docker tools are unavailable right now: {exc}",
                "system",
            )
        self.root.after(100, self._drain_events)

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Card.TFrame", background="#ffffff")
        style.configure("CardTitle.TLabel", background="#ffffff", font=("Helvetica", 16, "bold"))
        style.configure("CardText.TLabel", background="#ffffff", font=("Helvetica", 11))
        style.configure("Primary.TButton", font=("Helvetica", 11, "bold"), padding=(14, 8))
        style.configure("Soft.TLabel", background="#f4f6fb", foreground="#5a6370", font=("Helvetica", 10))

    def _build_layout(self) -> None:
        shell = ttk.Frame(self.root, padding=18, style="Card.TFrame")
        shell.pack(fill="both", expand=True, padx=18, pady=18)

        header = ttk.Frame(shell, style="Card.TFrame")
        header.pack(fill="x", padx=6, pady=(4, 12))
        ttk.Label(header, text="Empiric Assistant", style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text="Simple helper for non-technical users. Type your request and get guided results.",
            style="CardText.TLabel",
        ).pack(anchor="w", pady=(4, 0))

        controls = ttk.Frame(shell, style="Card.TFrame")
        controls.pack(fill="x", padx=6, pady=(0, 12))
        ttk.Label(controls, text="Provider", style="CardText.TLabel").grid(row=0, column=0, sticky="w")
        self.provider_combo = ttk.Combobox(
            controls,
            state="readonly",
            width=10,
            values=("claude", "ollama"),
            textvariable=self.provider_var,
        )
        self.provider_combo.grid(row=1, column=0, padx=(0, 10), sticky="w")

        ttk.Label(controls, text="Model", style="CardText.TLabel").grid(row=0, column=1, sticky="w")
        self.model_entry = ttk.Entry(controls, textvariable=self.model_var, width=34)
        self.model_entry.grid(row=1, column=1, padx=(0, 10), sticky="we")
        controls.grid_columnconfigure(1, weight=1)

        self.reset_button = ttk.Button(
            controls,
            text="Start New Session",
            command=self._reset_session,
        )
        self.reset_button.grid(row=1, column=2, sticky="e")

        transcript_frame = ttk.Frame(shell, style="Card.TFrame")
        transcript_frame.pack(fill="both", expand=True, padx=6)
        self.transcript = tk.Text(
            transcript_frame,
            wrap="word",
            state="disabled",
            bg="#ffffff",
            fg="#1f2530",
            font=("Helvetica", 11),
            padx=14,
            pady=12,
            relief="flat",
            borderwidth=0,
        )
        scrollbar = ttk.Scrollbar(transcript_frame, orient="vertical", command=self.transcript.yview)
        self.transcript.configure(yscrollcommand=scrollbar.set)
        self.transcript.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.transcript.tag_configure("user", foreground="#0f3d75", spacing1=8, spacing3=6)
        self.transcript.tag_configure("assistant", foreground="#1f2530", spacing1=8, spacing3=6)
        self.transcript.tag_configure("tool", foreground="#5d4a00", spacing1=8, spacing3=6)
        self.transcript.tag_configure("system", foreground="#4b5563", spacing1=8, spacing3=6)
        self.transcript.tag_configure("label", font=("Helvetica", 10, "bold"))

        composer = ttk.Frame(shell, style="Card.TFrame")
        composer.pack(fill="x", padx=6, pady=(12, 2))
        self.input_text = tk.Text(
            composer,
            height=3,
            wrap="word",
            font=("Helvetica", 11),
            relief="solid",
            borderwidth=1,
        )
        self.input_text.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self.send_button = ttk.Button(
            composer,
            text="Send",
            command=self._send_message,
            style="Primary.TButton",
        )
        self.send_button.pack(side="right")

        footer = ttk.Frame(shell, style="Card.TFrame")
        footer.pack(fill="x", padx=6, pady=(8, 0))
        ttk.Label(footer, textvariable=self.status_var, style="Soft.TLabel").pack(anchor="w")

    def _bind_events(self) -> None:
        self.provider_combo.bind("<<ComboboxSelected>>", self._on_provider_changed)
        self.input_text.bind("<Return>", self._handle_enter)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_provider_changed(self, _event: object | None = None) -> None:
        provider = self.provider_var.get().strip().lower()
        if provider == "ollama":
            self.model_var.set(DEFAULT_OLLAMA_MODEL)
        else:
            self.model_var.set(DEFAULT_CLAUDE_MODEL)

    def _handle_enter(self, event: tk.Event) -> str | None:
        if event.state & 0x0001:  # Shift+Enter inserts newline
            return None
        self._send_message()
        return "break"

    def _set_busy_state(self, busy: bool) -> None:
        self.is_running = busy
        state = "disabled" if busy else "normal"
        self.send_button.configure(state=state)
        self.input_text.configure(state=state)
        self.provider_combo.configure(state="disabled" if busy else "readonly")
        self.model_entry.configure(state=state)
        self.reset_button.configure(state=state)

    def _append_message(self, author: str, text: str, kind: str) -> None:
        cleaned = (text or "").strip()
        if not cleaned:
            return
        self.transcript.configure(state="normal")
        self.transcript.insert("end", f"{author}\n", ("label", kind))
        self.transcript.insert("end", f"{cleaned}\n\n", (kind,))
        self.transcript.configure(state="disabled")
        self.transcript.see("end")

    def _selected_model(self) -> str:
        return build_model_name(self.provider_var.get(), self.model_var.get())

    def _ensure_loop(self, model: str) -> None:
        with self.loop_lock:
            if self.current_loop is not None and self.current_model == model:
                return
            warmup_ollama_if_needed(model)
            self.current_loop = create_orchestrated_loop(
                model=model,
                ask_user=self._ask_user_tool,
                display_to_user=self._display_tool,
            )
            self.current_loop.set_worker_event_callback(
                lambda event: self.events.put(("worker_event", event))
            )
            self.current_model = model

    def _reset_session(self) -> None:
        with self.loop_lock:
            self.current_loop = None
            self.current_model = ""
        self._append_message("System", "Started a new session.", "system")
        self.status_var.set("Ready")

    async def _ask_user_tool(self, query: str) -> str:
        signal = threading.Event()
        payload = {"query": query, "signal": signal, "answer": ""}
        self.events.put(("ask_user", payload))
        signal.wait()
        return str(payload["answer"])

    async def _display_tool(self, text: str) -> None:
        self.events.put(("display", text))

    def _send_message(self) -> None:
        if self.is_running:
            return
        text = self.input_text.get("1.0", "end").strip()
        if not text:
            return

        self._append_message("You", text, "user")
        self.input_text.delete("1.0", "end")
        self._set_busy_state(True)
        self.status_var.set("Thinking...")

        thread = threading.Thread(target=self._run_agent, args=(text,), daemon=True)
        thread.start()

    def _run_agent(self, user_text: str) -> None:
        try:
            model = self._selected_model()
            self._ensure_loop(model)

            with self.loop_lock:
                loop = self.current_loop
                if loop is None:
                    raise RuntimeError("Assistant session could not be initialized.")
                loop.add_user_message(user_text)

            asyncio.run(self._consume_events(loop))
            self.events.put(("done", None))
        except Exception as exc:  # noqa: BLE001
            self.events.put(("error", str(exc)))

    async def _consume_events(self, loop_obj: object) -> None:
        async for event in loop_obj.run():
            self.events.put(("agent_event", event))

    def _handle_agent_event(self, event: object) -> None:
        if isinstance(event, EventText):
            author = "Assistant" if event.source == "orchestrator" else event.source.title()
            self._append_message(author, event.text, "assistant")
            self.status_var.set("Responding...")
            return
        if isinstance(event, EventToolUse):
            self._append_message(
                "Working",
                f"{event.source} is using {event.tool.__class__.__name__}.",
                "tool",
            )
            self.status_var.set("Working...")
            return
        if isinstance(event, EventToolResult):
            self._append_message(
                "Result",
                (event.result or "")[:600],
                "tool",
            )
            self.status_var.set("Processing...")

    def _drain_events(self) -> None:
        while True:
            try:
                event_type, payload = self.events.get_nowait()
            except queue.Empty:
                break

            if event_type == "agent_event":
                self._handle_agent_event(payload)
            elif event_type == "worker_event" and isinstance(payload, EventToolUse):
                self.status_var.set(f"{payload.source} is running a tool...")
            elif event_type == "display":
                self._append_message("Assistant", str(payload), "system")
            elif event_type == "ask_user":
                if isinstance(payload, dict):
                    question = str(payload.get("query", "Input needed"))
                    answer = simpledialog.askstring("Input Needed", question, parent=self.root)
                    payload["answer"] = answer or ""
                    signal = payload.get("signal")
                    if isinstance(signal, threading.Event):
                        signal.set()
            elif event_type == "error":
                self._append_message("Error", str(payload), "system")
                messagebox.showerror("Request failed", str(payload), parent=self.root)
                self._set_busy_state(False)
                self.status_var.set("Ready")
            elif event_type == "done":
                self._set_busy_state(False)
                self.status_var.set("Ready")

        self.root.after(100, self._drain_events)

    def _on_close(self) -> None:
        if self.is_running and not messagebox.askyesno(
            "Close window?",
            "A request is still running. Close anyway?",
            parent=self.root,
        ):
            return
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    DesktopAgentApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
