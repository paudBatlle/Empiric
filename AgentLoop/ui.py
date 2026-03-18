import asyncio

from agent import EventText, EventToolResult, EventToolUse
from app_runtime import (
    DEFAULT_CLAUDE_MODEL,
    DEFAULT_OLLAMA_MODEL,
    build_model_name,
    create_orchestrated_loop,
    warmup_ollama_if_needed,
)
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.status import Status
from tools import start_python_dev_container


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
        model_name = input(f"Ollama model name (default: {DEFAULT_OLLAMA_MODEL}): ").strip()
        return build_model_name("ollama", model_name)

    claude_model = input(f"Claude model name (default: {DEFAULT_CLAUDE_MODEL}): ").strip()
    return build_model_name("claude", claude_model)


async def main():
    selected_model = choose_model()
    warmup_ollama_if_needed(selected_model)
    loop = create_orchestrated_loop(
        model=selected_model,
        ask_user=get_prompt_from_user,
        display_to_user=display_message_to_user,
    )
    loop.set_worker_event_callback(
        lambda event: (
            console.print(
            Panel(
                Markdown(
                    f"## Source\n`{event.source}`\n\n"
                    f"## Tool\n`{event.tool.__class__.__name__}`\n\n"
                    f"## Args\n```json\n{event.tool.model_dump_json(indent=2)}\n```"
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

    console = Console()
    try:
        start_python_dev_container("python-dev")
    except RuntimeError as exc:
        console.print(f"[yellow]Warning: Docker tools unavailable: {exc}[/yellow]")
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
