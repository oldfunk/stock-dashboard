# report-t26 — 每日 5 分钟线积累（D1 闭合的第一步，P1）

执行人：员工 AI（Qoder）｜2026-10-28｜分支 **`worker/t26`（含产品代码，待 review，不合并）**
**分支链**：`worker/t31` → `worker/t32` → **`worker/t26`**（ stacked，按顺序 review/合并）
先设计后实施（任务书要求）：§1-§3 是设计与实测，§4 是落地的代码。

> **一句话结论**：积累链路已通 —— 单日整根 48 根/标的落库、幂等重跑不翻倍、
> 缺口只报告不假装、周期（1/5/15/30）分道、保留窗口可关。
> 新浪 5 分钟源实测**每根都在**（3 只标的各 18 个交易日 × 48 根，无半根日），
> 但**回看只有约 18 个交易日** ⇒ 必须**每天攒**，缺口过期就补不回来（已实测确认）。
> 新增 10 条测试，全套 **397 passed, 4 skipped**（t32 基线 387+4 ⇒ 零回归）。

---

## 1. 设计输入：现状实测（只读，NEC 生产）

| 项 | 实测 | 结论 |
|---|---|---|
| `minute_bars` 存量 | **2,304 行 / 3 只标的**（000792 等），2026-09-18→2026-10-19，**48 根/标的/日** | 只有引擎日内顺带拉过的票才有，池内其余 35 只**根本没有分钟历史** |
| 表结构 | `symbol, ts, open, high, low, close, volume, amount` | **没有周期列** ⇒ 1 分钟与 5 分钟混在同一张表里无法区分（`paper_trading/data/minute.py:171-196` 两条路径都往同一张表写）——真实缺陷，本轮修 |
| 采集入口 | ① 引擎盘中 `runner.py:296-306`（只拉它关心的票）② CLI `minute pull`（默认 5 分钟）③ `fetch_minute_batch` 默认却是 `period="1"` | 三个入口默认值不一致，`minute pull` 与 `fetch_minute_batch` 打架（P2，本轮把 sync 路径的周期显式化） |
| 数据源能力 | `ak`/新浪 N 分钟：`fetch_minute(000792/600519/002648, period="5", days=30)` → **864 行 = 18 交易日 × 48 根**，每根齐全（`day-count set = [48]`，无半根日） | 周期网格与真实交易时段完全对齐（09:35…11:30 + 13:05…15:00）；**回看上限约 18 个交易日**（模块 docstring 写"22 天"，本轮实测 18，取更保守值） |
| 量级 | 合成 445,056 行（38 只 × 48 × 244 日）实测 **66.8 MB，150.2 B/行**（含主键索引） | 全池一天 = 1,824 行 ≈ **274 KB**；一年 ≈ **67 MB**。NEC `/` 分区 **367 GB 空闲** ⇒ 磁盘不是约束，但**默认仍要有开关**（CF 卡/新机部署时才会咬人） |

## 2. 设计决定

1. **周期标签**：`minute_bars.period TEXT DEFAULT '5'`。
   默认值取 `'5'` 是**如实**而非省事：现网 2,304 行全部来自 `runner.py` 的
   `fetch_minute(..., period="5")` 路径（48 根/日 与 5 分钟网格吻合），
   而 `minute pull` 的默认周期也是 5。迁移用例锁死这件事。
2. **每日攒、不指望回填**：源的 18 个交易日硬上限 ⇒ 任何"漏一天补一天"超过
   约 3 周就不可行。`sync_day()` 因此**只报告缺口**（`gapped`/`no_data`/`errors`），
   并把它写进 `op_log[minute:gap]` 留痕（AGENTS 纪律 4：不许静默）。
3. **幂等**：`INSERT … ON CONFLICT(symbol,ts) DO UPDATE`（已有），重跑同日同周期
   不翻倍、值取最新；`period` 参与写入，所以 1 分钟与 5 分钟同 `ts` 也各归其行
   ——**注意**：主键仍是 `(symbol, ts)`，跨周期同刻（如 `09:35` 的 1 分钟与 5 分钟）
   会互相覆盖！本轮实测确认并把风险写进 §5-①（要根治需把 `period` 进主键，
   那是重建表级别的改动，我不在 P1 任务里擅自动刀）。
4. **保留窗口**：`prune_minute_bars(before_day, period)` + CLI `--retention-days N`
   （按"已入库交易日"倒数取切点，不依赖日历，删不到没攒过的日期）。
   **默认 0＝不删**（磁盘现状允许）；开关留在运维手里。
5. **口径**：分钟线本就是**原始价**（新浪给的是交易所实时价，无复权）。
   T31 已给 `adjust='raw'` 标签，本轮用两个标的实测印证：
   000792 最后一根 5 分钟收盘 **22.10** = 当日 raw 日收 **22.10**；
   600519 **1330.75 = 1330.75** ⇒ 引擎的分钟口径闸可复用同一套标签。
6. **调度**：`minute sync` 设计成收盘后一条 cron 命令即可（**我不动 cron**，
   给出建议行由主 AI 决定）：
   ```
   30 15 * * 1-5 cd /home/nec/paper-trading && venv/bin/python -m paper_trading.cli \
       minute sync --pool-limit 60 --period 5 --json >> minute.log 2>&1
   ```
   与既有 `agent tick`（盘中每 5 分钟）不冲突：盘中那份只服务日内规则，
   `sync` 负责**全池**收盘后补齐。

## 3. 缺口/容量算法

- 期望网格：`expected_slots(period)` = 上午 09:31..11:30 + 下午 13:01..15:00
  中 `(m - 09:30) % period == 0` 或段尾（11:30/15:00）→
  实测 **1→240 / 5→48 / 15→16 / 30→8** 根/日；
- `sync_day()` 返回 `{written, expected_per_symbol, per_symbol{missing,missing_sample},
  no_data, gapped, errors}`；
- 覆盖概览 `minute_coverage(period)`：行数/标的数/首末 ts；
- 容量：`retention_days=0` 时全年 67 MB；`retention_days=500`（约 2 年）封顶 ≈134 MB。

## 4. 实施内容（`worker/t26`）

| 文件 | 改动 |
|---|---|
| `paper_trading/data/db_manager.py` | `minute_bars` 加 `period` 列（默认 `'5'`，迁移用例锁定）；`upsert_minute_bars(..., period="5")`；`get_minute_bars(..., period=None)` 可按周期筛；新增 `minute_days / minute_ts_on / minute_coverage / prune_minute_bars` |
| `paper_trading/data/minute.py` | 新增 `SESSION_START_MIN / _trading_minutes() / expected_slots() / sync_day()`（`fetch` 可注入，测试零网络） |
| `paper_trading/engine/runner.py` | 盘中写库改为显式 `period="5"`（不再依赖默认值） |
| `paper_trading/cli.py` | `minute sync`（`--day/--period/--days/--retention-days`）；`minute pull` 传 `period`；`minute stat` 增加 `coverage/days`；缺口写 `op_log[minute:gap]` |
| `tests/test_minute_accumulate.py` | 10 条：网格计数、老库迁移标签、周期隔离、幂等重跑不翻倍、缺口报告、单只失败不拖垮整批、无数据标记、保留窗口、跨周期不误删 |

## 5. 遗留风险（要主 AI 拍板的，我没擅自动）

1. **主键缺 `period`（真实数据损坏风险）**：同一天 `09:35` 的 1 分钟 bar 与 5 分钟 bar
   会互相覆盖（`ON CONFLICT(symbol,ts)`）。根治 = 重建表把 `period` 进主键
   （SQLite 需要新建表 + 搬数据），属钱类级别的数据层改动 ⇒ 我**只报告不动手**。
   现状规避：`minute pull --period 1` 与 `sync --period 5` 不要混用同一 `ts` 段
   （1 分钟路径只在当日盘中用，且腾讯分时源只给当日）。
2. **回看只有约 18 个交易日**（实测，docstring 的 22 天偏乐观）：
   错过一天就要在 3 周内补，否则永久缺失 ⇒ 建议 `sync` 的 cron **失败要报警**
   （可并入 T22 心跳：新增 `minute` 心跳项，我没有擅自加）。
3. **`fetch_minute_batch` 默认 `period="1"` 与 `minute pull` 默认 `5` 不一致**：
   保留原样（改默认值影响现网调用者），但 `sync` 路径已全部显式传参。
4. **停牌/半日市**会产生少于 48 根的合法缺口：目前计入 `missing`，
   是否降级为"可接受"需要 T31 的 `stock_pool`/停牌信息配合（M2 后可做）。

## 6. 复现与真机实测

单测（不需要网络）：

```bash
python -m pytest tests/test_minute_accumulate.py -q      # 10 passed
python -m pytest tests -q                                 # 397 passed, 4 skipped
```

**真机 CLI 实测**（NEC 生产 venv + 真新浪源；代码从我的分支拷到 `/tmp/t26run`，
库写在 `/tmp/t26_probe.db`，**不碰生产 data.db / paper_account.db**，用完即删）：

| 场景 | 实测输出 |
|---|---|
| `minute sync --day 2026-10-28 --symbols 000792 600519 --period 5` | 每只 `written=48, bars=48, missing=0`；`gapped=[] errors=[]`；`coverage.rows=96` |
| 同日**重跑**（幂等） | `written=96`（再次全量落库），`coverage.rows` 仍 **96** ⇒ 不翻倍 |
| `--day 2026-01-05`（超出源回看约 18 个交易日） | `written=0`，`no_data=[000792]`、`gapped=[000792]` ⇒ **只报告不假装**，且写 `op_log[minute:gap]` |
| `minute stat --period 5` | `coverage.rows=96`、`days=1` |
| `--day 2026-10-27 --retention-days 1` | `pruned=48`（只保留最近 1 个已入库交易日），`coverage.rows=96` 保持 |

真实源核对（只读）：`fetch_minute(000792/600519/002648, period="5", days=30)`
→ 各 **864 行 = 18 交易日 × 48 根**，`day-count set = [48]`（无半根日）。


## 7. D1 闭合还差什么

任务书说"攒够回测窗口后回测线可升分钟频率"。本轮交付的是**积累能力**；
升频回测还需：① 连续 3 个月以上的分钟历史（按现在 1,824 行/日，约 60 个交易日
即可攒够一次 walkforward 的样本外窗口）；② `engine/backtest.py` 的分钟回测口径
（rqalpha `frequency=1m` 需要分钟 store，T32 适配器目前只做 `1d`）；
③ 分钟级 T+1/涨跌停判定与日线一致性（M2 稳定后的独立任务）。
