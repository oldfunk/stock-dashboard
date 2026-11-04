# report-t34 — §5.5 裁决 1+2 落地：母库口径标签如实 + 当日跨源对照 + T+1 翻转停线

执行人：员工 AI（Qoder）｜2026-10-29｜分支 `worker/t34`（含产品代码，**待 review 不合并**）
依据：`refactor-plan` §5.5 裁决 1（标签错）与裁决 2（当日无独立参照 → A+B 同做）
测试：**新增 21 条**，全套 **418 passed, 4 skipped**（合并后基线 397+1，零回归）

> **一句话结论**：三条都落了并且**在生产机上真跑过**——
> ① 母库 `adj` 标签不再谎称 `raw`（兜底列实测就是前复权），并有参数化测试锁死；
> ② 当日跨源对照（akshare 日 K vs 腾讯实时价，收盘后、容差 0.5%）已进管道；
> ③ G-T13-1 增补判据落成可审计的 `raw_flip_watch()`，
>    并且**把"母库还没给参照"与"我们没重跑对照"分开**：前者只告警，后者才停线。
> 真跑还暴露了一条我原先没想到的 operational 真相：**15:09 收盘后新浪当天日 K 还没出**，
> 跨源对照会空跑——现在这变成显式计数 `intraday_no_today_bar`，不再假装"已校过"。

---

## 1. 裁决 1：母库口径标签（`integration/mother.py`）

| 位置 | 改动 |
|---|---|
| 模块 docstring（原"母库提供不复权/后复权"） | 改为如实：`open/high/low/close` **四列都是前复权**（10,548,933 行 `close==close_qfq` 零例外，与 `close_raw` 不等占 80.6%），不复权只有 `close_raw/volume/amount` |
| `daily_bars()` 降级链注释 | `qfq → hfq → 不复权 close` 改成 `qfq → hfq → qfq(兜底列)`，并注明"真要不复权请读 `close_raw`" |
| `adj` 标签 | 原来 `close_qfq`/`close_adj` 都为空时报 `"raw"` ⇒ 改为按**实际取到的列**报 `"qfq"`（纪律 4：标签是事实陈述） |

**对 §5.5 措辞的一处事实校正（照实说）**：裁决写"21 行 `adj='raw'` 错标"。
生产母库实测 `close_qfq` 的 NULL 行数是 **0**（`close_raw`/`close_adj` 各 21 行为 NULL）
⇒ 那条 `adj="raw"` 分支在**当前生产数据下不可达**，不是"已经标错了 21 行"，
而是"**一旦母库回填状态变化就会标错**"的潜在缺陷。我按裁决改了逻辑并加了锁死测试，
但请留意这个差别（结论不变：必须改）。

测试：`test_mother_adj_label_matches_actual_column` 三种列组合参数化
（qfq 有 → `qfq`；只 hfq → `hfq`；两者都无、走兜底列 → `qfq` 而非 `raw`）。

## 2. 裁决 2-A：当日跨源对照（`data/raw_feed.py`）

- `intraday_reference(symbols, quotes=…)`：取**腾讯实时快照**（与 akshare/新浪不同源，
  满足事故复盘的"独立基准原则"），只保留 `price>0` 的标的。
- `check_raw_bars(..., intraday=…, day=…, enforce_intraday=True)`：
  只对 **`bar.date == day`** 那根生效，与实时价比 `RAW_CLOSE_TOLERANCE=0.5%`，
  超差 → `mismatches[why="intraday"]` → **整只拒收**（与母库分歧同等处理）。
- 只有收盘后才强制（`session_closed()`）：盘中实时价本来就 ≠ 收盘价，
  硬比会天天误拒——实测 `14:30 → False / 15:20(=UTC 07:20) → True`。
- **时区坑（真跑出来的）**：`session_closed()` 最初用机器本地钟。生产机时区可能是
  UTC/HKT，用本地钟会让当日对照"看起来跑了其实没跑"。现在规则是：
  不传 `now` ⇒ 取 UTC 现在折北京时间；传 naive ⇒ 视为北京墙钟；传 aware ⇒ 折算。
- **新暴露的 operational 事实**：15:09（HKT，生产实测）新浪当天日 K **尚未发布**，
  于是跨源对照没有可比对象。原来这会静默成 `intraday_checked=0`，
  现在明确报 `intraday_no_today_bar=<标的数>`。
  ⇒ **建议采集时间从"15:05"改到 ≥15:30**（或让 `sync` 在 `no_today_bar>0` 时 15 分钟后重试一次）；
  这条不改代码，等主 AI 定 cron 时刻（红线 2 我不动 cron）。

## 3. 裁决 2-B：T+1 必须翻转，否则停线（`raw_flip_watch()`）

`{symbol: {flip_ok, stop, reason, bar_last, checked_last, checked_lag, ref_asof}}` +
汇总 `violations / warnings / stop_line`。三种 reason 的处置不同：

| 情形 | `reason` | 停线？ |
|---|---|---|
| 当日那根还没对照，但已过 T+1，**母库参照也没覆盖到该日** | `waiting_reference` | ❌ 只告警（母库的锅，不能停我们的线） |
| 母库参照**已覆盖**该日，我们却没重跑对照 | `checked_not_flipped` | ✅ 停线（我们的锅） |
| raw 本身断供（最新 bar 落后 > `max_lag`）/ 完全无 raw | `raw_stale` / `no_raw_data` | ✅ 停线 |
| 当日未对照但在 T+1 宽限内 | `None` | ❌ 放行（裁决原文允许当日 checked=0） |

参照新鲜度来自新增的 `mother.raw_close_asof(codes)`（每只标的 `close_raw` 已覆盖到的最后一天）；
`sync_raw()` 内部已用当轮参照的最大日期推同。CLI 侧：

- `data fetch-raw` → 结果带 `flip_watch`，`intraday_missing>0` 时写 `op_log[data:raw_no_intraday_ref]`；
- `data raw-coverage --gate` → `violations` 现在并入 `flip_watch` 的停线项，`--gate` 时 exit 1；
- 新增 `--no-intraday`（排障用，会留 `intraday_missing` 流水）。

## 4. 真机实测（NEC 生产 venv，只读源码 + 临时库，跑完即删）

```
$ date   2026-10-29 15:09:13 HKT
$ python -m paper_trading.cli --data-db /tmp/t34_data.db data fetch-raw \
      --symbols 000792 600519 000651 --days 5 --json
  written=14/标的(3 只共 42)   checked(母库)=5/标的   rejected=[]
  intraday_enforced=true   intraday_checked=0   intraday_no_today_bar=3     ← 当日日K未发布
  flip_watch: violations=[]  warnings=[000651, 600519]  stop_line=false      ← 母库参照滞后，只告警
```

同一只 000792（母库参照覆盖到 10-28）没有进 `warnings` ⇒ 三态区分在真实数据上成立；
`600519/000651` 因母库滞后（10-23/10-26 一档）被判 `waiting_reference` 而**没有**误停线——
这正是裁决 2-B 要的语义（否则母库一天不更新我们就得停线）。

对照链路本身另有一处独立验证：实时价与 akshare 10-28 raw 收盘同日才可比
（拉到的 `date=2026-10-29` 快照被正确拒绝用于 10-28 那根的比对）。

## 5. 交付清单

| 文件 | 改动 |
|---|---|
| `paper_trading/integration/mother.py` | 模块 docstring 与降级链注释如实；`adj` 标签按实际列；新增 `raw_close_asof()` |
| `paper_trading/data/raw_feed.py` | `intraday_reference()`、`session_closed()`（北京时间）、`check_raw_bars(...intraday/day/enforce_intraday)`、`intraday_no_today_bar`、`raw_flip_watch()`；模块 docstring 改"四条硬规矩" |
| `paper_trading/cli.py` | `data fetch-raw --no-intraday` + `op_log[data:raw_no_intraday_ref]`；`data raw-coverage --gate` 并入翻转停线判据 |
| `tests/test_caliber_defense_t34.py` | 21 条（标签三态参数化、当日对照四态、时区、翻转三态、母库 asof） |

## 6. 我不做的事 / 待主 AI 定的

- 不动 cron（采集时刻 15:05 → ≥15:30 的建议在 §2，请主 AI 定）；
- 不碰生产 `data.db`（真跑用的是 `/tmp` 临时库，跑完删）；
- 不改 `config.yaml` 默认值（容差与 `max_lag` 目前是模块常量，要配置化请说一声）；
- `intraday_no_today_bar` 的重试策略（等到日 K 出为止 vs 直接告警）留给主 AI 定，
  我倾向前者 + 30 分钟上限。
