# 运维巡检与自动备份（M4）

> 目标：账本每天有可恢复的备份；心跳、行情、熔断异常能在 cron 日志里看见。
> 全部只读检查；唯一写操作是生成备份文件，不碰账户余额/持仓。

## 1. 命令

```bash
# 冷备 + 只保留最近 20 份自动备份（安全副本/演练副本不参与轮转）
python -m paper_trading.cli ledger backup --out-dir backups --keep 20 --json

# 最近备份是否新鲜/完整（默认 26 小时）
python -m paper_trading.cli ledger backup-status --out-dir backups --json

# 一次巡检：备份 / 心跳 / raw_gate / 熔断
python -m paper_trading.cli ops check --json
```

`ops check` 输出 `ok=false` 时退出码为 1，可直接挂 cron；同时写
`var/ops_status.json` 供面板/后续告警读取。检查项：

| id | 判据 |
|---|---|
| `backup` | 存在最近自动备份、`PRAGMA integrity_check=ok`、年龄 ≤26h |
| `heartbeat` | engine/agent/rq_tick/minute 在应跳窗口内未停滞 |
| `quote` | `raw_gate`：本地 raw 新鲜 + 母库逐日参照已翻转 |
| `qfq` | 本地 vs 母库前复权同日比值漂移/尺度不一致（`data qfq-check`） |
| `risk` | `drawdown_halt` / `daily_loss_halt` 是否触发 |

## 2. 安装 cron（生产）

```bash
cd /home/nec/paper-trading
bash deploy/install-ops-cron.sh --check
bash deploy/install-ops-cron.sh --install
```

只增删带 `pt-ops-backup` / `pt-ops-check` 标记的两行：

- `10 21 * * *`：`ledger backup --keep 20`
- `20 21 * * *`：`ops check`

日志：`logs/backup.log`、`logs/ops.log`。不碰其它 cron。
备份文件与目录在 POSIX 下收紧为 `600` / `700`（Windows 无 POSIX mode，忽略）。

## 3. 告警处理

- `backup=red`：看 `logs/backup.log`；确认 `backups/` 可写、磁盘空间足够；
  必要时手动跑 `ledger backup --keep 20`。
- `heartbeat=red`：`health show`；检查 crontab/systemd；参考 `docs/migration-m3.md`。
- `quote=red`：`data raw-coverage --gate`；先补 `data fetch-raw`，再看母库参照
  （`flip_watch.waiting_days` 是母库侧，`unflipped_days` 是我们没重跑）。
- `qfq=warn`：`data qfq-check --json`；看 `anchor_shift` 的日期与
  `max_dev`，先跑 `data repair-qfq --symbols ... --json`（dry-run）确认本地是否需要重拉，
  再 `--apply`；两边锚点谁新谁旧需人工核对，**不自动改数据**。
  `qfq=red`（scale_mismatch）优先。若只是母库回填滞后，提交/跟踪母侧问题。
  2026-11-28 实测：`anchor_shift` 也可能是 **akshare 与母库 qfq 锚点差异**，
  执行侧 `auto` 用母库、本地仅兜底；`repair-qfq` 修复本地后仍 warning 属预期。
- `risk=red`：查 `risk_state` 与 `ai:decide` 拒因；**不要手工改峰值/基线**，
  按事故流程核对钱类口径。

## 4. 回滚

```bash
# 移除两条 cron（脚本会保留其它行）
crontab -l | grep -v 'pt-ops-backup' | grep -v 'pt-ops-check' | crontab -
```

备份文件按文件名 `<stem>.<YYYYmmddTHHMMSS>.db` 轮转；`*.pre-restore.*.db`
和 `*.drill.db` 永不被自动删除。恢复步骤见 `docs/ledger-backup.md`。
