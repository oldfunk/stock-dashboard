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


# 会被完整性检查覆盖的文案字段（机器字段如 benchmark/metric 是 ASCII 码，不查）
TEXT_FIELDS = ("statement", "prior", "kill_criteria")


def assert_readable_text(field: str, value: Any) -> None:
    """预注册卡片的文案必须**真的可读**，否则锁了就永远读不出。

    产线实况（2026-11-05 只读取证）：H-001 的 `statement` 39 个字符里 26 个是
    `?`（库里字节 `3F3F3F…4D4F53` = `??…MOS?`），`kill_criteria_json` 同样
    —— 也就是这张已锁定的预注册卡，**判据与终止条件在权威库里是乱码**。
    这是文案经过某个 ASCII 通道（errors=replace）留下的形状，不是应用逻辑错，
    但应用可以在唯一不可逆的那一步（lock）之前拦住它：
      - `??` 连续两个问号：中文被逐字替换的典型痕迹（单个 `?` 是正常英文问句，放行）；
      - U+FFFD 替换符：编码失败的另一种痕迹；
      - 代理残留（`\\udcXX`）：argv/文件系统按 ASCII surrogateescape 解码留下的，
        这种值连 UTF-8 都编不出来，进库后不可逆。
    拦下时报错要说清"哪一个字段、为什么"，让人能改——不是静默清洗成问号。
    """
    if isinstance(value, (list, tuple)):
        for i, item in enumerate(value):
            assert_readable_text(f"{field}[{i}]", item)
        return
    if not isinstance(value, str):
        return
    why = None
    if "??" in value:
        why = "出现连续问号（中文文案被 ASCII 通道逐字替换成 ? 的痕迹）"
    elif "\ufffd" in value:
        why = "出现 U+FFFD 替换符（上游解码失败）"
    else:
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            why = "含代理残留（argv/路径按非 UTF-8 解码，入库后不可恢复）"
    if why:
        raise HypothesisError(
            f"假设字段 {field} 不可锁定：{why}；当前值={value!r}。"
            "请用 UTF-8 通道重新录入（检查调用侧 locale / 传输层），"
            "锁定的预注册卡片必须能被人读出判据与终止条件")


def assert_hypothesis_readable(hyp: dict) -> None:
    """按 TEXT_FIELDS 逐个查（create/update/lock 三处共用）。"""
    for field in TEXT_FIELDS:
        if field in hyp and hyp[field] is not None:
            assert_readable_text(field, hyp[field])


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
        assert_hypothesis_readable(
            {"statement": statement, "prior": prior,
             "kill_criteria": kill_criteria})
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
        # 锁定是不可逆的那一步：文案读不出来就不许锁（产线 H-001 的判据与
        # kill_criteria 已是 ?，正是这一步没有闸留下的教训）
        assert_hypothesis_readable(row)
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
        assert_hypothesis_readable(merged)
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
