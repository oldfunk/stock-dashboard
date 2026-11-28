# v0.11.0 — 面板 P2 收口 + 第二实例修复 + 可靠性治理

> 发布：2026-11-28（周六，非交易日）· 子项目自行打 tag/发 Release
> 上游：`v0.10.1` 之后 I1–I4 · 全量 **1017 passed / 13 skipped**

## 面板 P2（本次主交付）

- **CSI300 基准曲线**（`GET /api/benchmark`）：与 NAV 按首个共同交易日归一化，
  返回组合/基准/超额序列、基准 MDD、`asof/stale`；累计收益率模式叠加沪深300虚线，
  累计收益卡显示“超额 vs 沪深300”。
  - 生产 11-27：32 个共同交易日，组合 **−3.00%** vs 基准 **−6.40%**，
    **超额 +3.40pp**，基准 MDD 10.54%。
- **行业分布**（`GET /api/sectors`）：持仓市值按 `stock_snapshot.sector` 聚合，
  NULL→“未知”、未知排最后、>8 合并“其他”；生产 4 只持仓覆盖 4/4，
  合计 37,604 = 持仓市值。
- **成交记录**：交易页新增 `/api/fills` 表（时间/方向/股票/股数/价格/费用）+ CSV；
  缺表旧库降级为空列表。
- **母项目契约卡**（`GET /api/contract`，30s 缓存）：展示
  strategy/sector/benchmark 的 `ok/stale/asof/统计/问题`。

## 执行链与第二实例

- **T101 执行顺序**：卖 → 买 → hold；拒绝与卖出动作冲突的买入条件。
- **T102 动作终态门禁**：买卖单被执行管道丢弃 → daily-check 红（不再静默）。
- **第二实例面板**：内置方案 `mother/general`（不改指令）免口令；
  `custom`/改指令仍须口令；`/api/intents` 在无 `rq_intents` 表的旧线实例降级为空。
- **影子对账退役**：缺 `parity:diff` 由每日 warn 改为 skip；双轨交叉验证由
  `intent_chain`（意图↔账本）与 `action_funnel`（动作终态）承担。

## 可靠性 / 治理

- `main` 禁强推/禁删（`enforce_admins`）；`v*` tag ruleset（禁删/禁移/禁强推）。
- `data contract` 只读探针：策略/行业/基准的形状、取值、新鲜度；形状漂移 exit 3。
- 面板 P2 窄屏/深色检查：800px 页面无溢出；contract 687/687、fills 721/721 无内滚；
  0 console error。

## 生产观察（发布前置）

| 日期 | NAV | daily-check | 说明 |
|---|---|---|---|
| 11-24 | 94,882.00 | 13绿 / 3黄 / 0红 | 计划全 hold |
| 11-25 | 92,967.07 | 16 / 2 / 0 | 条件/引擎卖 600989 700@19.40 |
| 11-26 | 94,568.98 | 14 / 3 / 0 | 卖 000792 100@21.52 |
| 11-27 | 96,989.96 | 16 / 2 / 0 | 买 002648 400@22.91；卖 000426/603979/002558 |

4 个交易日 **0 红**，NAV 每日恰 1 行；`action_funnel` 无红（计划动作均 hold，
成交来自条件单/引擎）。

## 升级 / 兼容

- **无 `paper_account.db` / `data.db` schema 迁移**；旧库直接可用。
- 母项目：`git subtree pull --prefix=paper_trading paper-upstream v0.11.0 --squash`，
  生产 gate 后部署 :8081（本次不强制，按母侧排期）。
- 已知边界：`raw_gate` 母库参照滞后（warn）；`sh000922` 可能 stale；
  P5.5 等母库 `analysis_outcome` 回填。

## 测试

`python -m pytest -q` → **1017 passed / 13 skipped**。
