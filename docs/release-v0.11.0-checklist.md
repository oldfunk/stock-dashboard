# v0.11.0 发布检查单

> 目标：P2 面板收口（I1 基准 / I2 行业 / I3 成交+契约探针）+ 前序修复。
> 发布权归**子项目**；母项目只 `subtree pull <tag>`，不反向推分支、不代发 Release。
> 当前基线：v0.10.1（代码 `d486736` 之后），全量 1017 passed / 13 skipped。

## 1. 范围

- I1 CSI300 基准（`/api/benchmark`，超额/相对回撤）
- I2 行业分布（`/api/sectors`，未知桶/覆盖度）
- I3 成交记录（`/api/fills` + CSV）与运维页母项目契约（`/api/contract`）
- T104 母项目接口探针；T101 执行顺序；T102 动作终态门禁
- v0.10.1 第二实例面板修复（方案切换免口令、intents 降级）

**不包含**：I4 剩余项、I5+（多策略/健康度）、CSI300 之外的指数、自动除权。

## 2. 前置条件（全部满足才发）

| 条件 | 证据 |
|---|---|
| **3 个干净收盘链**（11-24 / 11-25 / 11-26） | `daily-check` 0 红；`action_funnel` 绿 |
| NAV 每日恰 1 行 | `nav_history` 按日计数 = 1；与 raw 复算一致 |
| 无丢单/反向单 | `agent_decisions` 无管道拒绝；`intent_chain` 绿 |
| 全量测试 | `python -m pytest -q` 全绿 |
| 面板三接口 | `/api/benchmark` `available`、`/api/sectors` 合计=持仓、`/api/contract` 三组 OK |
| 窄屏/深色 | 800px 页面无溢出；深色正常；0 console error |

## 3. 发布步骤

1. `paper_trading/__init__.py`：`0.10.1` → `0.11.0`；
2. 全量测试 + `node --check` 面板 JS；
3. commit `chore(release): v0.11.0`，push main；
4. annotated tag `v0.11.0`（含内容/测试/生产证据），push tag；
5. GitHub Release（由子项目发布；母项目不代发）；
6. 母项目（如需）：`git subtree pull --prefix=paper_trading paper-upstream v0.11.0 --squash`，生产 gate 后部署 :8081；
7. 独立版 :8080：`git pull --ff-only` + `systemctl restart paper-trading-dashboard`；
8. 生产验收：`daily-check`、`/api/benchmark`、`/api/sectors`、`/api/contract`、首页截图。

## 4. 证据表（发布前填）

| 日期 | NAV | daily-check | action_funnel | 备注 |
|---|---|---|---|---|
| 2026-11-24 | 94,881.9994 | 13绿 / 3黄 / 0红 | 绿（计划全 hold） | 无成交 |
| 2026-11-25 | 92,967.0736 | 16 / 2 / 0 | 绿（计划全 hold） | 条件/引擎卖 600989 700@19.40 |
| 2026-11-26 | 94,568.9761 | 14 / 3 / 0 | 绿（计划全 hold） | 卖 000792 100@21.52 |
| 2026-11-27 | 96,989.9604 | 16 / 2 / 0 | 绿（计划全 hold） | 买 002648 400@22.91；卖 000426 1400 / 603979 200 / 002558 500 |

**结论：4 个交易日 0 红，NAV 每日恰 1 行；超过 3 日门禁 → 2026-11-28 发布 v0.11.0。**

## 5. 回滚

- 代码回滚：生产 `git pull` 到上一个 tag `v0.10.1` 并重启面板；
- 不重写已发布 tag（`v*` 已有 ruleset 保护：禁删/禁移/禁强推）；
- 账本不回滚；若有成交异常，按事故流程单独处理并留痕。

## 6. 发布结果（2026-11-28）

- tag：`v0.11.0`（commit `2ce67ef`）；
- Release：https://github.com/oldfunk/paper-trading/releases/tag/v0.11.0
  （子项目自行发布，非母项目代发）；
- 生产 :8080：`2ce67ef`，dashboard active，`paper_trading.__version__ = 0.11.0`；
- 接口复核：`/api/benchmark` available（asof 11-27，超额 +3.40pp）、
  `/api/sectors` 覆盖 4/4 / 合计 37,604、`/api/contract` 三组 OK；
- 母项目 :8081 仍 v0.10.1，vendor 待母侧排期。
