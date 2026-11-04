# report-t35 — `minute_bars` 主键重建（钱类数据层，P0，§5.5 裁决 6）

执行人：员工 AI（Qoder）｜2026-10-29｜分支 `worker/t35`（含产品代码，**待 review 不合并**）
独立分支（基于 main，不依赖未合并的 t34）。

> **一句话结论**：`(symbol, ts)` → **`(symbol, ts, period)`** 的在线重建已实现，
> 并在**生产 data.db 的真实副本（2,304 行）上演练通过**：一行不丢、先落备份、
> 旧结构留在备份里可回滚、生产库本体未被碰过。
> 新增 7 条迁移测试，全套 **404 passed, 4 skipped**（基线 397+1，零回归）。

---

## 1. 为什么这是钱类问题（拆的是什么炸弹）

旧主键 `(symbol, ts)` 下，同一时刻的 1 分钟与 5 分钟 bar 是**同一行**：
`upsert_minute_bars` 的 `ON CONFLICT` 会**静默改写**先写入的那条。
`minute.py` 有两条写入路径（新浪 N 分钟 / 腾讯 1 分钟分时），
`runner` 盘中写 5 分钟、`minute pull --period 1` 写 1 分钟 ⇒
只要有人在同一天两边都拉，日内规则和分钟回测就会拿到**被换过的价**，
而且没有任何报错。裁决 6 要求"现在拆"，拆法只能是主键加维度。

## 2. 迁移实现（`data/db_manager.py`）

`_init_schema` 末尾调用 `_migrate_minute_pk(conn)`，顺序是硬的：

1. **判需不需要**：`PRAGMA table_info` 里 `period` 已在主键 ⇒ 直接跳过（幂等）；
2. **先备份**：`<db>.pre-t35.bak`（`conn.backup()`，已存在则不覆盖，保留第一次现场）；
3. **建新表 + 整表搬运**：`minute_bars_t35`，缺列补默认（`adjust='raw'`、`period='5'`）；
4. **行数核对**：`新 != 旧` ⇒ 抛 `RuntimeError`，由 `_connect()` 回滚，**旧表原样保留**；
5. `DROP` 旧表 → `RENAME` → 重建 `idx_minute_symbol_ts`。
   整个过程在同一个事务里（SQLite 的 DDL 参与事务），异常不留半成品。

新库直接按新结构建表（含 `adjust/period` 列），不会再产生需要迁移的老库。

读写侧同步收紧：`upsert_minute_bars` 的冲突目标改成 `(symbol, ts, period)`；
`engine/runner._today_minutes` 与 `engine/backtest.load_minutes` 的读取**显式声明 `period="5"`**
——主键有了周期，读侧再不说周期就等于把两条粒度混着喂给规则，
违反 `minute.py` 自述的"刻意不混粒度"约定。

## 3. 生产真实副本演练（NEC，只读源码 + 副本，跑完删）

```
before  rows=2304  pk=['symbol','ts']  symbols=3        # 生产 data.db 的 /tmp 副本
INFO    minute_bars 主键已升级为 (symbol, ts, period)：2304 行迁移完成，
        备份见 t35_rehearsal.db.pre-t35.bak
after   pk=['symbol','ts','period']  total=2304
        coverage={period:'5', rows:2304, symbols:3, first:'2026-09-18T09:35', last:'2026-10-19T15:00'}
backup  exists=True size=7,090,176  rows=2304  pk=['symbol','ts']    # 旧结构在，可回滚
生产库   /home/nec/paper-trading/data.db  mtime 14:59 未变             # 没碰现网
```

现网 minute 数据实况（顺带核到）：**只有 3 只**有分钟线——
`000858 / 002192 / 600036`，各 **768 行 = 48 根 × 16 个交易日**，
symbol 一律 6 位数字（与 `daily_bars` 同格式，无格式分裂问题）。
演练脚本里我一开始拿 `000792` 探数，打印 0 行是**我选错了标的**，不是数据丢——
`minute_coverage`/`total` 两处都是 2,304 行一致。

## 4. 测试（`tests/test_minute_pk_migration.py`，7 条）

| 测试 | 锁住什么 |
|---|---|
| `test_migration_keeps_rows_and_upgrades_pk` | 主键升级 + 5 行原样 + 顺序/adjust 不变 |
| `test_migration_writes_backup_file_first` | 备份文件存在、**里面还是旧主键**、行数不丢（可回滚） |
| `test_migration_is_idempotent` | 二次打开不重复迁移（备份 mtime 不变）、结构仍正确 |
| `test_1min_and_5min_coexist_after_migration` | **覆盖风险回归**：同刻两种周期各自成行、互不覆盖 |
| `test_upsert_still_idempotent_per_key` | 同 `(symbol, ts, period)` 重跑是更新不是新增 |
| `test_prune_and_coverage_after_migration` | T26 的保留窗口/覆盖率在迁移库上照常工作 |
| `test_fresh_db_already_has_new_pk` | 新库建表即新结构，且不会多出备份文件 |

## 5. 运维注意（给主 AI，我没动生产）

1. 生产 `data.db` 一旦由带本分支的进程打开，就会**自动完成迁移并留下 `.pre-t35.bak`（≈7 MB）**。
   如果想避免"任何一次打开都可能迁移"，可把 `_migrate_minute_pk` 改成只由
   `data migrate-minute-pk` 显式触发——**要哪种请说**，我按显式改也就 10 行；
   默认选择自动，是因为裁决 6 说"定时炸弹现在拆"，留在代码里等人记得跑更危险。
2. 备份文件确认无误后可删（`rm ~/paper-trading/data.db.pre-t35.bak`）；迁移不会自己删它。
3. 迁移完成后，T26 的 `minute sync` 建议立刻跑一次全池：现在表里只有 3 只标的的分钟数据。
4. 我没做的：1 分钟数据的实际采集验证（`minute.py` 的腾讯分时路径只给当日，
   与 T34 发现的"日 K 收盘后延迟发布"是两类时效问题）。

## 6. 复现

```bash
python -m pytest tests/test_minute_pk_migration.py -q     # 7 passed
python -m pytest tests -q                                  # 404 passed, 4 skipped

# 生产副本演练（NEC，用完即删）
cp ~/paper-trading/data.db /tmp/rehearsal.db
python -c "import sys;sys.path.insert(0,'.');from paper_trading.data.db_manager import DataDBManager;DataDBManager('/tmp/rehearsal.db')"
```
