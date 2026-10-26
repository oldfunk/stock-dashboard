"""Research OS DAO（2026-10-26，R0-2）。

- 领域对象 -> 行（JSON 字段序列化）。
- 只做研究账本读写；业务库与快照库不在此处混淆。
- 不强制状态迁移（由注册表/领域对象负责），DAO 提供 update_status。
"""

from __future__ import annotations

import json
from typing import Any, Iterable

from src.models.database import db_conn, now_cn


def _data(obj: Any) -> dict[str, Any]:
    return obj.to_dict() if hasattr(obj, "to_dict") else dict(obj)


def _dumps(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def _loads(value: str | None, default: Any) -> Any:
    if value is None:
        return default
    return json.loads(value)


class ResearchDAO:
    """研究账本 DAO（SQLite）。"""

    # ── 假设 ────────────────────────────────────────────────────────────────
    def insert_hypothesis(self, obj: Any) -> None:
        d = _data(obj)
        with db_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO research_hypothesis
                (id, version, prior, statement, universe, signal_json, horizon_days,
                 benchmark, metric, threshold, regimes_json, costs_json,
                 kill_criteria_json, status, owner, created_at, locked_at,
                 review_date, supersedes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                d["id"], d.get("version", 1), d.get("prior"), d.get("statement"),
                d.get("universe"), _dumps(d.get("signal")), d.get("horizon_days"),
                d.get("benchmark"), d.get("metric"), d.get("threshold"),
                _dumps(d.get("regimes")), _dumps(d.get("costs")),
                _dumps(d.get("kill_criteria")), d.get("status"), d.get("owner", "human"),
                d.get("created_at") or now_cn().isoformat(), d.get("locked_at"),
                d.get("review_date"), d.get("supersedes"),
            ))

    def get_hypothesis(self, hypothesis_id: str) -> dict | None:
        with db_conn() as conn:
            row = conn.execute(
                "SELECT * FROM research_hypothesis WHERE id = ?",
                (hypothesis_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        for key in ("signal_json", "regimes_json", "costs_json", "kill_criteria_json"):
            field = key.replace("_json", "")
            d[field] = _loads(d.pop(key), [] if field == "regimes" or field == "kill_criteria" else {})
        return d

    def list_hypotheses(self, status: str | None = None, limit: int = 50) -> list[dict]:
        sql = "SELECT * FROM research_hypothesis"
        params: list = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with db_conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        out = []
        for row in rows:
            d = dict(row)
            d["signal"] = _loads(d.pop("signal_json"), {})
            d["regimes"] = _loads(d.pop("regimes_json"), [])
            d["costs"] = _loads(d.pop("costs_json"), {})
            d["kill_criteria"] = _loads(d.pop("kill_criteria_json"), [])
            out.append(d)
        return out

    def update_hypothesis_status(self, hypothesis_id: str, status: str) -> int:
        with db_conn() as conn:
            return conn.execute(
                "UPDATE research_hypothesis SET status = ? WHERE id = ?",
                (status, hypothesis_id)).rowcount

    # ── 实验 ────────────────────────────────────────────────────────────────
    def insert_experiment(self, obj: Any) -> None:
        d = _data(obj)
        with db_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO research_experiment
                (id, hypothesis_id, plan_json, data_snapshot_id, code_commit,
                 config_hash, random_seed, status, started_at, finished_at, error,
                 created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                d["id"], d.get("hypothesis_id"), _dumps(d.get("plan")),
                d.get("data_snapshot_id"), d.get("code_commit"), d.get("config_hash"),
                d.get("random_seed"), d.get("status"), d.get("started_at"),
                d.get("finished_at"), d.get("error"), d.get("created_at") or now_cn().isoformat(),
            ))

    def get_experiment(self, experiment_id: str) -> dict | None:
        with db_conn() as conn:
            row = conn.execute(
                "SELECT * FROM research_experiment WHERE id = ?",
                (experiment_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["plan"] = _loads(d.pop("plan_json"), {})
        return d

    def list_experiments(self, hypothesis_id: str | None = None,
                         status: str | None = None, limit: int = 50) -> list[dict]:
        sql = "SELECT * FROM research_experiment WHERE 1=1"
        params: list = []
        if hypothesis_id:
            sql += " AND hypothesis_id = ?"
            params.append(hypothesis_id)
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with db_conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        out = []
        for row in rows:
            d = dict(row)
            d["plan"] = _loads(d.pop("plan_json"), {})
            out.append(d)
        return out

    def update_experiment_status(self, experiment_id: str, status: str,
                                 error: str | None = None) -> int:
        with db_conn() as conn:
            return conn.execute(
                "UPDATE research_experiment SET status = ?, error = ? WHERE id = ?",
                (status, error, experiment_id)).rowcount

    # ── 结果 ────────────────────────────────────────────────────────────────
    def insert_result(self, obj: Any) -> None:
        d = _data(obj)
        with db_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO research_result
                (id, experiment_id, metric, value, ci_low, ci_high, p_value,
                 sample_size, splits_json, verdict, notes, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                d["id"], d.get("experiment_id"), d.get("metric"), d.get("value"),
                d.get("ci_low"), d.get("ci_high"), d.get("p_value"),
                d.get("sample_size"), _dumps(d.get("splits")), d.get("verdict"),
                d.get("notes"), d.get("created_at") or now_cn().isoformat(),
            ))

    def list_results(self, experiment_id: str) -> list[dict]:
        with db_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM research_result WHERE experiment_id = ? "
                "ORDER BY created_at DESC", (experiment_id,)).fetchall()
        out = []
        for row in rows:
            d = dict(row)
            d["splits"] = _loads(d.pop("splits_json"), {})
            out.append(d)
        return out

    # ── 策略 ────────────────────────────────────────────────────────────────
    def insert_strategy(self, obj: Any) -> None:
        d = _data(obj)
        with db_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO strategy_version
                (id, version, parent_id, spec_json, status, provenance_json,
                 created_at, approved_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                d["id"], d.get("version", 1), d.get("parent_id"),
                _dumps(d.get("spec")), d.get("status"), _dumps(d.get("provenance")),
                d.get("created_at") or now_cn().isoformat(), d.get("approved_at"),
            ))

    def get_strategy(self, strategy_id: str) -> dict | None:
        with db_conn() as conn:
            row = conn.execute(
                "SELECT * FROM strategy_version WHERE id = ?",
                (strategy_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["spec"] = _loads(d.pop("spec_json"), {})
        d["provenance"] = _loads(d.pop("provenance_json"), {})
        return d

    def list_strategies(self, status: str | None = None, limit: int = 50) -> list[dict]:
        sql = "SELECT * FROM strategy_version"
        params: list = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with db_conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        out = []
        for row in rows:
            d = dict(row)
            d["spec"] = _loads(d.pop("spec_json"), {})
            d["provenance"] = _loads(d.pop("provenance_json"), {})
            out.append(d)
        return out

    # ── 部署 / 归因 ──────────────────────────────────────────────────────────
    def insert_deployment(self, obj: Any) -> None:
        d = _data(obj)
        with db_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO strategy_deployment
                (id, strategy_id, mode, start_date, end_date, capital, broker,
                 status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                d["id"], d.get("strategy_id"), d.get("mode"), d.get("start_date"),
                d.get("end_date"), d.get("capital"), d.get("broker"),
                d.get("status"), d.get("created_at") or now_cn().isoformat(),
            ))

    def insert_attribution(self, obj: Any) -> None:
        d = _data(obj)
        with db_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO strategy_attribution
                (id, deployment_id, period_start, period_end, total_return,
                 excess_return, costs, factor_json, sector_json, stock_json,
                 regime, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                d["id"], d.get("deployment_id"), d.get("period_start"),
                d.get("period_end"), d.get("total_return"), d.get("excess_return"),
                d.get("costs"), _dumps(d.get("factor")), _dumps(d.get("sector")),
                _dumps(d.get("stock")), d.get("regime"),
                d.get("created_at") or now_cn().isoformat(),
            ))

    # ── 决策 ────────────────────────────────────────────────────────────────
    def insert_decision(self, obj: Any) -> None:
        d = _data(obj)
        with db_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO decision_log
                (id, decision_date, type, target, change, rationale,
                 evidence_json, expected, review_date, status, outcome, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                d["id"], d.get("decision_date"), d.get("type"), d.get("target"),
                d.get("change"), d.get("rationale"), _dumps(d.get("evidence")),
                d.get("expected"), d.get("review_date"), d.get("status"),
                d.get("outcome"), d.get("created_at") or now_cn().isoformat(),
            ))

    def list_decisions(self, status: str | None = None, limit: int = 50) -> list[dict]:
        sql = "SELECT * FROM decision_log"
        params: list = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY decision_date DESC LIMIT ?"
        params.append(limit)
        with db_conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        out = []
        for row in rows:
            d = dict(row)
            d["evidence"] = _loads(d.pop("evidence_json"), {})
            out.append(d)
        return out

    # ── 提示词 / LLM 运行 ────────────────────────────────────────────────────
    def insert_prompt(self, obj: Any) -> None:
        d = _data(obj)
        with db_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO prompt_registry
                (prompt_id, version, task, template_hash, schema_json, model_tier,
                 tools_json, max_tokens, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                d["prompt_id"], d.get("version"), d.get("task"),
                d.get("template_hash"), _dumps(d.get("schema")), d.get("model_tier"),
                _dumps(d.get("tools")), d.get("max_tokens"), d.get("status", "active"),
                d.get("created_at") or now_cn().isoformat(),
            ))

    def get_prompt(self, prompt_id: str, version: int) -> dict | None:
        with db_conn() as conn:
            row = conn.execute(
                "SELECT * FROM prompt_registry WHERE prompt_id = ? AND version = ?",
                (prompt_id, version)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["schema"] = _loads(d.pop("schema_json"), {})
        d["tools"] = _loads(d.pop("tools_json"), [])
        return d

    def insert_llm_run(self, obj: Any) -> None:
        d = _data(obj)
        with db_conn() as conn:
            conn.execute("""
                INSERT INTO llm_run
                (id, task, prompt_id, prompt_version, model, input_hash,
                 output_json, schema_ok, citations_ok, tokens, latency_ms,
                 human_feedback, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                d["id"], d.get("task"), d.get("prompt_id"), d.get("prompt_version"),
                d.get("model"), d.get("input_hash"), _dumps(d.get("output")),
                d.get("schema_ok"), d.get("citations_ok"), d.get("tokens"),
                d.get("latency_ms"), d.get("human_feedback"),
                d.get("created_at") or now_cn().isoformat(),
            ))

    def list_llm_runs(self, task: str | None = None, limit: int = 50) -> list[dict]:
        sql = "SELECT * FROM llm_run"
        params: list = []
        if task:
            sql += " WHERE task = ?"
            params.append(task)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with db_conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        out = []
        for row in rows:
            d = dict(row)
            d["output"] = _loads(d.pop("output_json"), {})
            out.append(d)
        return out

    # ── 特征 / 数据质量 ──────────────────────────────────────────────────────
    def insert_feature_definition(self, obj: Any) -> None:
        d = _data(obj)
        with db_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO feature_definition
                (feature_id, version, name, formula, unit, direction,
                 missing_policy, lookback, source, owner, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                d["feature_id"], d.get("version", 1), d.get("name"), d.get("formula"),
                d.get("unit"), d.get("direction"), d.get("missing_policy"),
                d.get("lookback"), d.get("source"), d.get("owner"), d.get("status", "draft"),
                d.get("created_at") or now_cn().isoformat(),
            ))

    def insert_feature_value(self, instrument_id: str, trade_date: str,
                             feature_id: str, feature_version: int,
                             value: float | None, quality: str | None) -> None:
        with db_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO stock_feature_daily
                (instrument_id, trade_date, feature_id, feature_version, value, quality)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (instrument_id, trade_date, feature_id, feature_version, value, quality))

    def insert_universe_membership(self, obj: Any) -> None:
        d = _data(obj)
        with db_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO universe_membership
                (universe_id, instrument_id, trade_date, status, reason, source)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                d["universe_id"], d.get("instrument_id"), d.get("trade_date"),
                d.get("status"), d.get("reason"), d.get("source"),
            ))

    def insert_quality(self, obj: Any) -> None:
        d = _data(obj)
        with db_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO data_quality_daily
                (dataset, trade_date, coverage, missing_rate, outliers,
                 pit_violations, status, details_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                d["dataset"], d.get("trade_date"), d.get("coverage"),
                d.get("missing_rate"), d.get("outliers"), d.get("pit_violations"),
                d.get("status"), _dumps(d.get("details")),
            ))

    def list_quality(self, dataset: str, limit: int = 30) -> list[dict]:
        with db_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM data_quality_daily WHERE dataset = ? "
                "ORDER BY trade_date DESC LIMIT ?", (dataset, limit)).fetchall()
        out = []
        for row in rows:
            d = dict(row)
            d["details"] = _loads(d.pop("details_json"), {})
            out.append(d)
        return out
