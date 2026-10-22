from __future__ import annotations

import os

import pytest

from textlint_runner import SKILL_ROOT, installed


@pytest.fixture
def real_env() -> dict[str, str]:
    if not installed(SKILL_ROOT):
        pytest.fail(f"{SKILL_ROOT} で bun install を実行してからテストしてください")
    return {"PATH": os.environ["PATH"], "TEXTLINT_SKILL_DEPS_DIR": str(SKILL_ROOT)}
