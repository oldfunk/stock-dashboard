"""Prompt 框架回归测试（R0-9）。"""

import tempfile
from pathlib import Path

import pytest

from src.models import database as db_mod


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "data" / "db" / "p.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(path))
    db_mod.init_database()
    return path


def _content():
    return """## System
You are a research assistant.

## Context
{{market_state}}

## Task
Write a summary.

## Schema
```json
{"type": "object", "required": ["summary"]}
```
"""


def _register_task(task="market_summary"):
    from src.research.llm.tasks import TaskRegistry
    TaskRegistry().register({
        "task_id": task, "version": 1, "description": "d",
        "model_tier": "fast", "tools": ["get_market_state"],
        "output_schema": {"type": "object"}, "max_tokens": 2000,
        "status": "active"})


def test_save_get_render_and_replay(db):
    from src.research.llm.prompts import PromptRegistry, prompts_dir
    _register_task()
    reg = PromptRegistry()
    assert reg.save("market_summary", _content()) == 1
    assert (prompts_dir() / "market_summary" / "v1.md").exists()
    rendered = reg.render("market_summary", 1, {"market_state": "PE 20x"})
    assert "PE 20x" in rendered
    assert reg.replay("market_summary", 1).startswith("---")
    assert reg.schema("market_summary", 1) == {
        "type": "object", "required": ["summary"]}
    # 变更 -> 新版本；旧版本仍可回放
    assert reg.save("market_summary",
                    _content().replace("Write a summary.",
                                       "Write a concise summary.")) == 2
    assert "Write a summary." in reg.replay("market_summary", 1)
    assert "concise" in reg.replay("market_summary", 2)


def test_render_keeps_unfilled_placeholder(db):
    from src.research.llm.prompts import PromptRegistry
    _register_task()
    reg = PromptRegistry()
    reg.save("market_summary", _content())
    assert "{{market_state}}" in reg.render("market_summary", 1, {})


def test_missing_version_raises(db):
    from src.research.llm.prompts import PromptError, PromptRegistry
    with pytest.raises(PromptError):
        PromptRegistry().replay("nope", 1)


def test_bad_structure_rejected(db):
    from src.research.llm.prompts import PromptError, read_prompt
    path = Path(tempfile.mkdtemp()) / "v1.md"
    path.write_text(
        "---\ntask: t\nversion: 1\nmodel_tier: fast\n---\n\nno sections\n",
        encoding="utf-8")
    with pytest.raises(PromptError):
        read_prompt(path)


def test_sync_registers_existing_files(db):
    from src.models.database import db_conn
    from src.research.llm.prompts import PromptRegistry
    _register_task()
    reg = PromptRegistry()
    reg.save("market_summary", _content())
    with db_conn() as conn:
        conn.execute("DELETE FROM prompt_registry WHERE prompt_id='market_summary'")
    assert reg.dao.get_prompt("market_summary", 1) is None
    assert reg.sync("market_summary") == [1]
    assert reg.dao.get_prompt("market_summary", 1) is not None
