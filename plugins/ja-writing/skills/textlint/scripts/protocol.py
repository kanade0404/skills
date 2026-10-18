from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, TypedDict, Union, cast

if TYPE_CHECKING:
    from typing_extensions import TypeAlias

JsonValue: TypeAlias = Union[str, int, float, bool, "list[JsonValue]", "dict[str, JsonValue]", None]
JsonObject: TypeAlias = "dict[str, JsonValue]"


def loads(text: str) -> JsonValue:
    return cast("JsonValue", json.loads(text))


def as_object(value: JsonValue) -> JsonObject | None:
    return value if isinstance(value, dict) else None


def parse_object(text: str) -> JsonObject | None:
    try:
        return as_object(loads(text))
    except ValueError:
        return None


def items(value: JsonValue) -> list[JsonValue]:
    return value if isinstance(value, list) else []


def objects(value: JsonValue) -> list[JsonObject]:
    return [item for item in items(value) if isinstance(item, dict)]


def string(fields: JsonObject, key: str) -> str | None:
    value = fields.get(key)
    return value if isinstance(value, str) else None


def integer(fields: JsonObject, key: str) -> int | None:
    value = fields.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


@dataclass(frozen=True)
class ToolUseInput:
    cwd: str | None
    file_path: str | None
    command: str | None


def parse_tool_use(text: str) -> ToolUseInput | None:
    payload = parse_object(text)
    if not payload:
        return None
    tool_input = as_object(payload.get("tool_input")) or {}
    command = tool_input.get("command")
    if isinstance(command, list):
        command = "\n".join(str(part) for part in command)
    return ToolUseInput(
        cwd=string(payload, "cwd"),
        file_path=string(tool_input, "file_path"),
        command=command if isinstance(command, str) else None,
    )


@dataclass(frozen=True)
class StopInput:
    last_assistant_message: str | None
    transcript_path: str | None
    stop_hook_active: bool


def parse_stop(text: str) -> StopInput | None:
    payload = parse_object(text)
    if not payload:
        return None
    return StopInput(
        last_assistant_message=string(payload, "last_assistant_message"),
        transcript_path=string(payload, "transcript_path"),
        stop_hook_active=payload.get("stop_hook_active") is True,
    )


@dataclass(frozen=True)
class TranscriptEntry:
    role: str | None
    texts: tuple[str, ...]


def parse_transcript_line(line: str) -> TranscriptEntry | None:
    entry = parse_object(line)
    if entry is None:
        return None
    content = (as_object(entry.get("message")) or {}).get("content")
    if isinstance(content, str):
        texts: tuple[str, ...] = (content,)
    else:
        texts = tuple(string(block, "text") or "" for block in objects(content) if string(block, "type") == "text")
    return TranscriptEntry(role=string(entry, "type"), texts=texts)


class BlockOutput(TypedDict):
    decision: Literal["block"]
    reason: str


class SystemMessageOutput(TypedDict):
    systemMessage: str


class PostToolUseContext(TypedDict):
    hookEventName: Literal["PostToolUse"]
    additionalContext: str


class PostToolUseContextOutput(TypedDict):
    hookSpecificOutput: PostToolUseContext


class SessionStartContext(TypedDict):
    hookEventName: Literal["SessionStart"]
    additionalContext: str


class SessionStartOutput(TypedDict):
    hookSpecificOutput: SessionStartContext
