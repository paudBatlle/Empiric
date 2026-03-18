import asyncio
import subprocess

from agent import EventText, EventToolResult, EventToolUse
from orchestrator import OrchestratedAgentLoop, WorkerConfig
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.status import Status
from tools import (
    ToolRunCommandInDevContainer,
    ToolUpsertFile,
    create_tool_ask_user,
    create_tool_display_to_user,
    start_python_dev_container,
)


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


async def get_prompt_from_user(query: str) -> str:
    print()
    answer = input(f"User input needed:\n{query}\n\nUser: ")
    print()
    return answer


async def display_message_to_user(text: str) -> None:
    print()
    print(f"Agent message:\n{text}\n")


def choose_model() -> str:
    provider = (
        input("Choose model provider [claude/ollama] (default: claude): ")
        .strip()
        .lower()
    )
    if provider == "ollama":
        model_name = input("Ollama model name (default: llama3.1): ").strip()
        return f"ollama:{model_name or 'llama3.1'}"

    claude_model = input(
        "Claude model name (default: claude-3-5-sonnet-latest): "
    ).strip()
    return claude_model or "claude-3-5-sonnet-latest"


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


async def main():
    selected_model = choose_model()
    warmup_ollama_if_needed(selected_model)
    ToolAskUser = create_tool_ask_user(get_prompt_from_user)
    ToolDisplayToUser = create_tool_display_to_user(display_message_to_user)
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
    # Initialize with predefined workers
    loop = OrchestratedAgentLoop(
        model=selected_model,
        orchestrator_prompt=ORCHESTRATOR_SYSTEM_PROMPT,
        workers=workers,
        shared_tools=[ToolDisplayToUser, ToolAskUser],
    )
    loop.set_worker_event_callback(
        lambda event: (
            console.print(
            Panel(
                Markdown(
                    f"## Source\n`{event.source}`\n\n"
                    f"## Tool\n`{event.tool.__class__.__name__}`\n\n"
                    f"## Args\n\n{event.tool.model_dump_json(indent=2)}\n```"
                ),
                title="Worker Tool Call",
                border_style="magenta",
            )
        )
        if isinstance(event, EventToolUse)
        else console.print(
            Panel(
                Markdown(
                    f"## Source\n`{event.source}`\n\n"
                    f"## Result\n{(event.result or '')[:400]}"
                ),
                title="Worker Tool Result",
                border_style="magenta",
            )
        )
        if isinstance(event, EventToolResult)
        else None
    )
    )

    start_python_dev_container("python-dev")
    console = Console()
    status = Status("")

    while True:
        console.print(Rule("[bold blue]User[/bold blue]"))
        print(f"Active model: {selected_model}")
        query = input("\nUser: ").strip()
        loop.add_user_message(query)

        console.print(Rule("[bold blue]Orchestrated Agent Loop[/bold blue]"))
        async for event in loop.run():
            match event:
                case EventText(source=source, text=text):
                    if source != "orchestrator":
                        print(f"\n[{source}] ", end="", flush=True)
                    print(text, end="", flush=True)
                case EventToolUse(source=source, tool=tool):
                    status.update(f"{source} tool: {tool}")
                    panel = Panel(
                        Markdown(
                            f"## Source\n`{source}`\n\n"
                            f"## Tool\n`{tool.__class__.__name__}`\n\n"
                            f"## Args\n```json\n{tool.model_dump_json(indent=2)}\n```"
                        ),
                        title="Tool Call",
                        border_style="green",
                    )
                    status.start()
                    status.stop()
                    console.print()
                    console.print(panel)
                    console.print()
                case EventToolResult(source=source, result=result):
                    panel = Panel(
                        Markdown(f"## Source\n`{source}`\n\n## Result\n{result}"),
                        title="Tool Result",
                        border_style="green",
                    )
                    console.print(panel)
        print("\n")


if __name__ == "__main__":
    asyncio.run(main())
