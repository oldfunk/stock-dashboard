# v0.12.0 发布检查单

> 目标：策略组合 + 审计/报告 + 运维自动化 + 数据校准。
> 发布权归子项目；母项目只 `subtree pull <tag>`。
> 发布：**2026-11-28**（用户决定加速；post-release 观察 11-30/12-01）。
> 基线 **1107 passed / 13 skipped**。

## 1. 范围

- I5a 策略健康 / I5b 默认 MA20/60 / I5c-1/2/3（上下文、health_policy、资金额度）
- A4 回放风格审计 / A5 日周报 / A6 默认对照 / A7 四层边界文档
- B1 备份恢复/版本化 / B2 逐日审计 / B3 安全复核 / B4/B5 公司行为建议
- C1 AI 成绩校准 / C3 `daily_close` 补 raw 参照
- M4 运维巡检 + cron；面板 AI 成绩/额度/巡检卡
- **不包含**：撮合细化、母项目 vendor、C5 历史 NAV 缺口回填

## 2. 前置条件（全部满足才发）

| 条件 | 证据 / 命令 |
|---|---|
| 12-01 收盘链 0 红 | `deploy/daily-check.sh`；`action_funnel` 绿 |
| NAV 每日恰 1 行 | `nav_history` 按日计数 = 1 |
| 无丢单/反向单 | `agent_decisions` 无管道拒绝；`intent_chain` 绿 |
| 新默认策略首个计划 | 12-01 16:30 生成、12-02 执行；`plan` 带 strategy/health/scorecard |
| 全量测试 | `bash deploy/release-gate.sh`（含 pytest/ops/daily-check/qfq） |
| 面板 | `/api/ai-scorecard`、`/api/strategy-allocations`、`/api/ops-status`、`/api/report`、`/api/audit` 200；0 console error；800px 无横向溢出 |
| 运维 | `ops check` 0 red；`ledger backup-status` ok；cron 两条存在 |
| 钱类 | 发布前后 cash/持仓股数/fills/nav 行数一致（记录快照） |
| 母库 | `data contract` ok；`raw-coverage --gate` 0 violations；`qfq-check` 无 red（anchor warn 需记录） |

## 3. 发布步骤

1. `paper_trading/__init__.py`：`0.11.0` → `0.12.0`；
2. 更新 `docs/ROADMAP.md` 当前版本/进度、`README` 版本相关文案；
3. `bash deploy/release-gate.sh`（GO/NO-GO；含全量/ops/daily-check/qfq）；
4. 全量测试 + `node --check` 面板脚本；
4. commit `chore(release): v0.12.0`，push main；
5. annotated tag `v0.12.0`（含范围/测试/生产证据），push tag；
6. GitHub Release（子项目发布）；
7. 母项目：`git subtree pull --prefix=paper_trading paper-upstream v0.12.0 --squash`，
   生产 gate + 重启 :8081；
8. 独立版 :8080：`git pull --ff-only` + `systemctl restart paper-trading-dashboard`；
9. 生产验收：`ops check`、`daily-check`、三接口、首页截图。

## 4. 证据表（发布前填）

| 日期 | NAV | daily-check | action_funnel | 备注 |
|---|---|---|---|---|
| 2026-11-28 | 96,989.9604 | 10绿/5黄/0红 | 待跑 | 部署 + ops 巡检 + release-gate GO（skip tests；非交易日） |
| 2026-11-30 |  |  |  | post-release 观察：旧计划执行 + 新默认计划 16:30 生成 |
| 2026-12-01 |  |  |  | 新默认计划 09:30 执行；post-release 关键观察日 |

**结论：2026-11-28 按用户“加速”决定发布；11-30/12-01 继续观察，异常按回滚/事故流程处理。**

## 5. 回滚

- 代码回滚：生产 `git checkout v0.11.0` 并重启；不重写已发布 tag；
- 账本不回滚；若有成交异常，按事故流程单独处理并留痕；
- `ops` cron 可单独移除：
  `crontab -l | grep -v 'pt-ops-' | crontab -`。

## 6. 发布结果（未发布）

- tag：—
- Release：—
- 生产 :8080：`9a5dbfd`（v0.12 开发批次，未 bump）
- 母项目 :8081：v0.10.1（待 vendor）
