# report-t31 — close_raw 供给管道（M2 数据侧，P0）

执行人：员工 AI（Qoder）｜2026-10-28｜分支 **`worker/t31`（含产品代码，待 review，不合并）**
依据：`docs/refactor-plan-open-source.md` §5.4 拍板 + `docs/worker/report-t13.md` §4.1/§4.3（P0）
门禁：G-T13-1（口径标签拒启）、G-T13-2（新鲜度）、T26 口径同步

> **一句话结论**：管道已建成并可测——引擎现在有**唯一的原始价取数口**
> （`BarSource.engine_raw_bars`），写入前与**独立源**同日对照，缺/陈旧/口径不符一律
> **返回空**（fail-closed），AI 展示层继续用 qfq 不受影响。新增 17 条测试，
> 全套 **357 passed, 1 skipped**（基线 `e28def3` 同环境实测 340 passed，零回归）。
> **对 §5.4 的一条事实修正**（见 §2）：母库只有 `close_raw/volume/amount` 是不复权，
> `open/high/low/close` 四列本身就是**前复权** ⇒ "母库 close_raw 优先供 OHLC" 不成立；
> 改为 **akshare `adjust=""` 供 raw OHLC + 母库 `close_raw` 做独立同日对照**——
> 比原方案更强（喂料源与验料源彼此独立，正是事故复盘要的"独立基准"）。

---

## 1. 交付清单（改了什么，逐文件）

| 文件 | 改动 | 作用 |
|---|---|---|
| `paper_trading/models/types.py:39-41` | `Bar` 新增 `amount`、`adjust`（都带默认值） | 口径随行携带；成交额补齐（rqalpha 的 VWAP/流动性判定要它，见 report-t13 §4.9） |
| `paper_trading/data/db_manager.py` | 新表 `daily_bars_raw`（PK(symbol,timestamp) + `adjust/source/checked`）；`daily_bars` **加列** `adjust DEFAULT 'qfq'`；`minute_bars` **加列** `adjust DEFAULT 'raw'`；`upsert_raw_bars(bars, source, checked_dates=…)`（**逐行**标记已独立对照）/`get_raw_bars/raw_latest/raw_latest_checked/raw_row_flags`；`get_bars` 读出时也带上 `adjust` 标签 | 原始价独立成表，**不动既有 qfq 任何数值**（AI 展示层还靠它）；`upsert_raw_bars` 收到非 raw 直接 `ValueError` 拒写；标签随行走，引擎侧校验才有牙齿 |
| `paper_trading/data/raw_feed.py`（新） | `check_raw_bars` / `sync_symbol` / `sync_raw` / `raw_freshness` / `factor_freshness_ok` | 采集→对照→落库→新鲜度裁决；`fetch`/`reference` 可注入（测试零网络） |
| `paper_trading/data/akshare_fetcher.py:36-77` | `_rows_to_bars` 捕获 `amount` 并带 `adjust`；新增 `fetch_daily_raw()` | raw 采集口（新浪 `stock_zh_a_daily(adjust="")` 优先，实测生产 venv 可用；东财在 NEC 直连失败，已在 §3 记录） |
| `paper_trading/integration/mother.py:506-542` | 新增 `raw_close_map()`（只读，取 `close_raw/volume/amount`） | 独立同日参照源（验料），不作喂料 |
| `paper_trading/data/source.py:76-105` | 新增 `engine_raw_bars()` + `last_engine_reject` | **引擎唯一允许的取数口**，任何不合格返回 `[]` 并留下可读原因 |
| `paper_trading/cli.py` | `data fetch-raw`（含 `--no-mother-ref`）/ `data raw-coverage`（含 `--gate`）；拒收写 `op_log[data:raw_reject]` | 运维入口 + 可审计；`--gate` 供 cron/CI 当门禁 |
| `tests/test_raw_feed.py`（新） | 17 条（见 §5） | 含**老库迁移**用例（生产 data.db 首次打开的真实路径） |

## 2. 【事实修正】母库的"raw"只有三列，§5.4 的读取链要按这个改

生产母库 `kline_daily`（10,548,933 行，2026-10-28 只读实测）列：
`open, close, high, low, volume, amount, turnover, close_raw, close_adj, close_qfq`。

同日三列并排（600519，2016-01-04）：

```
母库 open/high/low/close = 172.87 / 172.87 / 166.52 / 166.544   ← 与前复权 close_qfq 完全相等
母库 close_raw            = 210.02
rqalpha bundle（原始价权威）= open 218.00 / high 218.00 / low 209.99 / close 210.02
```

000792 2016-01-04、002594 2016-01-04 同样形态（母库 close == close_qfq，close_raw 才等于 bundle raw close）。
⇒ **母库的 OHLC 与 `close` 都是前复权口径，只有 `close_raw / volume / amount` 是不复权**；
`mother.py:406-429` 现有注释把 `close` 当"不复权"兜底列，是**误判**（它其实是 qfq）。

由此 T31 的读取链定为：

- **喂料**：akshare `stock_zh_a_daily(adjust="")` —— raw OHLC + `amount` + 股本量（一手实测）；
- **验料**：母库 `close_raw`（+ `volume/amount`）同日逐行对照，超容差 → **整只拒收**；
- 母库缺该日（实测 000792 2026-10-28 的 `close_raw` 就是 NULL）→ 该行计入 `no_reference`
  且**逐行**不标 `checked`（已验证的旧日不受影响；母库补上后重跑即自动回补，见 §3）。

对照严格度：raw 对 raw 实测**逐分一致**（000792 2026-10-26：22.24 / 32,772,237 / 727,573,784
三字段与母库完全相同），所以容差取 **0.5%**（`RAW_CLOSE_TOLERANCE`）远比 qfq 跨源闸的 10% 严，
而事故那类 6.4× 污染一定落网。

## 3. 实测证据（全部只读，不写生产库）

| 项 | 结果 |
|---|---|
| **母库 `close` 列的真实口径**（全表统计） | 10,548,933 行里 `abs(close - close_qfq) > 0.005` 的行数 = **0** ⇒ `close` 就是前复权；而 `abs(close - close_raw) > 0.005` 有 **8,498,992 行（80.6%）** |
| 母库 `close_raw` / `close_adj` 完整度 | 全表仅 **21 行** 为 NULL（不是普遍缺，但恰好落在最新日，见下条） |
| akshare raw 日线（NEC 生产 venv，2026-10-28） | 000792：10-26 22.24 / 10-27 22.21 / **10-28 22.10**，volume 32,772,237 / 32,111,015 / 30,032,806，amount 727,573,784 / 718,887,481 / 667,449,515 |
| 与母库 `close_raw` 同日对照 | 10-26 **22.24 = 22.24** ✓、volume/amount 全等 ✓；10-28 母库 `close_raw` 为 **NULL** |
| **【关键缺口】母库参照在"今天"结构性缺席** | 我们池 38 只全部在母库存在，但 **18/38 只最新日落后于 2026-10-28**（9 只停在 10-23、8 只 10-27、1 只 10-26）；而**已到 10-28 的 20 只，其 10-28 行 `close_raw` 全部为 NULL（20/20）** ⇒ 独立对照在当日几乎必然拿不到参照 |
| 由此确定的实现语义 | `checked` **逐行**记录（不是整批一刀切）：已验证的旧日不被新日拖累降级；母库补上后**重跑同一批**（`INSERT OR REPLACE`）该行自动翻成已验证（测试 `test_sync_symbol_marks_checked_per_row_not_per_batch` 实测这条回补路径） |
| 与母库新鲜度差 | 600519 母库最新日 **2026-10-23**，akshare 已到 **10-28** ⇒ `raw-coverage` 必须逐标的看，不能看全表 max |
| 5 分钟线口径（T26 同步项） | 000792 最后一根 5m（15:00）**22.10** = 当日 raw 日收 **22.10** ✓；600519 **1330.75 = 1330.75** ✓ ⇒ 分钟线本就是原始价，现显式打 `adjust='raw'` |
| 东财 `stock_zh_a_hist` 在 NEC | `ConnectionError: Remote end closed` —— 现有 `fetch_daily` 的新浪优先顺序是对的，raw 采集依赖它 |
| 母库 OHLC 误判后果 | 若按原设想"母库供 raw OHLC"，会把 **qfq 的 OHLC 拼上 raw 的 close**（混合口径），`high≥max(open,close)≥min(open,close)≥low` 一致性都可能破——`raw_row_flags()` 里就带这条 `ohlc_inconsistent` 检查 |

### 3.1 当日无法独立对照 ⇒ 需要 M2 补的第三条防线（不许留白）

`close_raw` 在 T 日为 NULL 是**母库回填节奏**决定的，不是偶发。所以 raw 管道当日的防线是：

1. **绝对量级闸**（`RAW_ABS_MAX=10 万`）+ **OHLC 一致性闸**（`raw_row_flags`）——当场生效；
2. **T+1 回补对照**：次日母库补上后重跑同一批，`checked` 自动翻转（已实现并测）；
3. **建议主 AI 增补**（我不擅自扩面）：当日再加一条**跨源实时价对照**——
   `data/realtime.py` 的实时快照价 vs 当日 akshare raw 收盘，>0.5% 即拒收当根。
   这条能把"当日独立基准"真正补上；实现约 20 行，我可以按你指示补做。

## 4. 门禁映射（G-T13 落到哪）

| 门禁 | 本任务的实现 | 状态 |
|---|---|---|
| **G-T13-1 口径** | 写入侧 `upsert_raw_bars` 拒非 raw；读取侧 `engine_raw_bars` 校验 `adjust=='raw'` 且独立表；`daily_bars` 打 `adjust='qfq'` 标签 | ✅ 已实现（测试 2/6/13/14/15） |
| **G-T13-2 新鲜度（本地半边）** | `raw_freshness()` 按交易日历算滞后交易日数；`engine_raw_bars` 陈旧即返回 `[]`；`data raw-coverage --gate` 可当 cron 门禁 | ✅ 已实现（测试 11/14/15） |
| **G-T13-2 因子新鲜度（回测线半边）** | `factor_freshness_ok(today, factor_last)` 是纯裁决函数；**bundle `ex_cum_factor` 末日读取**留给 T32 适配器（rqalpha 依赖在那边，产品不引 h5py） | ✅ 决策函数已实现并测（测试 12），取数在 T32 |
| G-T13-3 band 缺失拒跑 | 属 DayBarStore（T32） | ⏭ 移交 T32 |
| G-T13-8 拒单可审计 | 引擎取数被拒的原因落在 `BarSource.last_engine_reject`；raw 拒收落 `op_log[data:raw_reject]` | ✅ 出口已备好，接入在 T32 |
| T26 五分线 raw 口径 | `minute_bars.adjust='raw'` + 实测印证（§3） | ✅ 已同步 |

## 5. 测试（`tests/test_raw_feed.py`，17 条，全离线）

口径标签（老库加列不改值 / 拒写非 raw / raw 回读带 amount+adjust / 半批不算 checked）·
独立对照（一致→checked / 6.4× 尺度污染→拒 / 荒谬量级→拒 / 有分歧整只不落半条）·
端到端（注入假源+假参照，一只干净一只分歧 → `written=2 / rejected=['600519']`）·
新鲜度（陈旧/缺数/`no_raw_data`）· 因子门禁三态 ·
引擎闸四态（缺表 / 无数 / 陈旧 / 正常返回）· 母库 `raw_close_map`（临时 sqlite 断言它只取 raw 三列）·
**生产迁移**（用老 schema 建库→打开→`daily_bars` 原值 22.2403 未变、raw 表为空、分钟线拿到标签）。

全套实测（本地同环境、同一分支两次对照）：

| 跑法 | 结果 |
|---|---|
| `worker/t31` 分支 | **357 passed, 1 skipped** |
| 基线 `e28def3`（独立 worktree 实测，非引用他人数字） | **340 passed, 1 skipped** |
| 差值 | **+17 = 本分支新增测试数**；未删除、未跳过、未弱化任何既有用例 ⇒ **零回归** |

## 6. 复现

```bash
# 本地（我的环境：Python 3.13 + pandas 3.0.6 + numpy 2.3.4）
python -m pytest tests/test_raw_feed.py -q          # 17 passed
python -m pytest tests -q                            # 357 passed, 1 skipped

# 只读实测（NEC 生产 venv，不写任何库）
python - <<'PY'
import akshare as ak
d = ak.stock_zh_a_daily(symbol="sz000792", start_date="20261026", end_date="20261028", adjust="")
print(d[["date","close","volume","amount"]])      # 期望 22.24 / 22.21 / 22.10（= 母库 close_raw）
PY

# 采集 + 对照 + 覆盖率（M2 上线后才该在生产跑；我未执行）
python -m paper_trading.cli data fetch-raw --symbols 000792 600519 --days 40 --json
python -m paper_trading.cli data raw-coverage --symbols 000792 --gate --json
```

## 7. 我不做的事 / 待 review 的点

- 分支 `worker/t31` 含**产品代码**，按协议**待你 review 后合并**；我不碰 main、不部署、不改 cron、
  不在生产跑 `data fetch-raw`（那会写生产 data.db）。
- `config.yaml` 默认值一字未动；`daily_bars` 的既有 qfq 数值一字未动（迁移用例断言过）。
- 待你拍板的三处设计选择：
  1. **raw 独立表** vs `daily_bars` 加 `close_raw` 列 + 改主键（改主键要重建表并触碰既有行，我没选）；
  2. **母库当验料不当喂料**（§2 的证据决定的，如果你要维持"母库优先"，需要先让母方补 `open_raw/high_raw/low_raw`）；
  3. `RAW_CLOSE_TOLERANCE=0.5%` 与 `MAX_LAG_TRADING_DAYS=1` 的取值（都可配置化，但我不想动 config 默认值）。
- **`mother.py:406-429` 那条"不复权 close"注释是错的**，我没顺手改（它是 L2 读料口径的
  权威描述，改它属于 §5.4 之后的整体决定）——已同步进 `questions.md`。

## 8. 对同事 B（Hermes）`report-t33` 两条审查意见的处置

她审的是我 T13 的原型，两条都成立，逐条回执：

1. **ST band 用 5% 是合规缺陷** —— 认。本分支已修 `spikes/rqalpha_mod_t13sim.py::band_of`：
   主板 10% / 创业·科创 20% / 北交 30%，**ST 不降档**
   （AGENTS 合规表 #4/#5 + 硬规则 2"不得沿用 ST=5% 旧口径"）。
   参数 `is_st` 保留但显式 `del`，逼调用点表态；修完后**重跑 T13 selftest 21/21**
   （我们池无 ST 标的，band 数值逐根不变——这是"修正不改既有结论"的证明）。
   产品化时该函数进 T32 的 DayBarStore 并配**单测**（含 ST 主板/创业 ST/北交 ST 三档）。
2. **provider 的 fsync/校验和** —— 认，属 T32（provider 产品化时按钱类件对待）：
   落盘 `os.replace` 之后补 `f.flush()+os.fsync(dir_fd)`，并给每个 state 文件加内容校验和，
   `load()` 校验和不符 → 拒绝恢复并停线（而不是拿半截状态继续跑）。
3. 她提的 **close_raw 新鲜度**（母库 raw 列滞后）与我 §3.1/questions.md 第 2 问是同一件事，
   两侧独立命中 ⇒ 这条缺口是真的，等主 AI 在 (A) 当日跨源对照 / (B) T+1 回补闭环 之间拍板。
