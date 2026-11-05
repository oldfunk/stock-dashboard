"""批17-c 用例（2026-11-05）：预注册文案完整性闸（H-001 乱码教训）。

产线只读取证：H-001（已锁定）的 statement 39 字符里 26 个是 `?`，库里字节
`3F3F3F3F3F3F3F3F4D4F533F…`，kill_criteria_json 同样——判据与终止条件在权威库里读不出。
本批不改那条历史行（生产写归母方），改的是**让下一次锁不上这种卡**。
"""

import pytest

from src.models import database as db_mod


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "data" / "db" / "hinteg.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(path))
    db_mod.init_database()
    return path


def _service():
    from src.research.hypothesis import HypothesisService
    return HypothesisService()


def _create(service, **over):
    data = dict(statement="MOS 有预测力", prior="value", universe="A",
                signal={"feature": "mos"}, horizon_days=126,
                benchmark="sh000300", metric="excess", threshold=">0",
                regimes=["all"], costs={}, kill_criteria=["超额连续两年为负"])
    data.update(over)
    return service.create(**data)


# ── 正常路径不能被闸误伤 ──────────────────────────────────

def test_normal_chinese_card_creates_and_locks(db):
    svc = _service()
    row = _create(svc)
    assert row["statement"] == "MOS 有预测力"
    assert svc.lock(row["id"])["status"] == "locked", "正常中文卡片不该被完整性闸拦住"


def test_single_question_mark_is_legitimate(db):
    """单个 `?` 是正常问句（"CI 跨 0 吗?"），只有连续两个才像被替换过。"""
    svc = _service()
    row = _create(svc, statement="MOS 在 risk_off 里有预测力吗?")
    assert svc.lock(row["id"])["locked_at"]


# ── 三种损坏形状都要拦下 ─────────────────────────────────

@pytest.mark.parametrize("bad", [
    "????????MOS????T+20/T+60",            # H-001 实况：ASCII 通道 errors=replace
    "MOS \ufffd\ufffd 有预测力",            # U+FFFD 替换符
    "MOS \udce4\udce0 有预测力",            # argv 按非 UTF-8 解码的代理残留
])
def test_mangled_statement_cannot_be_created_or_locked(db, bad):
    from src.research.hypothesis import HypothesisError
    svc = _service()
    with pytest.raises(HypothesisError) as exc:
        _create(svc, statement=bad)
    assert "statement" in str(exc.value)
    with db_mod.db_conn() as conn:
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM research_hypothesis").fetchone()["n"] == 0, \
            "拦下时不许留下半张卡"


def test_mangled_kill_criteria_is_blocked(db):
    """判据写得清清楚楚，终止条件却是问号——这张卡等于没有 kill criteria。"""
    from src.research.hypothesis import HypothesisError
    svc = _service()
    with pytest.raises(HypothesisError) as exc:
        _create(svc, kill_criteria=["??MOS?<10?", "CI??0"])
    assert "kill_criteria[0]" in str(exc.value)


def test_lock_blocks_a_card_whose_text_was_mangled_by_a_bypass(db):
    """绕开 create 直接写库的脏卡，lock 这一步也必须拒绝（锁了就不可能再改）。"""
    from src.research.hypothesis import HypothesisError
    svc = _service()
    row = _create(svc)
    with db_mod.db_conn() as conn:
        conn.execute("UPDATE research_hypothesis SET statement = ? WHERE id = ?",
                     ("????MOS????", row["id"]))
    with pytest.raises(HypothesisError):
        svc.lock(row["id"])
    assert svc.view(row["id"])["status"] == "draft", "拒绝锁定后状态不能被改成 locked"


def test_update_rejects_mangled_text_without_touching_the_row(db):
    from src.research.hypothesis import HypothesisError
    svc = _service()
    row = _create(svc)
    with pytest.raises(HypothesisError):
        svc.update(row["id"], statement="??????")
    assert svc.view(row["id"])["statement"] == "MOS 有预测力", "失败的 update 不许留痕"
