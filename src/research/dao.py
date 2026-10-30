"""Research OS DAO（2026-10-26，R0-2）。

- 领域对象 -> 行（JSON 字段序列化）。
- 只做研究账本读写；业务库与快照库不在此处混淆。
- 不强制状态迁移（由注册表/领域对象负责），DAO 提供 update_status。
"""

from __future__ import annotations

import json
from typing import Any

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

    def update_strategy_status(self, strategy_id: str, status: str) -> int:
        with db_conn() as conn:
            return conn.execute(
                "UPDATE strategy_version SET status = ? WHERE id = ?",
                (status, strategy_id)).rowcount

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

    def update_deployment_status(self, deployment_id: str, status: str) -> int:
        with db_conn() as conn:
            return conn.execute(
                "UPDATE strategy_deployment SET status = ? WHERE id = ?",
                (status, deployment_id)).rowcount

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

    def list_decisions_by_target(self, target: str, limit: int = 50) -> list[dict]:
        """按 target 精确查决策（供状态变更守卫用，避免全表扫描）。"""
        with db_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM decision_log WHERE target = ? "
                "ORDER BY decision_date DESC LIMIT ?", (target, limit)).fetchall()
        out = []
        for row in rows:
            d = dict(row)
            d["evidence"] = _loads(d.pop("evidence_json"), {})
            out.append(d)
        return out

    def get_decision(self, decision_id: str) -> dict | None:
        with db_conn() as conn:
            row = conn.execute(
                "SELECT * FROM decision_log WHERE id = ?", (decision_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["evidence"] = _loads(d.pop("evidence_json"), {})
        return d

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

    # ── LLM 任务注册（R0-8） ─────────────────────────────────────────────────
    def insert_task(self, obj: Any) -> None:
        d = _data(obj)
        with db_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO llm_task
                (task_id, version, description, model_tier, tools_json,
                 output_schema_json, max_tokens, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                d["task_id"], d.get("version", 1), d.get("description"),
                d.get("model_tier"), _dumps(d.get("tools")),
                _dumps(d.get("output_schema")), d.get("max_tokens"),
                d.get("status", "active"),
                d.get("created_at") or now_cn().isoformat(),
            ))

    def get_task(self, task_id: str, version: int | None = None) -> dict | None:
        if version is None:
            sql = "SELECT * FROM llm_task WHERE task_id = ? ORDER BY version DESC LIMIT 1"
            params = [task_id]
        else:
            sql = "SELECT * FROM llm_task WHERE task_id = ? AND version = ?"
            params = [task_id, version]
        with db_conn() as conn:
            row = conn.execute(sql, params).fetchone()
        return self._task_dict(row) if row else None

    def list_tasks(self, status: str | None = None, limit: int = 50) -> list[dict]:
        sql = "SELECT * FROM llm_task"
        params: list = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY task_id, version DESC LIMIT ?"
        params.append(limit)
        with db_conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._task_dict(row) for row in rows]

    @staticmethod
    def _task_dict(row) -> dict:
        d = dict(row)
        d["tools"] = _loads(d.pop("tools_json"), [])
        d["output_schema"] = _loads(d.pop("output_schema_json"), {})
        return d

    # ── 品种（多市场基础） ────────────────────────────────────────────────────
    def upsert_instrument(self, obj: Any) -> None:
        d = _data(obj)
        with db_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO instrument
                (instrument_id, market_id, symbol, exchange, name, currency,
                 lot_size, tick_size, list_date, delist_date, status,
                 created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                d["instrument_id"], d.get("market_id", "CN"), d.get("symbol"),
                d.get("exchange"), d.get("name"), d.get("currency"),
                d.get("lot_size"), d.get("tick_size"), d.get("list_date"),
                d.get("delist_date"), d.get("status", "active"),
                d.get("created_at") or now_cn().isoformat(),
                d.get("updated_at") or now_cn().isoformat(),
            ))

    def list_instruments(self, status: str | None = None,
                         limit: int = 50) -> list[dict]:
        sql = "SELECT * FROM instrument"
        params: list = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY instrument_id LIMIT ?"
        params.append(limit)
        with db_conn() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def count_instruments(self, status: str | None = None) -> int:
        sql = "SELECT COUNT(*) AS n FROM instrument"
        params: list = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        with db_conn() as conn:
            return conn.execute(sql, params).fetchone()["n"]

    # ── 市场状态快照（R1-2） ─────────────────────────────────────────────────
    def insert_market_state(self, obj: Any) -> None:
        d = _data(obj)
        with db_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO market_state_daily
                (trade_date, metrics_json, missing_json, coverage, status,
                 regime, regime_confidence, quality_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                d["trade_date"], _dumps(d.get("metrics")), _dumps(d.get("missing")),
                d.get("coverage"), d.get("status"), d.get("regime"),
                d.get("regime_confidence"), _dumps(d.get("quality")),
                d.get("created_at") or now_cn().isoformat(),
            ))

    def get_market_state(self, trade_date: str) -> dict | None:
        with db_conn() as conn:
            row = conn.execute(
                "SELECT * FROM market_state_daily WHERE trade_date = ?",
                (trade_date,)).fetchone()
        return self._state_dict(row) if row else None

    def list_market_states(self, limit: int = 30) -> list[dict]:
        with db_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM market_state_daily ORDER BY trade_date DESC LIMIT ?",
                (limit,)).fetchall()
        return [self._state_dict(r) for r in rows]

    def get_state_dates(self, start: str, end: str) -> list[str]:
        """区间内已落库的市场状态交易日（升序）。

        回填的"跑到哪了"直接以目标表为准——不另立进度表，写库面保持在目标表内
        （代价：源数据事后被修正过的日子不会自动失效，须显式 ``--recompute``）。
        """
        with db_conn() as conn:
            rows = conn.execute(
                "SELECT trade_date FROM market_state_daily "
                "WHERE trade_date BETWEEN ? AND ? ORDER BY trade_date",
                (start, end)).fetchall()
        return [r["trade_date"] for r in rows]

    @staticmethod
    def _state_dict(row) -> dict:
        d = dict(row)
        d["metrics"] = _loads(d.pop("metrics_json"), {})
        d["missing"] = _loads(d.pop("missing_json"), [])
        d["quality"] = _loads(d.pop("quality_json"), {})
        return d

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
                d.get("status") or "recorded", _dumps(d.get("details")),
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

    def get_quality(self, dataset: str, trade_date: str) -> dict | None:
        """按 (dataset, trade_date) 精确取一条质量记录。"""
        with db_conn() as conn:
            row = conn.execute(
                "SELECT * FROM data_quality_daily "
                "WHERE dataset = ? AND trade_date = ?",
                (dataset, trade_date)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["details"] = _loads(d.pop("details_json"), {})
        return d
