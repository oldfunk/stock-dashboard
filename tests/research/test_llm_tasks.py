"""LLM 任务注册表回归测试（R0-8）。"""

import pytest

from src.models import database as db_mod


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "data" / "db" / "t.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(path))
    db_mod.init_database()
    return path


def _task(**over):
    from src.research.llm.tasks import TaskSpec
    data = dict(task_id="market_summary", version=1, description="市场状态叙事",
                model_tier="fast", tools=["get_market_state"],
                output_schema={"type": "object", "required": ["summary"]},
                max_tokens=2000, status="active")
    data.update(over)
    return TaskSpec(**data)


def test_register_get_list_and_tools(db):
    from src.research.llm.tasks import TaskRegistry
    reg = TaskRegistry()
    reg.register(_task())
    spec = reg.get("market_summary")
    assert spec.model_tier == "fast"
    assert spec.tools == ["get_market_state"]
    assert reg.list_tasks("active")[0].task_id == "market_summary"
    assert reg.allowed_tools("market_summary") == ["get_market_state"]


def test_unregistered_task_rejected(db):
    from src.research.llm.tasks import LLMError, TaskRegistry
    with pytest.raises(LLMError):
        TaskRegistry().require("nope")
    with pytest.raises(LLMError):
        TaskRegistry().assert_callable("nope")


def test_missing_schema_or_tools_rejected(db):
    from src.research.llm.tasks import LLMError, TaskRegistry
    reg = TaskRegistry()
    with pytest.raises(LLMError):
        reg.register(_task(task_id="no_schema", output_schema={}))
    with pytest.raises(LLMError):
        reg.register(_task(task_id="no_tools", tools=[]))
    with pytest.raises(LLMError):
        reg.register(_task(task_id="bad_tier", model_tier="turbo"))
    with pytest.raises(LLMError):
        reg.register(_task(task_id="bad_tokens", max_tokens=0))


def test_retired_task_not_callable(db):
    from src.research.llm.tasks import LLMError, TaskRegistry
    reg = TaskRegistry()
    reg.register(_task(task_id="old_task", status="retired"))
    assert reg.require("old_task").status == "retired"
    with pytest.raises(LLMError):
        reg.assert_callable("old_task")


def test_duplicate_version_rejected(db):
    from src.research.llm.tasks import LLMError, TaskRegistry
    reg = TaskRegistry()
    reg.register(_task())
    with pytest.raises(LLMError):
        reg.register(_task())


def test_versioning(db):
    from src.research.llm.tasks import TaskRegistry
    reg = TaskRegistry()
    reg.register(_task(version=1))
    reg.register(_task(version=2, max_tokens=4000))
    assert reg.get("market_summary").version == 2
    assert reg.get("market_summary", 1).max_tokens == 2000
