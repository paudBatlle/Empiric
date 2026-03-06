from dataclasses import dataclass
from typing import Callable

from agent import Agent, AgentEvent, EventText, EventToolResult, EventToolUse
from tools import Tool, create_tool_delegate_to_agent


@dataclass
class WorkerConfig:
    name: str
    description: str
    system_prompt: str
    tools: list[Tool]


class OrchestratedAgentLoop:
    def __init__(
        self,
        *,
        model: str,
        orchestrator_prompt: str,
        workers: list[WorkerConfig],
        shared_tools: list[Tool] | None = None,
    ):
        self.workers: dict[str, Agent] = {
            w.name: Agent(
                name=w.name,
                model=model,
                system_prompt=w.system_prompt,
                tools=w.tools,
            )
            for w in workers
        }
        self._worker_descriptions = {w.name: w.description for w in workers}
        self._tool_event_callback: Callable[[AgentEvent], None] | None = None

        orchestrator_tools = []
        for worker_name, worker in self.workers.items():
            tool_name = f"ToolDelegateTo{worker_name.title().replace('-', '')}"
            description = (
                f"Delegate a task to `{worker_name}` worker. "
                f"{self._worker_descriptions[worker_name]}"
            )
            orchestrator_tools.append(
                create_tool_delegate_to_agent(
                    tool_name=tool_name,
                    description=description,
                    delegate=self._build_delegate(worker_name, worker),
                )
            )

        self.orchestrator = Agent(
            name="orchestrator",
            model=model,
            system_prompt=orchestrator_prompt,
            tools=[*(shared_tools or []), *orchestrator_tools],
        )

    def set_worker_event_callback(self, callback: Callable[[AgentEvent], None]) -> None:
        self._tool_event_callback = callback

    def add_user_message(self, message: str):
        self.orchestrator.add_user_message(message)

    def _build_delegate(self, worker_name: str, worker: Agent):
        async def delegate(task: str) -> str:
            worker.add_user_message(task)
            text_chunks: list[str] = []

            async for event in worker.run():
                if self._tool_event_callback is not None:
                    self._tool_event_callback(event)

                if isinstance(event, EventText):
                    text_chunks.append(event.text)
                elif isinstance(event, EventToolUse):
                    text_chunks.append(f"\n[{worker_name} used {event.tool.__class__.__name__}]")
                elif isinstance(event, EventToolResult):
                    text_chunks.append(
                        f"\n[{worker_name} tool result: {event.result.strip()[:400]}]"
                    )

            result = "".join(text_chunks).strip()
            return result or f"{worker_name} completed the task."

        return delegate

    async def run(self):
        async for event in self.orchestrator.run():
            yield event
