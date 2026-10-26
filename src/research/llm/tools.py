"""工具网关（2026-10-26，R0-10）。

职责：
- **只读工具**：``market_state`` / ``features`` / ``financials`` / ``valuation`` /
  ``outcomes`` / ``hypothesis`` / ``experiment``。
- **草案写工具**：``draft_decision`` / ``draft_hypothesis``（需批准 token）。
- **白名单**：只能调用任务声明的工具；越权拒绝。
- **超时 / 行数上限 / 审计**：每次调用写 ``llm_run``。

原则：LLM 只能读事实、只能产出草案；草案落库需人工批准 token。
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from src.models.database import db_conn, now_cn
from src.research.dao import ResearchDAO
from src.research.llm.tasks import TaskRegistry


class ToolError(ValueError):
    """工具越权 / 参数非法 / 超时。"""


READ_ONLY_TOOLS = frozenset({
    "market_state", "features", "financials", "valuation",
    "outcomes", "hypothesis", "experiment",
})
DRAFT_WRITE_TOOLS = frozenset({"draft_decision", "draft_hypothesis"})
ALL_TOOLS = READ_ONLY_TOOLS | DRAFT_WRITE_TOOLS

DEFAULT_TIMEOUT_S = 10.0
DEFAULT_ROW_LIMIT = 200


@dataclass
class ToolResult:
    tool: str
    ok: bool
    rows: int = 0
    elapsed_ms: int = 0
    error: str = ""
    data: Any = None
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool, "ok": self.ok, "rows": self.rows,
            "elapsed_ms": self.elapsed_ms, "error": self.error,
            "truncated": self.truncated,
            "data": self.data if self.rows <= 50 else f"<{self.rows} rows>",
        }


def _hash_args(tool: str, args: dict) -> str:
    raw = json.dumps({"tool": tool, "args": args}, sort_keys=True,
                     ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ── 只读工具实现 ────────────────────────────────────────────────────────────

def _rows(sql: str, params: tuple, limit: int) -> tuple[list[dict], bool]:
    with db_conn() as conn:
        cursor = conn.execute(sql, params)
        columns = [d[0] for d in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
    truncated = len(rows) > limit
    return rows[:limit], truncated


def tool_features(args: dict, limit: int) -> tuple[Any, bool]:
    rows, truncated = _rows(
        "SELECT instrument_id, trade_date, value, quality "
        "FROM stock_feature_daily WHERE feature_id = ? "
        "AND trade_date BETWEEN ? AND ? AND value IS NOT NULL "
        "ORDER BY instrument_id, trade_date",
        (args["feature_id"], args.get("start_date", "0000-00-00"),
         args.get("end_date", "9999-99-99")), limit)
    return rows, truncated


def tool_financials(args: dict, limit: int) -> tuple[Any, bool]:
    rows, truncated = _rows(
        "SELECT stock_code, report_date, report_type, notice_date, roe, eps, "
        "fcf, total_shares, cash, interest_bearing_debt "
        "FROM financial_history WHERE stock_code = ? AND report_type = 'A' "
        "ORDER BY report_date DESC", (args["stock_code"],), limit)
    return rows, truncated


def tool_valuation(args: dict, limit: int) -> tuple[Any, bool]:
    rows, truncated = _rows(
        "SELECT code, name, pe, pb, ps, market_cap, roe, revenue_growth, "
        "profit_growth, debt_ratio FROM stock_snapshot WHERE code = ?",
        (args["stock_code"],), 1)
    return rows, truncated


def tool_outcomes(args: dict, limit: int) -> tuple[Any, bool]:
    rows, truncated = _rows(
        "SELECT stock_code, analysis_date, signal, confidence, "
        "ret_20, ret_60, ret_120, excess_20, excess_60, excess_120 "
        "FROM analysis_outcome WHERE stock_code = ? "
        "ORDER BY analysis_date DESC", (args["stock_code"],), limit)
    return rows, truncated


def tool_hypothesis(args: dict, limit: int) -> tuple[Any, bool]:
    rows, truncated = _rows(
        "SELECT id, version, prior, statement, universe, status, metric, "
        "threshold, review_date FROM research_hypothesis "
        "WHERE status IN ('locked','testing','supported') "
        "ORDER BY created_at DESC", (), limit)
    return rows, truncated


def tool_experiment(args: dict, limit: int) -> tuple[Any, bool]:
    rows, truncated = _rows(
        "SELECT id, hypothesis_id, data_snapshot_id, code_commit, status, "
        "started_at, finished_at FROM research_experiment "
        "ORDER BY created_at DESC", (), limit)
    return rows, truncated


def tool_market_state(args: dict, limit: int) -> tuple[Any, bool]:
    # R1 未落地：显式返回不可用，不编造
    return [{"status": "unavailable",
             "reason": "市场状态向量尚未生成（R1）"}], False


def tool_draft_decision(args: dict, limit: int) -> tuple[Any, bool]:
    return [{"draft": args, "status": "pending_approval",
             "note": "草案需人工批准后才落库"}], False


def tool_draft_hypothesis(args: dict, limit: int) -> tuple[Any, bool]:
    return [{"draft": args, "status": "pending_approval",
             "note": "草案需人工批准后才落库"}], False


TOOL_FUNCS: dict[str, Callable[[dict, int], tuple[Any, bool]]] = {
    "market_state": tool_market_state,
    "features": tool_features,
    "financials": tool_financials,
    "valuation": tool_valuation,
    "outcomes": tool_outcomes,
    "hypothesis": tool_hypothesis,
    "experiment": tool_experiment,
    "draft_decision": tool_draft_decision,
    "draft_hypothesis": tool_draft_hypothesis,
}

# 各工具必需参数
REQUIRED_ARGS = {
    "features": ("feature_id",),
    "financials": ("stock_code",),
    "valuation": ("stock_code",),
    "outcomes": ("stock_code",),
    "draft_decision": ("target", "change"),
    "draft_hypothesis": ("statement",),
}


class ToolGateway:
    def __init__(self, dao: ResearchDAO | None = None,
                 task_registry: TaskRegistry | None = None):
        self.dao = dao or ResearchDAO()
        self.task_registry = task_registry or TaskRegistry(self.dao)

    def list_tools(self, task_id: str) -> list[str]:
        return self.task_registry.assert_callable(task_id).tools

    def call(self, task_id: str, tool: str, args: dict | None = None,
             approval_token: str | None = None,
             timeout_s: float = DEFAULT_TIMEOUT_S,
             row_limit: int = DEFAULT_ROW_LIMIT) -> ToolResult:
        """执行工具调用：白名单 -> 参数 -> 批准 -> 超时/行数 -> 审计。"""
        args = args or {}
        started = time.monotonic()
        spec = self.task_registry.assert_callable(task_id)
        if tool not in ALL_TOOLS:
            raise ToolError(f"未知工具: {tool}")
        if tool not in spec.tools:
            raise ToolError(f"任务 {task_id} 未授权工具: {tool}")
        for key in REQUIRED_ARGS.get(tool, ()):
            if not args.get(key):
                raise ToolError(f"工具 {tool} 缺少参数: {key}")
        if tool in DRAFT_WRITE_TOOLS and not approval_token:
            raise ToolError(f"草案写工具需批准 token: {tool}")

        func = TOOL_FUNCS[tool]
        error = ""
        data: Any = None
        truncated = False
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(func, args, row_limit)
                data, truncated = future.result(timeout=timeout_s)
        except concurrent.futures.TimeoutError:
            error = f"工具超时（>{timeout_s}s）"
        except Exception as exc:  # noqa: BLE001 - 工具异常转结果
            error = f"{type(exc).__name__}: {exc}"
        elapsed = int((time.monotonic() - started) * 1000)
        result = ToolResult(tool=tool, ok=not error,
                            rows=len(data) if isinstance(data, list) else 0,
                            elapsed_ms=elapsed, error=error, data=data,
                            truncated=truncated)
        self._audit(task_id, tool, args, result, spec.version)
        return result

    def _audit(self, task_id: str, tool: str, args: dict,
               result: ToolResult, prompt_version: int) -> None:
        self.dao.insert_llm_run({
            "id": f"TC-{now_cn().strftime('%Y%m%d%H%M%S')}-{tool}",
            "task": task_id, "prompt_id": tool, "prompt_version": prompt_version,
            "model": "tool_gateway", "input_hash": _hash_args(tool, args),
            "output": result.to_dict(), "schema_ok": int(result.ok),
            "citations_ok": 0, "tokens": 0, "latency_ms": result.elapsed_ms,
        })
