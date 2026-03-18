import subprocess
from typing import Awaitable, Callable

from orchestrator import OrchestratedAgentLoop, WorkerConfig
from tools import (
    ToolRunCommandInDevContainer,
    ToolUpsertFile,
    create_tool_ask_user,
    create_tool_display_to_user,
)

DEFAULT_PROVIDER = "claude"
DEFAULT_CLAUDE_MODEL = "claude-3-5-sonnet-latest"
DEFAULT_OLLAMA_MODEL = "llama3.1"

ORCHESTRATOR_SYSTEM_PROMPT = """
You are the orchestrator of a multi-agent coding system.
Your role is to:
1) Understand the user request.
2) Delegate specific sub-tasks to worker agents with delegation tools.
3) Combine worker outputs into a final clear answer.

Guidelines:
- Break large requests into small tasks before delegation.
- Delegate coding/file writing to coder.
- Delegate command execution/testing to runner.
- Prefer delegating first, then synthesizing results.
- If details are missing, ask the user directly.
- Use ToolDisplayToUser for one-way progress updates.
- Use ToolAskUser only when a user response is required.
- Do not claim a task is done unless a worker confirmed it.
"""

CODER_SYSTEM_PROMPT = """
You are a coding worker.
Focus on implementing code changes and file updates.
Use file editing tools when needed and return concise implementation notes.
"""

RUNNER_SYSTEM_PROMPT = """
You are a command runner worker.
Focus on executing commands, running checks/tests, and reporting outputs.
Do not edit files unless explicitly asked.
"""

PLANNER_SYSTEM_PROMPT = """
You are a planner worker.
Focus on making a detailed plan on how to accomplish the user task.
"""


def build_model_name(provider: str, model_name: str = "") -> str:
    normalized_provider = (provider or DEFAULT_PROVIDER).strip().lower()
    normalized_model = model_name.strip()

    if normalized_provider == "ollama":
        return f"ollama:{normalized_model or DEFAULT_OLLAMA_MODEL}"
    return normalized_model or DEFAULT_CLAUDE_MODEL


def warmup_ollama_if_needed(model: str) -> None:
    if not model.startswith("ollama:"):
        return

    ollama_model = model.split(":", 1)[1]
    print(f"Starting Ollama model with `ollama run {ollama_model}`...")
    try:
        # Run once with a short prompt so Ollama spins up the selected model.
        subprocess.run(
            ["ollama", "run", ollama_model, "reply with ok"],
            check=True,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "Ollama CLI was not found. Install Ollama and make sure `ollama` is in PATH."
        ) from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        raise RuntimeError(
            f"Failed to start Ollama model `{ollama_model}` with `ollama run`: {stderr}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"`ollama run {ollama_model}` timed out while starting the model."
        ) from exc


def create_orchestrated_loop(
    *,
    model: str,
    ask_user: Callable[[str], Awaitable[str]],
    display_to_user: Callable[[str], Awaitable[None]],
) -> OrchestratedAgentLoop:
    ToolAskUser = create_tool_ask_user(ask_user)
    ToolDisplayToUser = create_tool_display_to_user(display_to_user)

    workers = [
        WorkerConfig(
            name="coder",
            description="Can write and edit files.",
            system_prompt=CODER_SYSTEM_PROMPT,
            tools=[ToolUpsertFile, ToolDisplayToUser, ToolAskUser],
        ),
        WorkerConfig(
            name="runner",
            description="Can run shell commands inside the dev container.",
            system_prompt=RUNNER_SYSTEM_PROMPT,
            tools=[ToolRunCommandInDevContainer, ToolDisplayToUser, ToolAskUser],
        ),
        WorkerConfig(
            name="planner",
            description="Makes a plan on how to accomplish the user task.",
            system_prompt=PLANNER_SYSTEM_PROMPT,
            tools=[ToolDisplayToUser, ToolAskUser],
        ),
    ]

    return OrchestratedAgentLoop(
        model=model,
        orchestrator_prompt=ORCHESTRATOR_SYSTEM_PROMPT,
        workers=workers,
        shared_tools=[ToolDisplayToUser, ToolAskUser],
    )
