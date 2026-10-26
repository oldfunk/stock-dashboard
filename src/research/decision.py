"""Research OS 决策账本（2026-10-26，R0-7）。

职责：
- 记录"改什么 / 为什么 / 证据 ID / 预期 / 复核日"（``decision_log``）。
- 策略/假设/部署状态变更**必须带决策记录**（``require`` 守卫）。
- 关闭时写结果（``outcome``）。
- CLI：``create`` / ``close`` / ``list`` / ``require`` / ``transition``。

用法::

    python -m src.research.decision create --type promote --target S-BASE-v0 \\
        --change "进入纸盘" --rationale "实验通过" \\
        --evidence E-20261026-001=supported --expected "3 个月一致" \\
        --review-date 2027-01-31
    python -m src.research.decision transition strategy S-BASE-v0 paper
    python -m src.research.decision close DL-20261026-001 --outcome "已晋升"
"""

from __future__ import annotations

import argparse
from datetime import date

from src.models.database import db_conn, now_cn
from src.research.dao import ResearchDAO
from src.research.domain import (
    Decision, DecisionStatus, next_id, validate_transition,
)


class DecisionRequiredError(ValueError):
    """决策缺失或状态变更不受支持。"""


# 实体 -> 研究账本表
ENTITY_TABLE = {
    "hypothesis": "research_hypothesis",
    "strategy": "strategy_version",
    "deployment": "strategy_deployment",
}


def parse_evidence(text: str) -> dict[str, str]:
    """解析 ``E-1=supported,E-2=rejected`` 形式的证据引用。"""
    out: dict[str, str] = {}
    for part in (text or "").split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise DecisionRequiredError(f"证据格式应为 key=value: {part!r}")
        key, value = part.split("=", 1)
        out[key.strip()] = value.strip()
    return out


class DecisionService:
    def __init__(self, dao: ResearchDAO | None = None):
        self.dao = dao or ResearchDAO()

    def _next_id(self, day: date | str) -> str:
        # next_id 内部已归一化日期（date / YYYYMMDD / ISO 均可）
        with db_conn() as conn:
            existing = [r["id"] for r in conn.execute("SELECT id FROM decision_log")]
        return next_id("DL", day, existing)

    # ── 记录 / 关闭 ──────────────────────────────────────────────────────────
    def record(self, type: str, target: str, change: str, rationale: str,
               evidence: dict[str, str] | None = None, expected: str = "",
               review_date: str = "", decision_date: str | None = None,
               status: str = DecisionStatus.OPEN.value) -> Decision:
        if not (type and target and change and rationale):
            raise DecisionRequiredError(
                "决策缺少必填字段: type/target/change/rationale")
        day = decision_date or now_cn().strftime("%Y-%m-%d")
        decision = Decision(
            id=self._next_id(day), decision_date=day, type=type, target=target,
            change=change, rationale=rationale, evidence=evidence or {},
            expected=expected, review_date=review_date, status=status)
        self.dao.insert_decision(decision)
        return decision

    def close(self, decision_id: str, outcome: str) -> dict:
        current = self.dao.get_decision(decision_id)
        if current is None:
            raise DecisionRequiredError(f"决策不存在: {decision_id}")
        validate_transition("decision", current["status"],
                            DecisionStatus.CLOSED.value)
        with db_conn() as conn:
            conn.execute(
                "UPDATE decision_log SET status = ?, outcome = ? WHERE id = ?",
                (DecisionStatus.CLOSED.value, outcome, decision_id))
        return self.dao.get_decision(decision_id)

    # ── 守卫 ────────────────────────────────────────────────────────────────
    def require(self, entity_type: str, entity_id: str,
                decision_id: str | None = None) -> dict:
        """校验该实体存在可引用的决策记录；缺失则拒绝。"""
        if entity_type not in ENTITY_TABLE:
            raise DecisionRequiredError(f"未知实体: {entity_type}")
        decisions = [d for d in self.dao.list_decisions_by_target(entity_id, limit=500)
                     if d["status"] in (DecisionStatus.OPEN.value,
                                        DecisionStatus.REVIEWED.value)]
        if decision_id:
            decisions = [d for d in decisions if d["id"] == decision_id]
        if not decisions:
            raise DecisionRequiredError(
                f"{entity_type} {entity_id} 状态变更缺少决策记录")
        return decisions[0]

    def transition(self, entity_type: str, entity_id: str, to_status: str,
                   decision_id: str | None = None) -> dict:
        """带决策记录的状态变更：校验迁移合法 + 必须有决策 + 落库。"""
        if entity_type not in ENTITY_TABLE:
            raise DecisionRequiredError(f"未知实体: {entity_type}")
        table = ENTITY_TABLE[entity_type]
        with db_conn() as conn:
            row = conn.execute(
                f"SELECT status FROM {table} WHERE id = ?",
                (entity_id,)).fetchone()
        if not row:
            raise DecisionRequiredError(f"{entity_type} 不存在: {entity_id}")
        validate_transition(entity_type, row["status"], to_status)
        decision = self.require(entity_type, entity_id, decision_id)
        if entity_type == "hypothesis":
            self.dao.update_hypothesis_status(entity_id, to_status)
        elif entity_type == "strategy":
            self.dao.update_strategy_status(entity_id, to_status)
        else:
            self.dao.update_deployment_status(entity_id, to_status)
        return {"entity": entity_type, "id": entity_id, "status": to_status,
                "decision": decision["id"]}


# ── CLI ─────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="research.decision", description="Research OS 决策账本")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_create = sub.add_parser("create", help="记录决策")
    p_create.add_argument("--type", required=True)
    p_create.add_argument("--target", required=True)
    p_create.add_argument("--change", required=True)
    p_create.add_argument("--rationale", required=True)
    p_create.add_argument("--evidence", default="")
    p_create.add_argument("--expected", default="")
    p_create.add_argument("--review-date", default="")
    p_create.add_argument("--date", default=None)

    p_close = sub.add_parser("close", help="关闭决策并写结果")
    p_close.add_argument("decision_id")
    p_close.add_argument("--outcome", required=True)

    p_list = sub.add_parser("list", help="列出决策")
    p_list.add_argument("--status", default=None)

    p_require = sub.add_parser("require", help="校验状态变更是否有决策记录")
    p_require.add_argument("entity_type")
    p_require.add_argument("entity_id")
    p_require.add_argument("--decision", default=None)

    p_transition = sub.add_parser("transition", help="带决策记录的状态变更")
    p_transition.add_argument("entity_type")
    p_transition.add_argument("entity_id")
    p_transition.add_argument("to_status")
    p_transition.add_argument("--decision", default=None)

    args = parser.parse_args(argv)
    service = DecisionService()

    if args.cmd == "create":
        decision = service.record(
            args.type, args.target, args.change, args.rationale,
            parse_evidence(args.evidence), args.expected, args.review_date,
            args.date)
        print(decision.id)
    elif args.cmd == "close":
        print(service.close(args.decision_id, args.outcome)["id"])
    elif args.cmd == "list":
        for d in service.dao.list_decisions(args.status):
            print(f"{d['id']}\t{d['decision_date']}\t{d['status']}\t"
                  f"{d['type']}\t{d['target']}\t{d['change']}")
    elif args.cmd == "require":
        print(service.require(args.entity_type, args.entity_id,
                              args.decision)["id"])
    elif args.cmd == "transition":
        print(service.transition(args.entity_type, args.entity_id,
                                 args.to_status, args.decision))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
