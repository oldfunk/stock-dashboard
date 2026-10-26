"""工具网关回归测试（R0-10）。"""

import pytest

from src.models import database as db_mod


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "data" / "db" / "g.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(path))
    db_mod.init_database()
    return path


def _register(task="market_summary", tools=None):
    from src.research.llm.tasks import TaskRegistry
    TaskRegistry().register({
        "task_id": task, "version": 1, "description": "d", "model_tier": "fast",
        "tools": tools if tools is not None else [
            "market_state", "features", "hypothesis", "experiment",
            "draft_decision"],
        "output_schema": {"type": "object"}, "max_tokens": 2000,
        "status": "active"})


def test_readonly_tool_call_and_audit(db):
    from src.research.dao import ResearchDAO
    from src.research.llm.tools import ToolGateway
    _register()
    result = ToolGateway().call("market_summary", "hypothesis")
    assert result.ok
    assert result.elapsed_ms >= 0
    runs = ResearchDAO().list_llm_runs("market_summary")
    assert runs and runs[0]["prompt_id"] == "hypothesis"
    assert runs[0]["output"]["tool"] == "hypothesis"


def test_unauthorized_tool_rejected(db):
    from src.research.llm.tools import ToolError, ToolGateway
    _register(tools=["market_state"])
    with pytest.raises(ToolError):
        ToolGateway().call("market_summary", "hypothesis")


def test_unknown_tool_rejected(db):
    from src.research.llm.tools import ToolError, ToolGateway
    _register()
    with pytest.raises(ToolError):
        ToolGateway().call("market_summary", "rm_rf")


def test_draft_write_requires_token(db):
    from src.research.llm.tools import ToolError, ToolGateway
    _register()
    gw = ToolGateway()
    with pytest.raises(ToolError):
        gw.call("market_summary", "draft_decision",
                {"target": "S-1", "change": "x"})
    result = gw.call("market_summary", "draft_decision",
                     {"target": "S-1", "change": "x"}, approval_token="tok")
    assert result.ok


def test_missing_required_arg_rejected(db):
    from src.research.llm.tools import ToolError, ToolGateway
    _register()
    with pytest.raises(ToolError):
        ToolGateway().call("market_summary", "features", {})


def test_market_state_is_honest_unavailable(db):
    from src.research.llm.tools import ToolGateway
    _register()
    result = ToolGateway().call("market_summary", "market_state")
    assert result.ok
    assert result.data[0]["status"] == "unavailable"
