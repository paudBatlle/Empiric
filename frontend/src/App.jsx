import { useState, useEffect, useRef, useCallback } from "react";

/* ── WebSocket hook ──────────────────────────────────────────────────────── */
function useAgent() {
  const ws = useRef(null);
  const reconnectTimer = useRef(null);
  const reconnectAttempt = useRef(0);
  const shouldReconnect = useRef(true);
  const [connected, setConnected] = useState(false);
  const [messages, setMessages] = useState([]);
  const [thinking, setThinking] = useState(false);
  const [askQuery, setAskQuery] = useState(null);

  const push = useCallback(
    (msg) => setMessages((prev) => [...prev, { id: Date.now() + Math.random(), ...msg }]),
    []
  );

  // Attach a tool_result to the most recent pending tool_use
  const attachResult = useCallback(
    (result) =>
      setMessages((prev) => {
        const copy = [...prev];
        for (let i = copy.length - 1; i >= 0; i--) {
          if (copy[i].role === "tool_use" && copy[i].result === undefined) {
            copy[i] = { ...copy[i], result, resultTs: new Date() };
            return copy;
          }
        }
        return [...prev, { id: Date.now() + Math.random(), role: "tool_result", result }];
      }),
    []
  );

  const clearReconnectTimer = useCallback(() => {
    if (reconnectTimer.current) {
      clearTimeout(reconnectTimer.current);
      reconnectTimer.current = null;
    }
  }, []);

  const connect = useCallback(() => {
    if (ws.current && (ws.current.readyState === WebSocket.OPEN || ws.current.readyState === WebSocket.CONNECTING)) {
      return;
    }

    clearReconnectTimer();
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const socket = new WebSocket(`${proto}://${location.host}/ws`);
    ws.current = socket;
    socket.onopen = () => {
      reconnectAttempt.current = 0;
      setConnected(true);
    };
    socket.onclose = () => {
      ws.current = null;
      setConnected(false);
      setThinking(false);

      if (!shouldReconnect.current) return;
      const delayMs = Math.min(1000 * 2 ** reconnectAttempt.current, 10000);
      reconnectAttempt.current += 1;
      reconnectTimer.current = setTimeout(() => {
        connect();
      }, delayMs);
    };
    socket.onerror = () => socket.close();
    socket.onmessage = (e) => {
      let msg;
      try {
        msg = JSON.parse(e.data);
      } catch {
        return;
      }
      switch (msg.type) {
        case "session_ready":
          break;
        case "text":
          setThinking(false);
          push({ role: "assistant", text: msg.text, source: msg.source });
          break;
        case "tool_use":
          push({ role: "tool_use", source: msg.source, tool: msg.tool, ts: new Date() });
          break;
        case "tool_result":
          attachResult(msg.result);
          break;
        case "display":
          push({ role: "assistant", text: msg.text });
          break;
        case "ask_user":
          setAskQuery(msg.query);
          break;
        case "system":
          push({ role: "system", text: msg.text });
          break;
        case "done":
          setThinking(false);
          break;
      }
    };
  }, [push, attachResult, clearReconnectTimer]);

  useEffect(() => {
    shouldReconnect.current = true;
    connect();
    return () => {
      shouldReconnect.current = false;
      clearReconnectTimer();
      ws.current?.close();
      ws.current = null;
    };
  }, [connect, clearReconnectTimer]);

  const sendPacket = useCallback((packet) => {
    if (!ws.current || ws.current.readyState !== WebSocket.OPEN) return false;
    ws.current.send(JSON.stringify(packet));
    return true;
  }, []);

  const startSession = useCallback(
    (provider, model) => {
      setMessages([]);
      sendPacket({ type: "new_session", provider, model });
    },
    [sendPacket]
  );

  const sendMessage = useCallback(
    (text) => {
      if (!text.trim()) return;
      push({ role: "user", text });
      setThinking(true);
      sendPacket({ type: "user_message", text });
    },
    [push, sendPacket]
  );

  const answerAsk = useCallback(
    (answer) => {
      setAskQuery(null);
      sendPacket({ type: "answer", answer });
    },
    [sendPacket]
  );

  return { connected, messages, thinking, askQuery, startSession, sendMessage, answerAsk };
}

/* ── Thinking dots ───────────────────────────────────────────────────────── */
function ThinkingDots() {
  const [frame, setFrame] = useState(0);
  const frames = ["Thinking", "Thinking·", "Thinking··", "Thinking···"];
  useEffect(() => {
    const t = setInterval(() => setFrame((f) => (f + 1) % frames.length), 420);
    return () => clearInterval(t);
  }, []);
  return (
    <div
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 10,
        padding: "12px 18px",
        background: "#fff",
        border: "1px solid #ddd8ce",
        borderRadius: "16px 16px 16px 4px",
        animation: "fadeSlideUp 0.3s ease",
      }}
    >
      <span
        style={{
          fontSize: 13,
          color: "#9c9c91",
          fontStyle: "italic",
          fontFamily: "'Lora', Georgia, serif",
        }}
      >
        {frames[frame]}
      </span>
      <span
        style={{
          width: 7,
          height: 7,
          borderRadius: "50%",
          background: "#c4622d",
          animation: "empulse 1.2s ease infinite",
          display: "inline-block",
        }}
      />
    </div>
  );
}

/* ── Tool card – inline in the chat stream ───────────────────────────────── */
function ToolCard({ msg }) {
  const [expanded, setExpanded] = useState(false);
  const hasResult = msg.result !== undefined;
  const ts = msg.ts instanceof Date ? msg.ts : new Date(msg.ts || Date.now());

  return (
    <div
      className="anim-in"
      style={{
        margin: "16px 0",
        fontFamily: "'Source Sans 3','Helvetica Neue',sans-serif",
      }}
    >
      {/* ── Header row ── */}
      <div
        style={{
          display: "flex",
          alignItems: "stretch",
          borderRadius: hasResult && expanded ? "10px 10px 0 0" : "10px",
          border: "1px solid #2a3650",
          overflow: "hidden",
          background: "#1b2333",
          transition: "border-radius 0.15s",
        }}
      >
        {/* Left accent bar */}
        <div
          style={{
            width: 4,
            flexShrink: 0,
            background: hasResult ? "#3d9e6e" : "#4a8cca",
            transition: "background 0.3s",
          }}
        />

        {/* Main content */}
        <div
          style={{
            flex: 1,
            padding: "10px 14px",
            display: "flex",
            alignItems: "center",
            gap: 8,
            minWidth: 0,
            flexWrap: "wrap",
          }}
        >
          {/* Spin / check icon */}
          <span
            style={{
              fontSize: 13,
              color: hasResult ? "#3d9e6e" : "#4a8cca",
              flexShrink: 0,
              fontWeight: 600,
              display: "inline-block",
              animation: hasResult ? "none" : "emspin 1.4s linear infinite",
            }}
          >
            {hasResult ? "✓" : "◌"}
          </span>

          {/* Source badge */}
          <span
            style={{
              fontSize: 11,
              fontWeight: 600,
              letterSpacing: "0.04em",
              background: "#243050",
              color: "#7aaad4",
              padding: "2px 9px",
              borderRadius: 4,
              border: "1px solid #2d3f60",
              flexShrink: 0,
            }}
          >
            {msg.source || "agent"}
          </span>

          <span style={{ color: "#3a4a62", fontSize: 12, flexShrink: 0 }}>→</span>

          {/* Tool badge */}
          <span
            style={{
              fontSize: 12,
              fontWeight: 600,
              color: "#e2c97e",
              background: "#251f0d",
              padding: "2px 9px",
              borderRadius: 4,
              border: "1px solid #3d3318",
              fontFamily: "'Courier New', monospace",
              flexShrink: 0,
            }}
          >
            {msg.tool || "tool"}
          </span>

          <div style={{ flex: 1, minWidth: 8 }} />

          {/* Timestamp */}
          <span style={{ fontSize: 10, color: "#3a4a60", flexShrink: 0 }}>
            {ts.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
          </span>

          {/* Status pill */}
          {hasResult ? (
            <span
              style={{
                fontSize: 10,
                fontWeight: 700,
                letterSpacing: "0.05em",
                background: "#0d2a1e",
                color: "#3daf72",
                padding: "2px 8px",
                borderRadius: 4,
                flexShrink: 0,
              }}
            >
              DONE
            </span>
          ) : (
            <span
              style={{
                fontSize: 10,
                fontWeight: 700,
                letterSpacing: "0.05em",
                background: "#251900",
                color: "#c4922d",
                padding: "2px 8px",
                borderRadius: 4,
                flexShrink: 0,
              }}
            >
              RUNNING
            </span>
          )}
        </div>

        {/* Expand toggle */}
        {hasResult && (
          <button
            onClick={() => setExpanded((e) => !e)}
            title={expanded ? "Collapse output" : "Expand output"}
            style={{
              background: "#151d2e",
              border: "none",
              borderLeft: "1px solid #2a3650",
              color: expanded ? "#7aaad4" : "#4a6080",
              cursor: "pointer",
              padding: "0 16px",
              fontSize: 11,
              flexShrink: 0,
              transition: "color 0.15s, background 0.15s",
              fontFamily: "'Source Sans 3','Helvetica Neue',sans-serif",
            }}
          >
            {expanded ? "▴ hide" : "▾ output"}
          </button>
        )}
      </div>

      {/* ── Output panel ── */}
      {hasResult && expanded && (
        <div
          style={{
            background: "#10151f",
            border: "1px solid #2a3650",
            borderTop: "1px solid #1e2840",
            borderRadius: "0 0 10px 10px",
            padding: "14px 18px",
            animation: "fadeSlideUp 0.2s ease",
            maxHeight: 280,
            overflowY: "auto",
          }}
        >
          <div
            style={{
              fontSize: 10,
              fontWeight: 700,
              color: "#2e4060",
              letterSpacing: "0.08em",
              textTransform: "uppercase",
              marginBottom: 10,
            }}
          >
            Output
          </div>
          <pre
            style={{
              margin: 0,
              fontSize: 12,
              lineHeight: 1.75,
              color: "#9ab5cc",
              fontFamily: "'Courier New', Courier, monospace",
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
            }}
          >
            {(msg.result || "(empty)").trim()}
          </pre>
        </div>
      )}
    </div>
  );
}

/* ── Chat bubble ─────────────────────────────────────────────────────────── */
function Bubble({ msg, prevMsg }) {
  const isUser = msg.role === "user";
  const isSystem = msg.role === "system";
  const showMeta = prevMsg?.role !== msg.role;

  if (isSystem)
    return (
      <div className="anim-in" style={{ textAlign: "center", padding: "10px 0" }}>
        <span
          style={{
            fontSize: 11,
            color: "#9c9c91",
            background: "#ede8de",
            padding: "3px 14px",
            borderRadius: 99,
            fontFamily: "'Source Sans 3','Helvetica Neue',sans-serif",
          }}
        >
          {msg.text}
        </span>
      </div>
    );

  return (
    <div
      className="anim-in"
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: isUser ? "flex-end" : "flex-start",
        marginTop: showMeta ? 18 : 3,
      }}
    >
      {showMeta && (
        <div
          style={{
            fontSize: 10,
            fontWeight: 600,
            color: "#9c9c91",
            marginBottom: 5,
            paddingLeft: isUser ? 0 : 4,
            paddingRight: isUser ? 4 : 0,
            fontFamily: "'Source Sans 3','Helvetica Neue',sans-serif",
            letterSpacing: "0.06em",
            textTransform: "uppercase",
          }}
        >
          {isUser ? "You" : "✦ Assistant"}
        </div>
      )}
      <div
        style={{
          maxWidth: "70%",
          background: isUser ? "#1a1a18" : "#ffffff",
          color: isUser ? "#faf8f4" : "#1a1a18",
          padding: "12px 17px",
          borderRadius: isUser ? "16px 16px 4px 16px" : "16px 16px 16px 4px",
          border: isUser ? "none" : "1px solid #ddd8ce",
          fontSize: 15,
          lineHeight: 1.65,
          fontFamily: "'Source Sans 3','Helvetica Neue',sans-serif",
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
        }}
      >
        {msg.text}
      </div>
    </div>
  );
}

/* ── Shared button styles ────────────────────────────────────────────────── */
const inputSt = {
  width: "100%",
  padding: "10px 14px",
  borderRadius: 8,
  border: "1px solid #c8c2b5",
  background: "#fff",
  color: "#1a1a18",
  fontSize: 14,
  outline: "none",
  boxSizing: "border-box",
  fontFamily: "'Source Sans 3','Helvetica Neue',sans-serif",
};
const primarySt = {
  padding: "10px 22px",
  background: "#c4622d",
  color: "#fff",
  border: "none",
  borderRadius: 8,
  fontSize: 14,
  fontWeight: 600,
  fontFamily: "'Source Sans 3','Helvetica Neue',sans-serif",
  cursor: "pointer",
};
const ghostSt = {
  padding: "9px 16px",
  background: "transparent",
  color: "#6b6b63",
  border: "1px solid #ddd8ce",
  borderRadius: 8,
  fontSize: 13,
  fontFamily: "'Source Sans 3','Helvetica Neue',sans-serif",
  cursor: "pointer",
};
const labelSt = {
  display: "block",
  fontSize: 11,
  fontWeight: 600,
  color: "#6b6b63",
  letterSpacing: "0.07em",
  textTransform: "uppercase",
  marginBottom: 6,
  fontFamily: "'Source Sans 3','Helvetica Neue',sans-serif",
};
const sideLabel = {
  fontSize: 10,
  fontWeight: 600,
  color: "#9c9c91",
  letterSpacing: "0.07em",
  textTransform: "uppercase",
  marginBottom: 8,
};

/* ── Settings modal ──────────────────────────────────────────────────────── */
function SettingsPanel({ provider, setProvider, model, setModel, onApply, onClose }) {
  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(26,26,24,0.45)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 100,
        backdropFilter: "blur(4px)",
        animation: "fadeIn 0.2s ease",
      }}
    >
      <div
        style={{
          background: "#faf8f4",
          border: "1px solid #ddd8ce",
          borderRadius: 20,
          padding: "36px 40px",
          width: 420,
          boxShadow: "0 20px 60px rgba(26,26,24,0.15)",
          animation: "fadeSlideUp 0.3s cubic-bezier(0.22,1,0.36,1)",
        }}
      >
        <h2
          style={{
            fontFamily: "'Lora',Georgia,serif",
            fontSize: 22,
            color: "#1a1a18",
            marginBottom: 6,
          }}
        >
          Settings
        </h2>
        <p style={{ fontSize: 13, color: "#6b6b63", marginBottom: 28 }}>
          Configure the model provider for this session.
        </p>
        <label style={labelSt}>Provider</label>
        <select value={provider} onChange={(e) => setProvider(e.target.value)} style={inputSt}>
          <option value="ollama">Ollama (local)</option>
          <option value="claude">Anthropic Claude</option>
        </select>
        <label style={{ ...labelSt, marginTop: 18 }}>Model</label>
        <input
          value={model}
          onChange={(e) => setModel(e.target.value)}
          style={inputSt}
          placeholder="e.g. qwen3.5:4b, claude-3-5-sonnet-latest"
        />
        <div style={{ display: "flex", gap: 10, marginTop: 32 }}>
          <button onClick={onApply} style={primarySt}>
            Apply & restart
          </button>
          <button onClick={onClose} style={ghostSt}>
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}

/* ── Ask-user modal ──────────────────────────────────────────────────────── */
function AskModal({ query, onAnswer }) {
  const [val, setVal] = useState("");
  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(26,26,24,0.45)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 100,
        backdropFilter: "blur(4px)",
      }}
    >
      <div
        style={{
          background: "#faf8f4",
          border: "1px solid #ddd8ce",
          borderRadius: 20,
          padding: "36px 40px",
          width: 420,
          boxShadow: "0 20px 60px rgba(26,26,24,0.15)",
          animation: "fadeSlideUp 0.3s cubic-bezier(0.22,1,0.36,1)",
        }}
      >
        <h2
          style={{
            fontFamily: "'Lora',Georgia,serif",
            fontSize: 20,
            color: "#1a1a18",
            marginBottom: 12,
          }}
        >
          Input needed
        </h2>
        <p style={{ fontSize: 15, color: "#3d3d38", marginBottom: 24, lineHeight: 1.6 }}>{query}</p>
        <input
          value={val}
          onChange={(e) => setVal(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && onAnswer(val)}
          autoFocus
          style={inputSt}
          placeholder="Your answer..."
        />
        <div style={{ marginTop: 20 }}>
          <button onClick={() => onAnswer(val)} style={primarySt}>
            Submit
          </button>
        </div>
      </div>
    </div>
  );
}

/* ── Sidebar ─────────────────────────────────────────────────────────────── */
function Sidebar({ messages, sessionCount, onNewSession }) {
  const [open, setOpen] = useState(true);
  const toolEvents = messages.filter((m) => m.role === "tool_use");
  const toolCounts = toolEvents.reduce((acc, e) => {
    acc[e.tool] = (acc[e.tool] || 0) + 1;
    return acc;
  }, {});

  return (
    <aside
      style={{
        width: open ? 236 : 44,
        minWidth: open ? 236 : 44,
        background: "#f2ede4",
        borderRight: "1px solid #ddd8ce",
        display: "flex",
        flexDirection: "column",
        transition: "width 0.25s cubic-bezier(0.22,1,0.36,1), min-width 0.25s",
        overflow: "hidden",
        flexShrink: 0,
      }}
    >
      <button
        onClick={() => setOpen((o) => !o)}
        style={{
          background: "none",
          border: "none",
          cursor: "pointer",
          padding: "14px",
          alignSelf: open ? "flex-end" : "center",
          color: "#9c9c91",
          fontSize: 14,
          flexShrink: 0,
        }}
      >
        {open ? "‹" : "›"}
      </button>

      {open && (
        <>
          <div style={{ padding: "0 18px 18px", borderBottom: "1px solid #ddd8ce" }}>
            <h1
              style={{
                fontFamily: "'Lora',Georgia,serif",
                fontSize: 17,
                fontWeight: 600,
                color: "#1a1a18",
                lineHeight: 1.25,
              }}
            >
              Empiric
              <br />
              <span style={{ color: "#c4622d" }}>Assistant</span>
            </h1>
            <p
              style={{
                fontSize: 11,
                color: "#9c9c91",
                marginTop: 4,
                fontFamily: "'Source Sans 3','Helvetica Neue',sans-serif",
              }}
            >
              Plain language → results
            </p>
          </div>

          <div style={{ padding: "14px 18px", borderBottom: "1px solid #ddd8ce" }}>
            <div style={sideLabel}>Sessions</div>
            {Array.from({ length: sessionCount }, (_, i) => (
              <div
                key={i}
                style={{
                  fontSize: 12,
                  padding: "4px 8px",
                  borderRadius: 5,
                  marginBottom: 2,
                  color: i === sessionCount - 1 ? "#c4622d" : "#6b6b63",
                  background: i === sessionCount - 1 ? "#f5e6dc" : "transparent",
                  fontFamily: "'Source Sans 3','Helvetica Neue',sans-serif",
                }}
              >
                {i === sessionCount - 1 ? "● " : "○ "}Session {i + 1}
              </div>
            ))}
            <button
              onClick={onNewSession}
              style={{
                marginTop: 8,
                width: "100%",
                padding: "6px 0",
                background: "#faf8f4",
                border: "1px dashed #c8c2b5",
                borderRadius: 6,
                fontSize: 12,
                color: "#6b6b63",
                cursor: "pointer",
                fontFamily: "'Source Sans 3','Helvetica Neue',sans-serif",
              }}
            >
              + New session
            </button>
          </div>

          <div style={{ padding: "14px 18px", flex: 1, overflow: "hidden" }}>
            <div style={sideLabel}>Tool calls ({toolEvents.length})</div>
            {toolEvents.length === 0 ? (
              <p
                style={{
                  fontSize: 11,
                  color: "#9c9c91",
                  fontStyle: "italic",
                  fontFamily: "'Source Sans 3','Helvetica Neue',sans-serif",
                }}
              >
                None yet — detail appears inline in chat.
              </p>
            ) : (
              Object.entries(toolCounts).map(([tool, count]) => (
                <div
                  key={tool}
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    fontSize: 11,
                    background: "#1b2333",
                    borderRadius: 4,
                    padding: "4px 9px",
                    marginBottom: 4,
                    border: "1px solid #2a3650",
                  }}
                >
                  <span style={{ color: "#e2c97e", fontFamily: "'Courier New',monospace" }}>
                    {tool}
                  </span>
                  <span style={{ color: "#4a6080", fontFamily: "'Source Sans 3',sans-serif" }}>
                    ×{count}
                  </span>
                </div>
              ))
            )}
          </div>
        </>
      )}
    </aside>
  );
}

/* ── Global keyframes ────────────────────────────────────────────────────── */
const STYLES = `
  @keyframes fadeSlideUp {
    from { opacity:0; transform:translateY(8px); }
    to   { opacity:1; transform:translateY(0); }
  }
  @keyframes fadeIn {
    from { opacity:0; } to { opacity:1; }
  }
  @keyframes empulse {
    0%,100% { opacity:1; transform:scale(1); }
    50%      { opacity:.4; transform:scale(.85); }
  }
  @keyframes emspin {
    from { transform:rotate(0deg); }
    to   { transform:rotate(360deg); }
  }
  .anim-in { animation: fadeSlideUp 0.3s cubic-bezier(0.22,1,0.36,1) both; }
  .composer-input::placeholder {
    color: #a7a095;
  }
`;

/* ── App ─────────────────────────────────────────────────────────────────── */
export default function App() {
  const { connected, messages, thinking, askQuery, startSession, sendMessage, answerAsk } = useAgent();

  const [provider, setProvider] = useState("ollama");
  const [model, setModel] = useState("qwen3.5:4b");
  const [showSettings, setShowSettings] = useState(false);
  const [sessionCount, setSessionCount] = useState(0);
  const [input, setInput] = useState("");
  const bottomRef = useRef(null);
  const textareaRef = useRef(null);

  useEffect(() => {
    const loadDefaults = async () => {
      try {
        const resp = await fetch("/api/default-model");
        if (!resp.ok) return;
        const data = await resp.json();
        if (typeof data.provider === "string") setProvider(data.provider);
        if (typeof data.model === "string") setModel(data.model || "qwen3.5:4b");
      } catch {
        // Keep local defaults when backend metadata is unavailable.
      }
    };
    loadDefaults();
  }, []);

  useEffect(() => {
    if (connected && sessionCount === 0) {
      startSession(provider, model);
      setSessionCount(1);
    }
  }, [connected, sessionCount, provider, model, startSession]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, thinking]);

  const handleSend = () => {
    if (!input.trim() || thinking) return;
    sendMessage(input.trim());
    setInput("");
    textareaRef.current?.focus();
  };

  const handleNewSession = () => {
    startSession(provider, model);
    setSessionCount((c) => c + 1);
  };

  return (
    <>
      <style>{STYLES}</style>
      <div style={{ display: "flex", height: "100vh", overflow: "hidden", background: "#faf8f4" }}>
        <Sidebar messages={messages} sessionCount={sessionCount} onNewSession={handleNewSession} />

        <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
          {/* Topbar */}
          <header
            style={{
              padding: "12px 24px",
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              borderBottom: "1px solid #ddd8ce",
              background: "#faf8f4",
              flexShrink: 0,
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <div
                style={{
                  width: 7,
                  height: 7,
                  borderRadius: "50%",
                  background: connected ? "#5a7a5e" : "#c4622d",
                  boxShadow: connected
                    ? "0 0 0 3px rgba(90,122,94,0.2)"
                    : "0 0 0 3px rgba(196,98,45,0.2)",
                  transition: "all 0.3s",
                }}
              />
              <span
                style={{
                  fontSize: 12,
                  color: "#9c9c91",
                  fontFamily: "'Source Sans 3','Helvetica Neue',sans-serif",
                }}
              >
                {connected ? "Connected" : "Connecting..."}
              </span>
            </div>
            <button onClick={() => setShowSettings(true)} style={{ ...ghostSt, fontSize: 12 }}>
              ⚙ Settings
            </button>
          </header>

          {/* Chat */}
          <div style={{ flex: 1, overflowY: "auto", padding: "28px 52px 20px" }}>
            {messages.length === 0 && !thinking && (
              <div
                style={{
                  textAlign: "center",
                  marginTop: "18vh",
                  animation: "fadeIn 0.5s ease",
                  fontFamily: "'Source Sans 3','Helvetica Neue',sans-serif",
                }}
              >
                <div style={{ fontSize: 34, marginBottom: 14 }}>✦</div>
                <h2
                  style={{
                    fontFamily: "'Lora',Georgia,serif",
                    fontSize: 24,
                    fontWeight: 500,
                    color: "#1a1a18",
                    marginBottom: 10,
                  }}
                >
                  What can I help with?
                </h2>
                <p
                  style={{
                    fontSize: 14,
                    color: "#6b6b63",
                    maxWidth: 340,
                    margin: "0 auto",
                    lineHeight: 1.7,
                  }}
                >
                  Type your request in plain language — I'll handle the technical steps.
                </p>
              </div>
            )}

            {messages.map((msg, i) => {
              if (msg.role === "tool_use") return <ToolCard key={msg.id} msg={msg} />;
              if (msg.role === "tool_result") return null;
              return <Bubble key={msg.id} msg={msg} prevMsg={messages[i - 1]} />;
            })}

            {thinking && (
              <div className="anim-in" style={{ marginTop: 12 }}>
                <ThinkingDots />
              </div>
            )}
            <div ref={bottomRef} />
          </div>

          {/* Input bar */}
          <div
            style={{
              padding: "14px 24px 18px",
              background: "#faf8f4",
              borderTop: "1px solid #ddd8ce",
              flexShrink: 0,
            }}
          >
            <div
              style={{
                display: "flex",
                gap: 10,
                alignItems: "center",
                background: "#fff",
                border: "1.5px solid #c8c2b5",
                borderRadius: 20,
                padding: "10px 14px",
                boxShadow: "0 1px 4px rgba(26,26,24,0.06)",
              }}
            >
              <textarea
                ref={textareaRef}
                className="composer-input"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    handleSend();
                  }
                }}
                rows={1}
                placeholder="Ask anything..."
                disabled={thinking}
                style={{
                  flex: 1,
                  resize: "none",
                  border: "none",
                  outline: "none",
                  background: "transparent",
                  color: "#1a1a18",
                  fontSize: 15,
                  fontFamily: "'Source Sans 3','Helvetica Neue',sans-serif",
                  lineHeight: 1.6,
                  minHeight: 24,
                  padding: "6px 0",
                  display: "block",
                  maxHeight: 160,
                  overflowY: "auto",
                }}
                onInput={(e) => {
                  e.target.style.height = "auto";
                  e.target.style.height = `${Math.min(e.target.scrollHeight, 160)}px`;
                }}
              />
              <button
                onClick={handleSend}
                disabled={!connected || thinking || !input.trim()}
                style={{
                  width: 36,
                  height: 36,
                  borderRadius: "50%",
                  flexShrink: 0,
                  background: input.trim() && !thinking ? "#c4622d" : "#ede8de",
                  border: "none",
                  cursor: input.trim() && !thinking ? "pointer" : "default",
                  color: input.trim() && !thinking ? "#fff" : "#9c9c91",
                  fontSize: 16,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  transition: "background 0.2s, color 0.2s",
                }}
              >
                ↑
              </button>
            </div>
            <p
              style={{
                fontSize: 10,
                color: "#b0a99e",
                marginTop: 6,
                paddingLeft: 4,
                fontFamily: "'Source Sans 3','Helvetica Neue',sans-serif",
              }}
            >
              Enter to send · Shift+Enter for new line
            </p>
          </div>
        </div>

        {showSettings && (
          <SettingsPanel
            provider={provider}
            setProvider={setProvider}
            model={model}
            setModel={setModel}
            onApply={() => {
              setShowSettings(false);
              handleNewSession();
            }}
            onClose={() => setShowSettings(false)}
          />
        )}
        {askQuery && <AskModal query={askQuery} onAnswer={answerAsk} />}
      </div>
    </>
  );
}
