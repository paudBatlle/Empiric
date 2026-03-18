"""
Empiric Assistant – redesigned UI
Design system: clean/airy, Linear-inspired, indigo + cyan accent.
All 12 improvement points implemented.
"""

import asyncio
import queue
import threading
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from agent import EventText, EventToolResult, EventToolUse
from app_runtime import (
    DEFAULT_PROVIDER,
    DEFAULT_CLAUDE_MODEL,
    DEFAULT_OLLAMA_MODEL,
    build_model_name,
    create_orchestrated_loop,
    warmup_ollama_if_needed,
)
from tools import start_python_dev_container


# ── Design Tokens ─────────────────────────────────────────────────────────────
BG          = "#f5f7fb"   # soft gray canvas
CARD        = "#ffffff"   # card surface
CARD2       = "#f9fafb"   # secondary card / tool bg
BORDER      = "#e5e7eb"   # subtle divider
PRIMARY     = "#4f46e5"   # indigo
PRIMARY_HOV = "#4338ca"
ACCENT      = "#06b6d4"   # cyan
SUCCESS     = "#059669"   # tool success green
WARNING     = "#d97706"   # tool warning amber
DANGER      = "#dc2626"   # error

TEXT_PRI    = "#111827"
TEXT_SEC    = "#6b7280"
TEXT_MUTED  = "#9ca3af"

BUBBLE_USER = "#eef2ff"   # light indigo tint
BUBBLE_ASST = "#ffffff"
BUBBLE_TOOL = "#fafaf9"

# ── Typography ────────────────────────────────────────────────────────────────
import sys

def _font(*args):
    """Return best available font tuple. args = (size, *modifiers)"""
    if sys.platform == "darwin":
        face = "Helvetica Neue"
    elif sys.platform == "win32":
        face = "Segoe UI"
    else:
        face = "DejaVu Sans"
    return (face, *args)

F_TITLE   = _font(20, "bold")
F_SUBHEAD = _font(12)
F_LABEL   = _font(11, "bold")
F_BODY    = _font(11)
F_SMALL   = _font(10)
F_TINY    = _font(9)

# ── Thinking Dots Animation ───────────────────────────────────────────────────
DOTS = ["Thinking.", "Thinking..", "Thinking..."]


class DesktopAgentApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Empiric Assistant")
        self.root.geometry("1020x740")
        self.root.minsize(780, 560)
        self.root.configure(bg=BG)

        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.loop_lock = threading.Lock()
        self.current_loop = None
        self.current_model = ""
        self.is_running = False
        self._dot_index = 0
        self._dot_after_id = None

        # Track last-message author for grouping
        self._last_author_kind: str | None = None

        default_provider = DEFAULT_PROVIDER.strip().lower() or "ollama"
        default_model = (
            DEFAULT_OLLAMA_MODEL if default_provider == "ollama" else DEFAULT_CLAUDE_MODEL
        )
        self.provider_var = tk.StringVar(value=default_provider)
        self.model_var = tk.StringVar(value=default_model)

        self._configure_styles()
        self._build_layout()
        self._bind_events()
        self._set_busy_state(False)

        self._add_bubble(
            "assistant",
            "👋 Welcome! Ask me anything in plain language and I'll handle the rest.",
        )

        try:
            start_python_dev_container("python-dev")
        except RuntimeError as exc:
            self._add_system(f"⚠️ Docker tools unavailable: {exc}")

        self.root.after(100, self._drain_events)

    # ── Styles ────────────────────────────────────────────────────────────────

    def _configure_styles(self) -> None:
        s = ttk.Style(self.root)
        try:
            s.theme_use("clam")
        except tk.TclError:
            pass

        # Frames
        s.configure("App.TFrame",       background=BG)
        s.configure("Card.TFrame",      background=CARD,  relief="flat")
        s.configure("Header.TFrame",    background=CARD)
        s.configure("InputBar.TFrame",  background=CARD)
        s.configure("Tool.TFrame",      background=CARD2)
        s.configure("BubbleUser.TFrame",background=BUBBLE_USER)
        s.configure("BubbleAsst.TFrame",background=BUBBLE_ASST)

        # Labels
        s.configure("Title.TLabel",
                    background=CARD, foreground=TEXT_PRI, font=F_TITLE)
        s.configure("Sub.TLabel",
                    background=CARD, foreground=TEXT_SEC, font=F_SUBHEAD)
        s.configure("Author.TLabel",
                    background=BG,   foreground=TEXT_MUTED, font=F_TINY)
        s.configure("UserAuthor.TLabel",
                    background=BG,   foreground=TEXT_MUTED, font=F_TINY)
        s.configure("Muted.TLabel",
                    background=CARD, foreground=TEXT_MUTED, font=F_TINY)
        s.configure("Status.TLabel",
                    background=CARD, foreground=TEXT_MUTED, font=F_TINY)

        # Buttons
        s.configure("Primary.TButton",
                    font=F_LABEL, padding=(18, 9),
                    background=PRIMARY, foreground="#ffffff", relief="flat")
        s.map("Primary.TButton",
              background=[("active", PRIMARY_HOV), ("disabled", "#c7d2fe")],
              foreground=[("disabled", "#ffffff")])
        s.configure("Ghost.TButton",
                    font=F_SMALL, padding=(8, 6),
                    background=CARD, foreground=TEXT_SEC, relief="flat")
        s.map("Ghost.TButton",
              background=[("active", CARD2)])

        # Entry / Combobox
        s.configure("TCombobox",
                    fieldbackground=CARD2, background=CARD2,
                    foreground=TEXT_PRI, arrowcolor=TEXT_SEC)

        # Separator
        s.configure("TSeparator", background=BORDER)

    # ── Layout ────────────────────────────────────────────────────────────────

    def _build_layout(self) -> None:
        # ── Root shell ──
        outer = ttk.Frame(self.root, style="App.TFrame")
        outer.pack(fill="both", expand=True)

        # ── Header bar ──
        self._build_header(outer)

        ttk.Separator(outer, orient="horizontal").pack(fill="x")

        # ── Settings panel (initially hidden) ──
        self._settings_visible = False
        self._settings_frame = self._build_settings_panel(outer)

        # ── Chat scroll area ──
        chat_container = ttk.Frame(outer, style="App.TFrame")
        chat_container.pack(fill="both", expand=True, padx=0, pady=0)

        self._chat_canvas = tk.Canvas(
            chat_container,
            bg=BG,
            highlightthickness=0,
            borderwidth=0,
        )
        scrollbar = ttk.Scrollbar(chat_container, orient="vertical",
                                  command=self._chat_canvas.yview)
        self._chat_canvas.configure(yscrollcommand=scrollbar.set)

        scrollbar.pack(side="right", fill="y")
        self._chat_canvas.pack(side="left", fill="both", expand=True)

        # Inner frame that holds all bubble rows
        self._messages_frame = ttk.Frame(self._chat_canvas, style="App.TFrame")
        self._canvas_window = self._chat_canvas.create_window(
            (0, 0), window=self._messages_frame, anchor="nw"
        )
        self._messages_frame.bind("<Configure>", self._on_frame_resize)
        self._chat_canvas.bind("<Configure>", self._on_canvas_resize)

        # Mouse-wheel scrolling
        self._chat_canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        self._chat_canvas.bind_all("<Button-4>",   self._on_mousewheel)
        self._chat_canvas.bind_all("<Button-5>",   self._on_mousewheel)

        ttk.Separator(outer, orient="horizontal").pack(fill="x")

        # ── Input bar ──
        self._build_input_bar(outer)

    def _build_header(self, parent: ttk.Frame) -> None:
        hdr = ttk.Frame(parent, style="Header.TFrame", padding=(24, 16, 24, 16))
        hdr.pack(fill="x")

        left = ttk.Frame(hdr, style="Header.TFrame")
        left.pack(side="left", fill="y")
        ttk.Label(left, text="Empiric Assistant", style="Title.TLabel").pack(anchor="w")
        ttk.Label(left,
                  text="Plain-language requests → intelligent results",
                  style="Sub.TLabel").pack(anchor="w", pady=(2, 0))

        right = ttk.Frame(hdr, style="Header.TFrame")
        right.pack(side="right", anchor="center")

        self._settings_btn = ttk.Button(
            right, text="⚙  Settings", style="Ghost.TButton",
            command=self._toggle_settings,
        )
        self._settings_btn.pack(side="right", padx=(8, 0))

        self._new_session_btn = ttk.Button(
            right, text="＋  New chat", style="Ghost.TButton",
            command=self._reset_session,
        )
        self._new_session_btn.pack(side="right")

    def _build_settings_panel(self, parent: ttk.Frame) -> ttk.Frame:
        panel = ttk.Frame(parent, style="Card.TFrame", padding=(24, 14))
        # Not packed initially

        row = ttk.Frame(panel, style="Card.TFrame")
        row.pack(fill="x")

        ttk.Label(row, text="Provider", style="Muted.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 6))
        self.provider_combo = ttk.Combobox(
            row, state="readonly", width=10,
            values=("claude", "ollama"),
            textvariable=self.provider_var,
        )
        self.provider_combo.grid(row=1, column=0, padx=(0, 16), sticky="w")

        ttk.Label(row, text="Model", style="Muted.TLabel").grid(
            row=0, column=1, sticky="w")
        self.model_entry = ttk.Entry(row, textvariable=self.model_var, width=36)
        self.model_entry.grid(row=1, column=1, sticky="we")
        row.grid_columnconfigure(1, weight=1)

        return panel

    def _toggle_settings(self) -> None:
        self._settings_visible = not self._settings_visible
        if self._settings_visible:
            self._settings_frame.pack(fill="x", after=self.root.nametowidget(
                self._settings_frame.master.winfo_children()[1].winfo_name()
            ))
            # Simpler: just pack/forget
            self._settings_frame.pack(fill="x")
            ttk.Separator(self._settings_frame.master,
                           orient="horizontal").pack(fill="x")
        else:
            self._settings_frame.pack_forget()

    def _build_input_bar(self, parent: ttk.Frame) -> None:
        bar = ttk.Frame(parent, style="InputBar.TFrame", padding=(20, 14))
        bar.pack(fill="x")

        # Outer container simulates a rounded input box
        input_outer = tk.Frame(
            bar,
            bg=CARD2,
            highlightbackground=BORDER,
            highlightthickness=1,
        )
        input_outer.pack(fill="x", side="left", expand=True, padx=(0, 12))

        self.input_text = tk.Text(
            input_outer,
            height=3,
            wrap="word",
            font=F_BODY,
            relief="flat",
            borderwidth=0,
            bg=CARD2,
            fg=TEXT_PRI,
            insertbackground=PRIMARY,
            padx=14,
            pady=10,
        )
        self.input_text.pack(fill="both", expand=True)

        self.send_button = ttk.Button(
            bar,
            text="Send  ↑",
            command=self._send_message,
            style="Primary.TButton",
        )
        self.send_button.pack(side="right", anchor="s", pady=(0, 2))

        # Status label below input
        status_row = ttk.Frame(parent, style="InputBar.TFrame", padding=(20, 0, 20, 10))
        status_row.pack(fill="x")
        self._status_label = ttk.Label(status_row, text="", style="Status.TLabel")
        self._status_label.pack(anchor="w")

    # ── Message Bubbles ───────────────────────────────────────────────────────

    def _add_bubble(self, kind: str, text: str) -> None:
        """Render a chat bubble. kind ∈ {user, assistant}"""
        text = (text or "").strip()
        if not text:
            return

        show_author = self._last_author_kind != kind
        self._last_author_kind = kind

        is_user = kind == "user"

        # Outer row – full width, align content left or right
        row = ttk.Frame(self._messages_frame, style="App.TFrame")
        row.pack(fill="x", padx=20, pady=(6 if show_author else 1, 0))

        # Author label (only on group start)
        if show_author:
            author_name = "You" if is_user else "🤖 Assistant"
            anchor = "e" if is_user else "w"
            author_lbl = tk.Label(
                row,
                text=author_name,
                bg=BG,
                fg=TEXT_MUTED,
                font=F_TINY,
                anchor=anchor,
            )
            author_lbl.pack(fill="x", pady=(0, 2))

        # Bubble container
        bubble_row = ttk.Frame(row, style="App.TFrame")
        bubble_row.pack(fill="x")

        if is_user:
            spacer = ttk.Frame(bubble_row, style="App.TFrame", width=120)
            spacer.pack(side="left")

        bubble = tk.Frame(
            bubble_row,
            bg=BUBBLE_USER if is_user else BUBBLE_ASST,
            highlightbackground="#c7d2fe" if is_user else BORDER,
            highlightthickness=1,
        )
        bubble.pack(
            side="right" if is_user else "left",
            fill="x",
            expand=not is_user,
        )

        lbl = tk.Label(
            bubble,
            text=text,
            wraplength=680,
            justify="left",
            bg=BUBBLE_USER if is_user else BUBBLE_ASST,
            fg=TEXT_PRI,
            font=F_BODY,
            padx=14,
            pady=10,
            anchor="w",
        )
        lbl.pack(fill="x")

        self._scroll_to_bottom()

    def _add_tool_event(self, icon: str, text: str, color: str = TEXT_SEC) -> None:
        """Inline system-log style tool event (compact, muted)."""
        text = (text or "").strip()
        if not text:
            return

        self._last_author_kind = "tool"

        row = ttk.Frame(self._messages_frame, style="App.TFrame")
        row.pack(fill="x", padx=28, pady=1)

        inner = tk.Frame(row, bg=CARD2,
                         highlightbackground=BORDER, highlightthickness=1)
        inner.pack(fill="x")

        tk.Label(
            inner,
            text=f"{icon}  {text}",
            bg=CARD2,
            fg=color,
            font=F_TINY,
            padx=12,
            pady=5,
            anchor="w",
            justify="left",
        ).pack(fill="x")

        self._scroll_to_bottom()

    def _add_system(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        self._last_author_kind = "system"

        row = ttk.Frame(self._messages_frame, style="App.TFrame")
        row.pack(fill="x", padx=28, pady=3)

        tk.Label(
            row,
            text=text,
            bg=BG,
            fg=TEXT_MUTED,
            font=F_TINY,
            anchor="center",
            justify="center",
        ).pack()

        self._scroll_to_bottom()

    # ── Thinking animation ────────────────────────────────────────────────────

    def _start_thinking_dots(self) -> None:
        self._dot_index = 0
        self._tick_dots()

    def _tick_dots(self) -> None:
        if not self.is_running:
            self._status_label.configure(text="")
            return
        self._status_label.configure(text=DOTS[self._dot_index % len(DOTS)])
        self._dot_index += 1
        self._dot_after_id = self.root.after(500, self._tick_dots)

    def _stop_thinking_dots(self) -> None:
        if self._dot_after_id:
            self.root.after_cancel(self._dot_after_id)
            self._dot_after_id = None
        self._status_label.configure(text="")

    # ── Canvas resize helpers ─────────────────────────────────────────────────

    def _on_frame_resize(self, _e=None) -> None:
        self._chat_canvas.configure(
            scrollregion=self._chat_canvas.bbox("all"))

    def _on_canvas_resize(self, event) -> None:
        self._chat_canvas.itemconfig(self._canvas_window, width=event.width)

    def _on_mousewheel(self, event) -> None:
        if event.num == 4:
            self._chat_canvas.yview_scroll(-1, "units")
        elif event.num == 5:
            self._chat_canvas.yview_scroll(1, "units")
        else:
            self._chat_canvas.yview_scroll(int(-event.delta / 40), "units")

    def _scroll_to_bottom(self) -> None:
        self.root.update_idletasks()
        self._chat_canvas.configure(
            scrollregion=self._chat_canvas.bbox("all"))
        self._chat_canvas.yview_moveto(1.0)

    # ── Events / bindings ─────────────────────────────────────────────────────

    def _bind_events(self) -> None:
        self.provider_combo.bind("<<ComboboxSelected>>", self._on_provider_changed)
        self.input_text.bind("<Return>", self._handle_enter)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_provider_changed(self, _event=None) -> None:
        provider = self.provider_var.get().strip().lower()
        self.model_var.set(
            DEFAULT_OLLAMA_MODEL if provider == "ollama" else DEFAULT_CLAUDE_MODEL
        )

    def _handle_enter(self, event: tk.Event):
        if event.state & 0x0001:
            return None
        self._send_message()
        return "break"

    def _set_busy_state(self, busy: bool) -> None:
        self.is_running = busy
        state = "disabled" if busy else "normal"
        self.send_button.configure(state=state)
        self.input_text.configure(state=state)
        if hasattr(self, "provider_combo"):
            self.provider_combo.configure(state="disabled" if busy else "readonly")
        if hasattr(self, "model_entry"):
            self.model_entry.configure(state=state)
        if hasattr(self, "_new_session_btn"):
            self._new_session_btn.configure(state=state)

    # ── Model / session ───────────────────────────────────────────────────────

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
        self._add_system("── New session started ──")

    # ── Tools (async, called from agent thread) ───────────────────────────────

    async def _ask_user_tool(self, query: str) -> str:
        signal = threading.Event()
        payload = {"query": query, "signal": signal, "answer": ""}
        self.events.put(("ask_user", payload))
        signal.wait()
        return str(payload["answer"])

    async def _display_tool(self, text: str) -> None:
        self.events.put(("display", text))

    # ── Send / run ────────────────────────────────────────────────────────────

    def _send_message(self) -> None:
        if self.is_running:
            return
        text = self.input_text.get("1.0", "end").strip()
        if not text:
            return
        self._add_bubble("user", text)
        self.input_text.delete("1.0", "end")
        self._set_busy_state(True)
        self._start_thinking_dots()
        threading.Thread(target=self._run_agent, args=(text,), daemon=True).start()

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

    async def _consume_events(self, loop_obj) -> None:
        async for event in loop_obj.run():
            self.events.put(("agent_event", event))

    # ── Event dispatch ────────────────────────────────────────────────────────

    def _handle_agent_event(self, event) -> None:
        if isinstance(event, EventText):
            self._stop_thinking_dots()
            author = "assistant" if event.source == "orchestrator" else event.source
            self._add_bubble(author if author == "assistant" else "assistant", event.text)
            return
        if isinstance(event, EventToolUse):
            tool_name = event.tool.__class__.__name__
            self._add_tool_event("⚙️", f"Running {tool_name}…", WARNING)
            return
        if isinstance(event, EventToolResult):
            snippet = (event.result or "")[:300]
            self._add_tool_event("✅", snippet, SUCCESS)

    def _drain_events(self) -> None:
        while True:
            try:
                event_type, payload = self.events.get_nowait()
            except queue.Empty:
                break

            if event_type == "agent_event":
                self._handle_agent_event(payload)
            elif event_type == "worker_event" and isinstance(payload, EventToolUse):
                tool_name = payload.tool.__class__.__name__
                self._add_tool_event("⚙️", f"{payload.source} → {tool_name}", WARNING)
            elif event_type == "display":
                self._add_bubble("assistant", str(payload))
            elif event_type == "ask_user":
                if isinstance(payload, dict):
                    question = str(payload.get("query", "Input needed"))
                    answer = simpledialog.askstring("Input Needed", question, parent=self.root)
                    payload["answer"] = answer or ""
                    sig = payload.get("signal")
                    if isinstance(sig, threading.Event):
                        sig.set()
            elif event_type == "error":
                self._stop_thinking_dots()
                self._add_tool_event("❗", str(payload), DANGER)
                messagebox.showerror("Request failed", str(payload), parent=self.root)
                self._set_busy_state(False)
            elif event_type == "done":
                self._stop_thinking_dots()
                self._set_busy_state(False)

        self.root.after(100, self._drain_events)

    # ── Close ──────────────────────────────────────────────────────────────────

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