import asyncio
from pathlib import Path
from typing import Awaitable, Callable, Type

from docker import errors as docker_errors
from pydantic import BaseModel, Field

from clients import docker_client


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


def create_tool_interact_with_user(
    prompter: Callable[[str], Awaitable[str]],
) -> Type[Tool]:
    class ToolInteractWithUser(Tool):
        """Ask the user for missing information."""

        query: str = Field(description="Question to ask the user")
        display: str = Field(
            description="Optional markdown artifact to display while waiting for user input"
        )

        async def __call__(self) -> str:
            return await prompter(self.query)

    return ToolInteractWithUser


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
