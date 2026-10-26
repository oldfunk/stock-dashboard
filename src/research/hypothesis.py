"""假设卡（2026-10-27，R2-1）。

- 生命周期：draft（可改）-> locked（**不可改**，预注册）-> testing -> supported/rejected/inconclusive/retired。
- 锁定后不可修改：``update`` 对 locked 及之后的状态一律拒绝。
- CLI：``create`` / ``lock`` / ``view`` / ``list``。

用法::

    python -m src.research.hypothesis create --statement "MOS 有预测力" \\
        --prior value --universe A --signal '{"feature":"mos"}' \\
        --horizon-days 126 --benchmark sh000300 --metric excess \\
        --threshold ">0" --regimes all --kill-criteria "超额连续两年为负"
    python -m src.research.hypothesis lock H-20261027-001
    python -m src.research.hypothesis view H-20261027-001
    python -m src.research.hypothesis list --status locked
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from src.models.database import db_conn, now_cn
from src.research.dao import ResearchDAO
from src.research.domain import (
    Hypothesis, next_id, validate_transition,
)
from src.research.registry import HypothesisRegistry

LOCKED_STATES = ("locked", "testing", "supported", "rejected", "retired")


class HypothesisError(ValueError):
    """假设卡操作失败。"""


class HypothesisLockedError(HypothesisError):
    """假设已锁定，不可修改。"""


class HypothesisService:
    def __init__(self, dao: ResearchDAO | None = None,
                 registry: HypothesisRegistry | None = None):
        self.dao = dao or ResearchDAO()
        self.registry = registry or HypothesisRegistry(self.dao)

    # ── 注册 / 查看 ──────────────────────────────────────────────────────────
    def create(self, statement: str, prior: str, universe: str, signal: dict,
               horizon_days: int, benchmark: str, metric: str, threshold: str,
               regimes: list[str], costs: dict, kill_criteria: list[str],
               hypothesis_id: str | None = None, review_date: str = "",
               owner: str = "human") -> dict:
        with db_conn() as conn:
            existing = [r["id"] for r in conn.execute(
                "SELECT id FROM research_hypothesis")]
        hid = hypothesis_id or next_id("H", now_cn().strftime("%Y-%m-%d"), existing)
        hypothesis = Hypothesis(
            id=hid, version=1, prior=prior, statement=statement, universe=universe,
            signal=signal, horizon_days=horizon_days, benchmark=benchmark,
            metric=metric, threshold=threshold, regimes=regimes, costs=costs,
            kill_criteria=kill_criteria, status="draft", owner=owner,
            review_date=review_date)
        self.registry.register(hypothesis)
        return self.dao.get_hypothesis(hid)

    def view(self, hypothesis_id: str) -> dict:
        row = self.dao.get_hypothesis(hypothesis_id)
        if row is None:
            raise HypothesisError(f"假设不存在: {hypothesis_id}")
        return row

    def list(self, status: str | None = None) -> list[dict]:
        return self.dao.list_hypotheses(status)

    # ── 锁定 / 修改 ──────────────────────────────────────────────────────────
    def lock(self, hypothesis_id: str) -> dict:
        row = self.view(hypothesis_id)
        validate_transition("hypothesis", row["status"], "locked")
        with db_conn() as conn:
            conn.execute(
                "UPDATE research_hypothesis SET status = 'locked', locked_at = ? "
                "WHERE id = ?", (now_cn().isoformat(), hypothesis_id))
        return self.dao.get_hypothesis(hypothesis_id)

    def update(self, hypothesis_id: str, **changes: Any) -> dict:
        """仅 draft 可改；locked 及之后一律拒绝（预注册纪律）。"""
        row = self.view(hypothesis_id)
        if row["status"] in LOCKED_STATES:
            raise HypothesisLockedError(
                f"假设 {hypothesis_id} 已 {row['status']}，不可修改（预注册锁定）")
        allowed = ("statement", "prior", "universe", "signal", "horizon_days",
                   "benchmark", "metric", "threshold", "regimes", "costs",
                   "kill_criteria", "review_date", "owner")
        for key in changes:
            if key not in allowed:
                raise HypothesisError(f"不可修改字段: {key}")
        merged = {**row, **changes, "status": "draft"}
        for key in ("statement", "universe", "signal", "regimes", "kill_criteria"):
            if not merged.get(key):
                raise HypothesisError(f"假设字段不可为空: {key}")
        hypothesis = Hypothesis.from_dict(merged)
        self.dao.insert_hypothesis(hypothesis)
        return self.dao.get_hypothesis(hypothesis_id)

    def transition(self, hypothesis_id: str, to_status: str) -> dict:
        row = self.view(hypothesis_id)
        validate_transition("hypothesis", row["status"], to_status)
        with db_conn() as conn:
            conn.execute(
                "UPDATE research_hypothesis SET status = ? WHERE id = ?",
                (to_status, hypothesis_id))
        return self.dao.get_hypothesis(hypothesis_id)


def _parse_json(text: str) -> dict:
    try:
        return json.loads(text or "{}")
    except json.JSONDecodeError as exc:
        raise HypothesisError(f"JSON 非法: {exc}") from exc


def _parse_list(text: str) -> list:
    text = (text or "").strip()
    if not text:
        return []
    return [x.strip() for x in text.split(",") if x.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="research.hypothesis",
                                     description="Research OS 假设卡")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_create = sub.add_parser("create", help="注册假设（draft）")
    p_create.add_argument("--statement", required=True)
    p_create.add_argument("--prior", default="value")
    p_create.add_argument("--universe", default="A")
    p_create.add_argument("--signal", default="{}")
    p_create.add_argument("--horizon-days", type=int, default=126)
    p_create.add_argument("--benchmark", default="sh000300")
    p_create.add_argument("--metric", default="excess")
    p_create.add_argument("--threshold", default=">0")
    p_create.add_argument("--regimes", default="all")
    p_create.add_argument("--costs", default="{}")
    p_create.add_argument("--kill-criteria", default="")
    p_create.add_argument("--id", default=None)
    p_create.add_argument("--review-date", default="")

    p_lock = sub.add_parser("lock", help="锁定假设（之后不可改）")
    p_lock.add_argument("hypothesis_id")

    p_view = sub.add_parser("view", help="查看假设")
    p_view.add_argument("hypothesis_id")

    p_list = sub.add_parser("list", help="列出假设")
    p_list.add_argument("--status", default=None)

    args = parser.parse_args(argv)
    service = HypothesisService()

    if args.cmd == "create":
        row = service.create(
            statement=args.statement, prior=args.prior, universe=args.universe,
            signal=_parse_json(args.signal), horizon_days=args.horizon_days,
            benchmark=args.benchmark, metric=args.metric, threshold=args.threshold,
            regimes=_parse_list(args.regimes), costs=_parse_json(args.costs),
            kill_criteria=_parse_list(args.kill_criteria),
            hypothesis_id=args.id, review_date=args.review_date)
        print(row["id"])
    elif args.cmd == "lock":
        print(service.lock(args.hypothesis_id)["id"])
    elif args.cmd == "view":
        print(json.dumps(service.view(args.hypothesis_id), ensure_ascii=False, indent=2))
    elif args.cmd == "list":
        for row in service.list(args.status):
            print(f"{row['id']}\t{row['status']}\t{row['statement']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
