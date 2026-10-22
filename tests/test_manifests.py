from __future__ import annotations

from pathlib import Path
from typing import ClassVar, TypeVar

import pytest
from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIRS = sorted(path.parent.parent for path in ROOT.glob("plugins/*/.claude-plugin/plugin.json"))


class Strict(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True, strict=True)


class Person(Strict):
    name: str


class PluginInfo(Strict):
    name: str
    display_name: str = Field(alias="displayName")
    version: str
    description: str
    author: Person
    repository: str
    license: str
    keywords: list[str]


class ClaudePlugin(PluginInfo):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow", frozen=True, strict=True)


class CursorPlugin(PluginInfo):
    skills: str


class MarketplaceEntry(Strict):
    name: str
    source: str
    description: str


class ClaudeMarketplace(Strict):
    name: str
    owner: Person
    description: str
    plugins: list[MarketplaceEntry]


class Metadata(Strict):
    description: str


class CursorMarketplace(Strict):
    name: str
    owner: Person
    metadata: Metadata
    plugins: list[MarketplaceEntry]


Model = TypeVar("Model", bound=BaseModel)


def read(path: Path, model: type[Model]) -> Model:
    return model.model_validate_json(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("directory", PLUGIN_DIRS, ids=[path.name for path in PLUGIN_DIRS])
def test_cursor_manifest_matches_claude(directory: Path) -> None:
    claude = read(directory / ".claude-plugin" / "plugin.json", ClaudePlugin)
    cursor = read(directory / ".cursor-plugin" / "plugin.json", CursorPlugin)
    shared = set(PluginInfo.model_fields)
    assert cursor.model_dump(include=shared) == claude.model_dump(include=shared)
    assert cursor.skills == "./skills/"


def test_cursor_marketplace_matches_claude() -> None:
    claude = read(ROOT / ".claude-plugin" / "marketplace.json", ClaudeMarketplace)
    cursor = read(ROOT / ".cursor-plugin" / "marketplace.json", CursorMarketplace)
    assert cursor.name == claude.name
    assert cursor.owner == claude.owner
    assert cursor.metadata == Metadata(description=claude.description)
    expected = [entry.model_copy(update={"source": entry.source.removeprefix("./")}) for entry in claude.plugins]
    assert cursor.plugins == expected
