from __future__ import annotations

import io
import stat
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

AI_LIKE = "# 紹介\n\n- **高速**: 圧倒的な速度を実現します🚀\n\n設定は次の通りです:\n\n```\n{}\n```\n"
CLEAN = "# 紹介\n\nこの文書は問題のない文章です。\n"


def write_executable(directory: Path, name: str, body: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    _ = path.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def set_stdin(monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(text))
