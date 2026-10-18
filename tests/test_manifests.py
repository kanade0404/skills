from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

ROOT = Path(__file__).resolve().parent.parent
SHARED_FIELDS = ("name", "displayName", "version", "description", "author", "repository", "license", "keywords")


def read(path: Path) -> dict[str, object]:
    return cast("dict[str, object]", json.loads(path.read_text(encoding="utf-8")))


def plugins(marketplace: dict[str, object]) -> list[dict[str, object]]:
    return cast("list[dict[str, object]]", marketplace["plugins"])


PLUGIN_DIRS = sorted(path.parent.parent for path in ROOT.glob("plugins/*/.claude-plugin/plugin.json"))


@pytest.mark.parametrize("directory", PLUGIN_DIRS, ids=[path.name for path in PLUGIN_DIRS])
def test_cursor_manifest_matches_claude(directory: Path) -> None:
    claude = read(directory / ".claude-plugin" / "plugin.json")
    cursor = read(directory / ".cursor-plugin" / "plugin.json")
    assert {key: cursor.get(key) for key in SHARED_FIELDS} == {key: claude.get(key) for key in SHARED_FIELDS}
    assert cursor["skills"] == "./skills/"


def test_cursor_marketplace_matches_claude() -> None:
    claude = read(ROOT / ".claude-plugin" / "marketplace.json")
    cursor = read(ROOT / ".cursor-plugin" / "marketplace.json")
    assert cursor["name"] == claude["name"]
    assert cursor["owner"] == claude["owner"]
    assert cursor["metadata"] == {"description": claude["description"]}
    expected = [
        {"name": entry["name"], "source": str(entry["source"]).removeprefix("./"), "description": entry["description"]}
        for entry in plugins(claude)
    ]
    assert plugins(cursor) == expected
