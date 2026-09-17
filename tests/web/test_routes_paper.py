"""虚拟盘面板路由测试（只读 /paper）"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    """每个测试用独立临时数据库"""
    from src.models import database as db_mod
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(db_mod, "get_db_path", lambda: db_path)
    db_mod.init_database()
    # 避免启动调度器线程
    from src.scheduler import MarketScheduler
    monkeypatch.setattr(MarketScheduler, "start", lambda self: None)
    monkeypatch.setattr(MarketScheduler, "stop", lambda self: None)
    from src.config import start_config_watcher, stop_config_watcher
    monkeypatch.setattr("src.config.start_config_watcher", lambda *a, **k: None)
    monkeypatch.setattr("src.config.stop_config_watcher", lambda *a, **k: None)
    from src.web.routes import app
    return TestClient(app)


def test_paper_page_empty(client):
    """空纸盘：200 + 非实盘横幅 + 空持仓提示"""
    resp = client.get("/paper")
    assert resp.status_code == 200
    assert "非实盘" in resp.text
    assert "暂无持仓" in resp.text
    assert "暂无委托记录" in resp.text


def test_paper_page_with_data(client):
    """有账户/持仓/委托/净值时数值渲染"""
    from src.models.database import (
        PaperAccountDAO, PaperOrderDAO, PaperPositionDAO, PaperNavDAO,
    )
    PaperAccountDAO().get_or_create(initial_cash=1000000.0)
    PaperPositionDAO().upsert("600519", 300, 300, avg_price=1500.0)
    PaperOrderDAO().place("600519", "BUY", 1500.0, 300, signal_source="高")
    PaperNavDAO().save("2026-09-17", 1010000.0, cash=560000.0,
                       cumulative_pnl=10000.0)

    resp = client.get("/paper")
    assert resp.status_code == 200
    assert "600519" in resp.text
    assert "560000.00" in resp.text
    assert "10000.00" in resp.text
    assert "已报" in resp.text  # submitted 状态中文


def test_paper_page_missing_tables(client):
    """纸盘表缺失（如未重启迁移）：200 降级为空状态，不 500"""
    from src.models.database import db_conn
    with db_conn() as conn:
        for t in ("paper_account", "paper_orders", "paper_trades",
                  "paper_positions", "paper_nav"):
            conn.execute(f"DROP TABLE IF EXISTS {t}")

    resp = client.get("/paper")
    assert resp.status_code == 200
    assert "尚未初始化" in resp.text


def test_order_dao_list_recent(client):
    """list_recent 按 id 倒序 + limit 生效"""
    from src.models.database import PaperOrderDAO
    dao = PaperOrderDAO()
    for _ in range(3):
        dao.place("000001", "BUY", 10.0, 100)
    recent = dao.list_recent(limit=2)
    assert len(recent) == 2
    assert recent[0]["id"] > recent[1]["id"]
