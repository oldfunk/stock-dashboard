# report-t21.md — 迁移 runbook 审查（Hermes）

## 结论
runbook 主体可行，但有 1 个 P0 顺序缺陷 + 若干 P1 缺口，建议修正后再执行 Phase 1。

## P0（阻塞）
1. **回退顺序错误**（runbook Phase 2「异常回退」）：当前写
   `恢复 Pi cron → 停 NEC cron → Pi 接管（数据从 NEC 拷回）`。
   若先恢复 cron，Pi 会在旧 db 上跑一轮（分钟级 tick）再拷回 NEC 数据，造成
   中间状态与双活风险。必须改为：**先 scp NEC db → Pi，校验 COUNT/integrity，
   再恢复 Pi cron，最后停 NEC cron**。顺序铁律与 Phase 1 一致。

## P1
2. **数据一致性验证缺文件级校验**：仅 COUNT + integrity_check 不足以证明
   "没丢账本"。建议加 `sha256sum` 对比拷贝前/后 db 文件；并扩展到
   `risk_state`、`t1_freeze`、`positions` 表（当前只列 4 表）。
3. **本地覆盖配置未列入迁移对象**：Pi 上可能存在 `engine.local.yaml` /
   `strategy.local.yaml` / `secrets.local.json`（gitignored）。runbook 清单
   未提；建议 Phase 1 前 `find ~/paper-trading -name '*.local.*'` 并 scp。
4. **dashboard/LLM 凭证**：admin_token 存浏览器 localStorage，NEC 新浏览器
   需重新接管（用已存 API Key 保存一次）；runbook 应注明，避免迁移后面板
   写操作 401。
5. **Phase 0 冷备份路径未指定**：`./pi-backup-<date>/` 建议明确为开发机绝对路径。
6. **cron 清单已与 AGENTS.md 一致**（5 条 paper-trading + 1 条 stock-dashboard 留 Pi），
   无漏项。

## P2
7. **NEC venv Python 3.12 vs 项目历史 3.13**：requirements 仅 5 个包，Py3.12 可跑；
   但 rqalpha 选型（T13）需确认 Py3.12 whl 可用；建议 T20 记录最终版本。

## 建议
修正 P0 顺序后可执行；P1 项建议在 Phase 1 前补齐，不阻塞 Phase 0。
