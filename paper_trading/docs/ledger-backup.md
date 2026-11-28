# 账本备份 / 恢复 / 版本化（B1）

> 适用：`paper_account.db`（现金/持仓/成交/NAV/风控状态/条件单/计划）。
> 原则：**先副本演练，再生产**；任何覆盖前自动安全副本；恢复后人工核对钱类口径。

## 1. 命令

```bash
# 只读体检（完整性/版本/表/行数/哈希）
python -m paper_trading.cli ledger inspect --json

# 冷备（sqlite backup API，不写当前账本）
python -m paper_trading.cli ledger backup --out-dir backups

# 冷备 + 只保留最近 20 份自动备份（安全副本/演练副本不参与轮转）
python -m paper_trading.cli ledger backup --out-dir backups --keep 20

# 最近备份的新鲜度/完整性（默认 26h）
python -m paper_trading.cli ledger backup-status --out-dir backups --json

# 恢复演练（默认 dry-run，只校验备份 + 对比行数）
python -m paper_trading.cli ledger restore --backup backups/paper_account.<stamp>.db

# 真恢复（会先自动生成 *.pre-restore.*.db 安全副本）
python -m paper_trading.cli ledger restore --backup backups/paper_account.<stamp>.db --apply

# 结构版本化（默认 dry-run；--apply 建 schema_meta / 补列）
python -m paper_trading.cli ledger migrate
python -m paper_trading.cli ledger migrate --apply

# 副本演练：备份 → 故意删一行 NAV → 从备份恢复 → 校验（只碰 work-dir）
python -m paper_trading.cli ledger drill --work-dir /tmp/ledger-drill
```

## 2. 恢复前置（生产）

1. 停掉 **dashboard / engine / agent cron / rq tick**（恢复期间不许有写入）；
2. `ledger inspect` 记录当前钱类基线：cash、持仓股数、fills/nav 行数、sha256；
3. `ledger backup` 先备当前账本（即使马上要覆盖）；
4. `ledger restore --backup ...`（dry-run 看行数差异）→ 确认 → `--apply`；
5. 恢复后 `ledger inspect`，逐项核对：
   - `account.cash`、`positions.total_volume/available_volume/avg_cost`；
   - `fills` 末笔与 `nav_history` 末日；
   - `op_log` 有 `ledger:restore` 留痕；
6. 重启服务，跑 `m3 daily-check` / 面板 `/api/health`。

## 3. 版本化

- `schema_meta.schema_version` 当前为 **1**；
- 低于当前版本：`ledger migrate --apply` 建表/补列并写 `op_log[ledger:migrate]`；
- 高于当前版本：拒绝迁移（避免旧程序写坏新结构），先升级程序；
- 迁移只做幂等结构变更，不碰余额/持仓/成交数值。

## 4. 回滚

- 恢复失败/核对不符：用恢复前的 `*.pre-restore.*.db` 再执行一次
  `ledger restore --backup <安全副本> --apply`；
- 不允许用 `cp` 覆盖 SQLite（WAL 半截页风险），一律走本工具；
- 演练记录（命令 + 行数 + 哈希 + 核对人/时间）写进运维日志，不写回账本。

## 5. 验收

- 副本 drill：备份行数 = 恢复后行数（`op_log` 会多一条 restore 留痕，属预期）；
- `PRAGMA integrity_check = ok`；
- 生产恢复后钱类口径人工核对通过，服务健康。

## 6. 自动备份（M4）

生产已用 `deploy/install-ops-cron.sh` 安装每日 21:10 `ledger backup --keep 20`
与 21:20 `ops check`（只读巡检）。`ledger backup-status` / `ops check` 会报
“备份超过 26h 未更新”或完整性失败；处理步骤见 `docs/ops-runbook.md`。
