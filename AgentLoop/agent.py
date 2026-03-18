from dataclasses import dataclass, field
import json
from typing import Any, AsyncGenerator

from anthropic.types import MessageParam, ToolResultBlockParam, ToolUnionParam
from pydantic import ValidationError
from tenacity import AsyncRetrying, stop_after_attempt, wait_fixed

from clients import anthropic_client, ollama_client
from tools import Tool


@dataclass
class EventText:
    source: str
    text: str
    type: str = "text"


@dataclass
class EventInputJson:
    source: str
    partial_json: str
    type: str = "input_json"


@dataclass
class EventToolUse:
    source: str
    tool: Tool
    type: str = "tool_use"


@dataclass
class EventToolResult:
    source: str
    tool: Tool
    result: str
    type: str = "tool_result"


AgentEvent = EventText | EventInputJson | EventToolUse | EventToolResult


@dataclass
class Agent:
    name: str
    system_prompt: str
    model: str
    tools: list[Tool]
    messages: list[MessageParam] = field(default_factory=list)
    ollama_messages: list[dict[str, Any]] = field(default_factory=list)
    available_tools: list[ToolUnionParam] = field(default_factory=list)
    available_tools_ollama: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self):
        self.available_tools = [
            {
                "name": tool.__name__,
                "description": tool.__doc__ or "",
                "input_schema": tool.model_json_schema(),
            }
            for tool in self.tools
        ]
        self.available_tools_ollama = [
            {
                "type": "function",
                "function": {
                    "name": tool.__name__,
                    "description": tool.__doc__ or "",
                    "parameters": tool.model_json_schema(),
                },
            }
            for tool in self.tools
        ]
        self.ollama_messages.append({"role": "system", "content": self.system_prompt})

    def add_user_message(self, message: str):
        self.messages.append(MessageParam(role="user", content=message))
        self.ollama_messages.append({"role": "user", "content": message})

    def _is_ollama_model(self) -> bool:
        return self.model.startswith("ollama:")

    def _ollama_model_name(self) -> str:
        return self.model.split(":", 1)[1] if self._is_ollama_model() else self.model

    @staticmethod
    def _tool_argument_error_result(
        *,
        tool_name: str,
        tool_args: dict[str, Any],
        validation_error: ValidationError,
        schema: dict[str, Any],
    ) -> str:
        return json.dumps(
            {
                "ok": False,
                "error_type": "tool_argument_validation_error",
                "tool_name": tool_name,
                "message": "Tool arguments failed schema validation. Retry with corrected arguments.",
                "received_arguments": tool_args,
                "required_fields": schema.get("required", []),
                "validation_errors": validation_error.errors(),
            }
        )

    @staticmethod
    def _tool_not_available_result(*, tool_name: str) -> str:
        return json.dumps(
            {
                "ok": False,
                "error_type": "tool_not_available",
                "tool_name": tool_name,
                "message": f"Tool `{tool_name}` is not available. Pick one of the declared tools.",
            }
        )

    async def _run_tool(
        self, tool_name: str, tool_args: dict[str, Any]
    ) -> AsyncGenerator[AgentEvent, None]:
        for tool in self.tools:
            if tool.__name__ != tool_name:
                continue

            try:
                t = tool.model_validate(tool_args)
            except ValidationError as exc:
                structured_error = self._tool_argument_error_result(
                    tool_name=tool_name,
                    tool_args=tool_args,
                    validation_error=exc,
                    schema=tool.model_json_schema(),
                )
                yield EventText(
                    source=self.name,
                    text=(
                        f"Tool `{tool_name}` argument validation failed. "
                        "Returning structured error so the model can retry."
                    ),
                )
                # Use model_construct so UI/debug output can still show a tool instance.
                invalid_tool_call = tool.model_construct(**tool_args)
                yield EventToolResult(
                    source=self.name, tool=invalid_tool_call, result=structured_error
                )
                return

            yield EventToolUse(source=self.name, tool=t)
            result = await t()
            yield EventToolResult(source=self.name, tool=t, result=result)
            return

        structured_error = self._tool_not_available_result(tool_name=tool_name)
        yield EventText(
            source=self.name,
            text=(
                f"Tool `{tool_name}` was requested but is not available. "
                "Returning structured error so the model can retry."
            ),
        )
        yield EventToolResult(
            source=self.name,
            tool=Tool.model_construct(),
            result=structured_error,
        )
        return

    async def _agentic_loop_anthropic(self) -> AsyncGenerator[AgentEvent, None]:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(3), wait=wait_fixed(3)
        ):
            with attempt:
                async with anthropic_client.messages.stream(
                    max_tokens=8000,
                    messages=self.messages,
                    model=self.model,
                    tools=self.available_tools,
                    system=self.system_prompt,
                ) as stream:
                    async for event in stream:
                        if event.type == "text":
                            yield EventText(source=self.name, text=event.text)
                        elif event.type == "input_json":
                            yield EventInputJson(
                                source=self.name, partial_json=event.partial_json
                            )
                        elif event.type == "thinking":
                            ...
                        elif event.type == "content_block_stop":
                            ...
                    accumulated = await stream.get_final_message()

        self.messages.append(MessageParam(role="assistant", content=accumulated.content))

        for content in accumulated.content:
            if content.type != "tool_use":
                continue

            tool_name = content.name
            tool_args = content.input

            for tool in self.tools:
                if tool.__name__ != tool_name:
                    continue

                try:
                    t = tool.model_validate(tool_args)
                except ValidationError as exc:
                    result = self._tool_argument_error_result(
                        tool_name=tool_name,
                        tool_args=tool_args,
                        validation_error=exc,
                        schema=tool.model_json_schema(),
                    )
                    yield EventText(
                        source=self.name,
                        text=(
                            f"Tool `{tool_name}` argument validation failed. "
                            "Returning structured error so the model can retry."
                        ),
                    )
                    invalid_tool_call = tool.model_construct(**tool_args)
                    yield EventToolResult(
                        source=self.name, tool=invalid_tool_call, result=result
                    )
                else:
                    yield EventToolUse(source=self.name, tool=t)
                    result = await t()
                    yield EventToolResult(source=self.name, tool=t, result=result)

                self.messages.append(
                    MessageParam(
                        role="user",
                        content=[
                            ToolResultBlockParam(
                                type="tool_result",
                                tool_use_id=content.id,
                                content=result,
                            )
                        ],
                    )
                )
                break
            else:
                result = self._tool_not_available_result(tool_name=tool_name)
                yield EventText(
                    source=self.name,
                    text=(
                        f"Tool `{tool_name}` was requested but is not available. "
                        "Returning structured error so the model can retry."
                    ),
                )
                yield EventToolResult(
                    source=self.name,
                    tool=Tool.model_construct(),
                    result=result,
                )
                self.messages.append(
                    MessageParam(
                        role="user",
                        content=[
                            ToolResultBlockParam(
                                type="tool_result",
                                tool_use_id=content.id,
                                content=result,
                            )
                        ],
                    )
                )

        if accumulated.stop_reason == "tool_use":
            async for e in self._agentic_loop_anthropic():
                yield e

    async def _agentic_loop_ollama(self) -> AsyncGenerator[AgentEvent, None]:
        while True:
            response = await ollama_client.chat(
                model=self._ollama_model_name(),
                messages=self.ollama_messages,
                tools=self.available_tools_ollama,
                stream=False,
            )
            # Handle network / server errors from Ollama gracefully.
            if isinstance(response, dict) and response.get("error"):
                error_type = response.get("error")
                status = response.get("status")
                reason = response.get("reason")
                body = response.get("body")
                details = f"{error_type}"
                if status is not None:
                    details += f" (status {status})"
                if reason:
                    details += f": {reason}"
                if body:
                    details += f"\nResponse body: {body}"
                yield EventText(
                    source=self.name,
                    text=f"Ollama request failed: {details}",
                )
                break

            message = response.get("message", {})
            text = (message.get("content") or "").strip()
            if text:
                yield EventText(source=self.name, text=text)

            assistant_message = {"role": "assistant", "content": message.get("content", "")}
            tool_calls = message.get("tool_calls") or []
            if tool_calls:
                assistant_message["tool_calls"] = tool_calls
            self.ollama_messages.append(assistant_message)

            if not tool_calls:
                break

            for tool_call in tool_calls:
                fn = tool_call.get("function", {})
                tool_name = fn.get("name", "")
                raw_arguments = fn.get("arguments", {})
                if isinstance(raw_arguments, str):
                    try:
                        tool_args = json.loads(raw_arguments)
                    except json.JSONDecodeError:
                        tool_args = {}
                else:
                    tool_args = raw_arguments

                result = ""
                async for event in self._run_tool(tool_name, tool_args):
                    yield event
                    if isinstance(event, EventToolResult):
                        result = event.result

                self.ollama_messages.append(
                    {
                        "role": "tool",
                        "name": tool_name,
                        "content": result,
                    }
                )
    
    def _rebuild_tool_metadata(self) -> None:
        self.available_tools = [
            {
                "name": tool.__name__,
                "description": tool.__doc__ or "",
                "input_schema": tool.model_json_schema(),
            }
            for tool in self.tools
        ]
        self.available_tools_ollama = [
            {
                "type": "function",
                "function": {
                    "name": tool.__name__,
                    "description": tool.__doc__ or "",
                    "parameters": tool.model_json_schema(),
                },
            }
            for tool in self.tools
        ]

    def __post_init__(self):
        self._rebuild_tool_metadata()
        self.ollama_messages.append({"role": "system", "content": self.system_prompt})

    def add_tool(self, tool: type["Tool"]) -> None:
        self.tools.append(tool)
        self._rebuild_tool_metadata()

    async def agentic_loop(self) -> AsyncGenerator[AgentEvent, None]:
        if self._is_ollama_model():
            async for e in self._agentic_loop_ollama():
                yield e
            return

        async for e in self._agentic_loop_anthropic():
            yield e

    async def run(self) -> AsyncGenerator[AgentEvent, None]:
        async for x in self.agentic_loop():
            yield x
