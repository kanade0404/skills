"""テストの fixture."""

from __future__ import annotations

import os

import pytest

from textlint_runner import SKILL_ROOT, installed


@pytest.fixture
def real_env() -> dict[str, str]:
    """本物の bun と、開発用に入れた依存(スキルの node_modules)を使う環境変数.

    Returns:
        環境変数。依存が入っていなければ失敗させる。
    """
    if not installed(SKILL_ROOT):
        pytest.fail(f"{SKILL_ROOT} で bun install を実行してからテストしてください")
    return {"PATH": os.environ["PATH"], "TEXTLINT_SKILL_DEPS_DIR": str(SKILL_ROOT)}
