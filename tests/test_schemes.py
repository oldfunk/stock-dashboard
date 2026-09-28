"""投资方案单测：永远只有 mother / general / custom 三种。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from paper_trading.strategy.schemes import (  # noqa: E402
    CUSTOM_ID,
    GENERAL_ID,
    MOTHER_ID,
    active_name,
    all_schemes,
    custom_instruction,
    mother_strategies,
    resolve_scheme,
    set_active,
)


def _mother(tmp_path):
    import sqlite3

    mdir = tmp_path / "sd"
    (mdir / "config").mkdir(parents=True)
    (mdir / "src").mkdir()
    (mdir / "config" / "strategies.yaml").write_text(
        "growth:\n  name: 成长型\n  desc: d\n  pe_max: 50\n",
        encoding="utf-8")
    dbdir = mdir / "data" / "db"
    dbdir.mkdir(parents=True)
    c = sqlite3.connect(str(dbdir / "stock_dashboard.db"))
    c.execute("CREATE TABLE t(x)")
    c.commit()
    c.close()
    return mdir


def test_exactly_three_kinds_no_mother(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCK_DASHBOARD_DIR", str(tmp_path / "nothing"))
    ss = all_schemes(str(tmp_path))
    assert set(ss) == {MOTHER_ID, GENERAL_ID, CUSTOM_ID} or set(ss) == {GENERAL_ID, CUSTOM_ID}
    assert MOTHER_ID not in ss  # Stock Dashboard 缺席，无母类
    assert resolve_scheme("nope", str(tmp_path)).name == GENERAL_ID
    assert resolve_scheme(MOTHER_ID, str(tmp_path)).name == GENERAL_ID  # 缺席回退
    name, src = active_name(str(tmp_path), GENERAL_ID)
    assert (name, src) == (GENERAL_ID, "default")


def test_mother_scheme_when_present(tmp_path, monkeypatch):
    mdir = _mother(tmp_path)
    monkeypatch.setenv("STOCK_DASHBOARD_DIR", str(mdir))
    ss = all_schemes(str(tmp_path))
    assert set(ss) == {MOTHER_ID, GENERAL_ID, CUSTOM_ID}
    m = ss[MOTHER_ID]
    assert m.available and m.universe_source == "screening"
    assert resolve_scheme(MOTHER_ID, str(tmp_path)).name == MOTHER_ID
    assert mother_strategies()[0]["key"] == "growth"


def test_set_active_only_three_ids(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCK_DASHBOARD_DIR", str(tmp_path / "nothing"))
    ok, _ = set_active(str(tmp_path), GENERAL_ID)
    assert ok
    assert active_name(str(tmp_path), GENERAL_ID) == (GENERAL_ID, "local")
    ok, _ = set_active(str(tmp_path), "ghost")
    assert not ok  # 第四种不存在
    ok, msg = set_active(str(tmp_path), MOTHER_ID)
    assert not ok and "同一台机器" in msg  # 母缺席拒绝，非静默
    # 自定义指令随切换一起存
    ok, _ = set_active(str(tmp_path), CUSTOM_ID, "只做银行股")
    assert ok
    assert custom_instruction(str(tmp_path)) == "只做银行股"
    assert active_name(str(tmp_path), GENERAL_ID)[0] == CUSTOM_ID


def test_custom_instruction_flows_to_prompt(monkeypatch, tmp_path):
    """custom 方案的自然语言指令必须进 prompt；换方案后消失。"""
    import json
    import tempfile

    import paper_trading.hermes_bridge as hb
    from paper_trading.agent import AgentTrader
    from paper_trading.llm import provider as prov
    from paper_trading.models import Bar
    from datetime import datetime, timedelta

    seen: dict = {}

    def fake(cfg, messages, system=""):
        seen["prompt"] = messages[0]["content"]
        return json.dumps({"actions": [], "summary": "x"})

    monkeypatch.setattr(prov, "chat", fake)
    monkeypatch.setenv("STOCK_DASHBOARD_DIR", str(tmp_path / "nothing"))
    f1 = tempfile.mktemp(suffix=".db", dir=str(tmp_path))
    f2 = tempfile.mktemp(suffix=".db", dir=str(tmp_path))
    b = hb.HermesBridge(data_db=f1, account_db=f2, config_path="/nonexistent.yaml",
                        secrets_path=str(tmp_path / "s.json"))
    from paper_trading.strategy.schemes import Scheme as _Scheme
    b.scheme = _Scheme(name=CUSTOM_ID, title="自定义指令",
                       instruction="只做银行股反弹")
    bars = [Bar(symbol="600519",
                timestamp=datetime(2024, 1, 1) + timedelta(days=10000 + i),
                open=10, high=11, low=9, close=10, volume=100)
            for i in range(30)]
    b.data_db.upsert_bars(bars)
    b.llm_save("custom", "https://x/v1", "m", "k")
    AgentTrader(b, b.agent_cfg).run(["600519"], dry_run=True)
    assert "只做银行股反弹" in seen["prompt"]
