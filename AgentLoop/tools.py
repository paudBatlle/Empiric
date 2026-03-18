from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable, Type

from docker import errors as docker_errors
from pydantic import BaseModel, Field

from clients import docker_client

if TYPE_CHECKING:
    # Imported only for type checking to avoid circular import at runtime.
    from orchestrator import OrchestratedAgentLoop

class Tool(BaseModel):
    async def __call__(self) -> str:
        raise NotImplementedError


class ToolRunCommandInDevContainer(Tool):
    """Run a command in the Python dev container and return its output."""

    command: str

    def _run(self) -> str:
        container = docker_client.containers.get("python-dev")
        exec_command = f"bash -c '{self.command}'"

        try:
            res = container.exec_run(exec_command)
            output = res.output.decode("utf-8")
        except Exception as e:
            output = f"Error: {e}\nCommand used: {exec_command}"

        return output

    async def __call__(self) -> str:
        return await asyncio.to_thread(self._run)


class ToolUpsertFile(Tool):
    """Create or update a file inside the Python dev container."""

    file_path: str = Field(description="Path to the file to create or update")
    content: str = Field(description="Full file content")

    def _run(self) -> str:
        container = docker_client.containers.get("python-dev")
        cmd = f'sh -c "cat > {self.file_path}"'
        _, socket = container.exec_run(
            cmd, stdin=True, stdout=True, stderr=True, stream=False, socket=True
        )
        socket._sock.sendall((self.content + "\n").encode("utf-8"))
        socket._sock.close()
        return "File written successfully"

    async def __call__(self) -> str:
        return await asyncio.to_thread(self._run)


def start_python_dev_container(container_name: str) -> None:
    """Start a fresh Python development container."""
    try:
        existing_container = docker_client.containers.get(container_name)
        if existing_container.status == "running":
            existing_container.kill()
        existing_container.remove()
    except docker_errors.NotFound:
        pass

    volume_path = str(Path(".scratchpad").absolute())
    _ = volume_path

    docker_client.containers.run(
        "python:3.12",
        detach=True,
        name=container_name,
        ports={"8888/tcp": 8888},
        tty=True,
        stdin_open=True,
        working_dir="/app",
        command="bash -c 'mkdir -p /app && tail -f /dev/null'",
    )


def create_tool_display_to_user(
    displayer: Callable[[str], Awaitable[None]],
) -> Type[Tool]:
    class ToolDisplayToUser(Tool):
        """Display information to the user without waiting for input."""

        text: str = Field(description="Text to display to the user")

        async def __call__(self) -> str:
            await displayer(self.text)
            return "Displayed message to user."

    return ToolDisplayToUser


def create_tool_ask_user(
    prompter: Callable[[str], Awaitable[str]],
) -> Type[Tool]:
    class ToolAskUser(Tool):
        """Ask the user for missing information and wait for a reply."""

        query: str = Field(description="Question to ask the user")

        async def __call__(self) -> str:
            return await prompter(self.query)

    return ToolAskUser


def create_tool_interact_with_user(
    prompter: Callable[[str], Awaitable[str]],
) -> Type[Tool]:
    """Backward-compatible alias for ask-user behavior."""
    return create_tool_ask_user(prompter)


def create_tool_delegate_to_agent(
    *,
    tool_name: str,
    description: str,
    delegate: Callable[[str], Awaitable[str]],
) -> Type[Tool]:
    class ToolDelegateToAgent(Tool):
        task: str = Field(description="Concrete task to delegate to this worker agent")

        async def __call__(self) -> str:
            return await delegate(self.task)

    ToolDelegateToAgent.__name__ = tool_name
    ToolDelegateToAgent.__doc__ = description
    return ToolDelegateToAgent


def create_tool_add_worker(loop: OrchestratedAgentLoop) -> Type[Tool]:
    class ToolAddWorker(Tool):
        """Create a new worker agent and register a delegate tool on the orchestrator."""

        name: str = Field(description="Unique name for the new worker")
        description: str = Field(description="Short description of what this worker does")
        system_prompt: str = Field(description="System prompt for the worker")
        # For a first version, keep tools simple: choose from a fixed set by name.
        tool_names: list[str] = Field(
            default_factory=list,
            description="List of tool class names (as strings) to enable for this worker",
        )

        async def __call__(self) -> str:
            # map tool_names -> actual Tool classes
            import tools as tools_module

            available_tool_classes: dict[str, type[Tool]] = {
                cls.__name__: cls
                for cls in vars(tools_module).values()
                if isinstance(cls, type) and issubclass(cls, Tool)
            }
            selected_tools: list[type[Tool]] = []
            for name in self.tool_names:
                tool_cls = available_tool_classes.get(name)
                if tool_cls is not None:
                    selected_tools.append(tool_cls)

            config = WorkerConfig(
                name=self.name,
                description=self.description,
                system_prompt=self.system_prompt,
                tools=selected_tools,
            )
            loop.add_worker(config)
            return f"Worker `{self.name}` created with tools: {[t.__name__ for t in selected_tools]}"

    return ToolAddWorker


