from __future__ import annotations

import json

import pytest

from protocol import (
    StopInput,
    ToolUseInput,
    TranscriptEntry,
    as_object,
    integer,
    items,
    objects,
    parse_object,
    parse_stop,
    parse_tool_use,
    parse_transcript_line,
    string,
)


class TestAccessors:
    @pytest.mark.parametrize("text", ["", "not json", "[]", "1", "null"])
    def test_parse_object_rejects_non_objects(self, text: str) -> None:
        assert parse_object(text) is None

    def test_parse_object(self) -> None:
        assert parse_object('{"a": [1]}') == {"a": [1]}

    def test_as_object(self) -> None:
        assert as_object({"a": 1}) == {"a": 1}
        assert as_object([1]) is None

    def test_items_and_objects(self) -> None:
        assert items("x") == []
        assert objects([{"a": 1}, "x", 1, None]) == [{"a": 1}]

    def test_string(self) -> None:
        assert string({"a": "x", "b": 1}, "a") == "x"
        assert string({"a": "x", "b": 1}, "b") is None
        assert string({}, "a") is None

    @pytest.mark.parametrize(("value", "expected"), [(1, 1), (True, None), (1.0, None), ("1", None)])
    def test_integer_rejects_bool_and_others(self, value: object, expected: int | None) -> None:
        fields = parse_object(json.dumps({"v": value}))
        assert fields is not None
        assert integer(fields, "v") == expected


class TestParseToolUse:
    def test_claude_code(self) -> None:
        text = json.dumps({"cwd": "/p", "tool_input": {"file_path": "a.md"}})
        assert parse_tool_use(text) == ToolUseInput(cwd="/p", file_path="a.md", command=None)

    def test_codex_command_list_is_joined(self) -> None:
        text = json.dumps({"tool_input": {"command": ["apply_patch", 1]}})
        assert parse_tool_use(text) == ToolUseInput(cwd=None, file_path=None, command="apply_patch\n1")

    def test_malformed_fields(self) -> None:
        text = json.dumps({"cwd": 1, "tool_input": {"file_path": 1, "command": 1}})
        assert parse_tool_use(text) == ToolUseInput(cwd=None, file_path=None, command=None)

    @pytest.mark.parametrize("text", ["", "not json", "[]", "{}"])
    def test_empty(self, text: str) -> None:
        assert parse_tool_use(text) is None


class TestParseStop:
    def test_fields(self) -> None:
        text = json.dumps({"last_assistant_message": "a", "transcript_path": "/t", "stop_hook_active": True})
        assert parse_stop(text) == StopInput("a", "/t", stop_hook_active=True)

    @pytest.mark.parametrize("active", [1, "true", False, None])
    def test_only_true_is_active(self, active: object) -> None:
        stop_input = parse_stop(json.dumps({"stop_hook_active": active}))
        assert stop_input == StopInput(None, None, stop_hook_active=False)

    @pytest.mark.parametrize("text", ["", "[]", "{}"])
    def test_empty(self, text: str) -> None:
        assert parse_stop(text) is None


class TestParseTranscriptLine:
    def test_string_content(self) -> None:
        line = json.dumps({"type": "user", "message": {"content": "hi"}})
        assert parse_transcript_line(line) == TranscriptEntry("user", ("hi",))

    def test_blocks_keep_only_text(self) -> None:
        content = [{"type": "text", "text": "a"}, {"type": "tool_use"}, {"type": "text"}, "junk"]
        line = json.dumps({"type": "assistant", "message": {"content": content}})
        assert parse_transcript_line(line) == TranscriptEntry("assistant", ("a", ""))

    def test_malformed(self) -> None:
        assert parse_transcript_line(json.dumps({"type": 1, "message": "x"})) == TranscriptEntry(None, ())
        assert parse_transcript_line("not json") is None
