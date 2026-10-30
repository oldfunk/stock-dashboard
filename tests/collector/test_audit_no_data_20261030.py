"""批10-c no_data 归因器回归测试（2026-10-30，全程只读 + mock，不打网络）。

守的三件事：
1. 分类规则本身（纯函数 `classify`）——每条判据都要能被用例杀掉；
2. `--verify-live` 的三种升级（末根新鲜 / 末根久远 / 核验失败），用假 HTTP 打桩；
3. **"100% 归因"是断言不是口号**：落不进任何一类 -> `unattributed` 非空 -> 退出码 1；
   以及体检脚本绝不写库。
"""

import json

import pytest

from src.models import database as db_mod


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "data" / "db" / "nd10c.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(db_mod, "get_db_path", lambda: str(path))
    db_mod.init_database()
    return path


def _fact(code="600123", name="退市某某", kline_rows=0, fin_rows=10,
          in_snapshot=True, cov="2025-12-31"):
    return {"task": "price_history", "code": code, "name": name, "market": "A",
            "in_snapshot": in_snapshot, "kline_rows": kline_rows,
            "kline_last": None, "fin_rows": fin_rows, "fin_last": "2025-12-31",
            "covered_report_date": cov, "written_rows": 0, "recorded_at": "2026-10-28"}


# ── 1. 分类规则 ────────────────────────────────────────────────────────────

def test_classify_bj_unmapped():
    from scripts.audit_no_data import classify
    category, evidence = classify(_fact(code="920000", name="安徽凤凰",
                                        kline_rows=0, fin_rows=0))
    assert category == "bj_unmapped"
    assert "北交所" in evidence and "920" in evidence


@pytest.mark.parametrize("name", ["退市沪科", "国华退", "恒久退", "*ST 退市某"])
def test_classify_delisted_by_name(name):
    """两种命名形式都要认：产线 9 只里既有"退市XX"前缀式，也有"XX退"后缀式
    （只判前缀的后半批会漏，变异实测过）。"""
    from scripts.audit_no_data import classify
    category, evidence = classify(_fact(name=name))
    assert category == "delisted"
    assert name in evidence


class _Resp:
    def __init__(self, payload):
        self._payload = payload.encode("utf-8")

    def read(self):
        return self._payload


def test_last_bar_date_three_exits_are_distinct(monkeypatch):
    """三态必须来自真函数而不是测试里的桩：末根 / 明确无此 symbol / 核验失败。"""
    import scripts.audit_no_data as aud
    seen = []

    def fake_urlopen(req, timeout=15):
        seen.append(req.full_url)
        # 新浪这个接口返回的是**裸数组**（探针实测形状），不是 {"data": [...]}
        return _Resp('[{"day":"2026-10-27 15:00:00","close":"0.73"}]')

    monkeypatch.setattr(aud.urllib.request, "urlopen", fake_urlopen)
    assert aud.last_bar_date("301139") == ("2026-10-27", None)
    assert "symbol=sz301139" in seen[0]

    monkeypatch.setattr(aud.urllib.request, "urlopen",
                        lambda req, timeout=15: _Resp("null"))
    assert aud.last_bar_date("301139") == (None, None), \
        "接口正常应答但无该 symbol -> 是'确属停止挂牌'的证据，不能塞进 error 通道"

    def boom(req, timeout=15):
        raise TimeoutError("无响应")

    monkeypatch.setattr(aud.urllib.request, "urlopen", boom)
    last, err = aud.last_bar_date("301139")
    assert last is None and "TimeoutError" in err, "核验失败必须与'无数据'分开"

    # 上游改返回形状也不算"确属停止挂牌"——那会把解析失败当成事实
    monkeypatch.setattr(aud.urllib.request, "urlopen",
                        lambda req, timeout=15: _Resp('{"data": []}'))
    last, err = aud.last_bar_date("301139")
    assert last is None and "响应形状异常" in err


def test_classify_ambiguous_when_no_delisting_marker():
    """行情 0 行、财务有值、名字不带"退" -> 库内证据分不开，必须留在 ambiguous。"""
    from scripts.audit_no_data import classify
    category, _ = classify(_fact(code="301139", name="*ST元道"))
    assert category == "ambiguous"


def test_classify_unattributed_when_evidence_matches_no_rule():
    """既不是北交所、名字不带退、财务也是 0 -> 现有判据盖不住，必须显式失败而不是猜。"""
    from scripts.audit_no_data import classify
    category, evidence = classify(_fact(code="600999", name="正常名字",
                                        kline_rows=0, fin_rows=0))
    assert category == "unattributed"
    assert "需要新增规则" in evidence


def test_bj_code_with_financial_rows_is_not_called_unmapped():
    """北交所号段但已经有财务数据 -> 不能按"整号段没采到"归类（规则要保守）。"""
    from scripts.audit_no_data import classify
    category, _ = classify(_fact(code="920001", name="纬达光电", fin_rows=5))
    assert category == "ambiguous"


def test_sina_symbol_prefixes():
    from scripts.audit_no_data import sina_sina_symbol
    assert sina_sina_symbol("600608") == "sh600608"
    assert sina_sina_symbol("900900") == "sh900900"
    assert sina_sina_symbol("000016") == "sz000016"
    assert sina_sina_symbol("301139") == "sz301139"


# ── 2. --verify-live 的三种升级 ────────────────────────────────────────────

def _resolve_with(monkeypatch, last=None, err=None, fresh_days=5):
    import scripts.audit_no_data as aud

    def fake_last(code, timeout=15):
        return last, err

    monkeypatch.setattr(aud, "last_bar_date", fake_last)
    monkeypatch.setattr(aud, "time", type("T", (), {"sleep": staticmethod(lambda s: None)})())
    rows = [dict(_fact(code="301139", name="*ST元道"))]
    return aud.resolve(rows, verify_live=True, fresh_days=fresh_days, sleep=0)[0]


def test_verify_live_fresh_bar_becomes_collection_defect(monkeypatch):
    from datetime import datetime
    row = _resolve_with(monkeypatch, last=datetime.now().strftime("%Y-%m-%d"))
    assert row["category"] == "live_but_no_data"
    assert "采集缺陷" in row["evidence"]


def test_verify_live_old_bar_becomes_stopped_trading(monkeypatch):
    row = _resolve_with(monkeypatch, last="2026-06-29")
    assert row["category"] == "stopped_trading"
    assert "2026-06-29" in row["evidence"]


def test_verify_live_symbol_absent_counts_as_stopped(monkeypatch):
    """接口正常应答但无该 symbol 日线 -> 这是"确属停止挂牌"的证据（≠核验失败）。"""
    row = _resolve_with(monkeypatch, last=None, err=None)
    assert row["category"] == "stopped_trading"
    assert "查不到" in row["evidence"]


def test_verify_live_failure_leaves_ambiguous_but_records_reason(monkeypatch):
    """核验失败不能猜：留在 ambiguous，但失败原因要写进证据里。"""
    row = _resolve_with(monkeypatch, last=None, err="TimeoutError: 无响应")
    assert row["category"] == "ambiguous"
    assert "末根核验失败" in row["evidence"]


def test_no_verify_live_does_not_touch_network(monkeypatch):
    import scripts.audit_no_data as aud

    def boom(code, timeout=15):
        raise AssertionError("默认路径不该打网络")

    monkeypatch.setattr(aud, "last_bar_date", boom)
    rows = aud.resolve([_fact(code="301139", name="*ST元道")], verify_live=False,
                       fresh_days=5, sleep=0)
    assert rows[0]["category"] == "ambiguous"


# ── 3. 端到端：读库、汇总、退出码、零写入 ──────────────────────────────────

def _seed_no_data(include_unattributable=True):
    with db_mod.db_conn() as conn:
        for code, name in (("920000", "安徽凤凰"), ("920001", "纬达光电")):
            conn.execute(
                "INSERT OR REPLACE INTO stock_snapshot (code, name, market,"
                " snapshot_date) VALUES (?, ?, 'A', '2026-10-30')", (code, name))
            conn.execute(
                "INSERT OR REPLACE INTO backfill_progress (task, code, updated_rows,"
                " status, covered_report_date, updated_at)"
                " VALUES ('financial_full', ?, 0, 'no_data', NULL, '2026-10-27')",
                (code,))
        conn.execute(
            "INSERT OR REPLACE INTO stock_snapshot (code, name, market,"
            " snapshot_date) VALUES ('301139', '*ST元道', 'A', '2026-10-30')")
        conn.execute(
            "INSERT OR REPLACE INTO backfill_progress (task, code, updated_rows,"
            " status, covered_report_date, updated_at)"
            " VALUES ('price_history', '301139', 0, 'no_data', '2025-12-31',"
            " '2026-10-28')")
        conn.execute(
            "INSERT INTO financial_history (stock_code, report_date, report_type,"
            " created_at) VALUES ('301139', '2025-12-31', 'A', '2026-10-01')")
    if include_unattributable:
        with db_mod.db_conn() as conn:   # 一条规则盖不住的：0 行 + 名字不带退 + 非北交所
            conn.execute(
                "INSERT OR REPLACE INTO backfill_progress (task, code, updated_rows,"
                " status, covered_report_date, updated_at)"
                " VALUES ('price_history', '600999', 0, 'no_data', NULL, '2026-10-28')")


def test_run_reports_every_no_data_row_and_flags_unattributed(db):
    from scripts.audit_no_data import run
    _seed_no_data()
    result = run()
    assert result["summary"]["total"] == 4
    assert result["summary"]["by_category"]["bj_unmapped"] == 2
    assert result["summary"]["by_category"]["ambiguous"] == 1
    assert result["summary"]["by_category"]["unattributed"] == 1
    assert result["summary"]["unattributed_codes"] == ["600999"]
    assert result["summary"]["by_task"]["financial_full"] == {"bj_unmapped": 2}


def _stdout_json(capsys):
    """main() 会先打 init_database 的建表日志，JSON 从第一个独立成行的 '{' 开始。"""
    out = capsys.readouterr().out
    idx = out.index("{\n")
    return json.loads(out[idx:])


def test_cli_exit_one_when_anything_unattributed(db, capsys):
    """100% 归因是机器断言：有一只落不进规则就非 0 退出。"""
    import scripts.audit_no_data as aud
    _seed_no_data()
    rc = aud.main([])
    payload = _stdout_json(capsys)
    assert rc == 1
    assert payload["summary"]["unattributed_codes"] == ["600999"]


def test_cli_exit_zero_when_every_code_attributed(db, capsys):
    import scripts.audit_no_data as aud
    _seed_no_data(include_unattributable=False)
    assert aud.main(["--format", "md"]) == 0
    out = capsys.readouterr().out
    assert "| bj_unmapped |" in out and "600999" not in out


def test_audit_never_writes(db):
    """体检脚本只读：跑一遍后四张表行数一字不变。"""
    from scripts.audit_no_data import run
    _seed_no_data()

    def snapshot():
        with db_mod.db_conn() as conn:
            return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                    for t in ("backfill_progress", "stock_snapshot",
                              "financial_history", "kline_daily")}

    before = snapshot()
    run()
    run(verify_live=False)
    assert snapshot() == before
