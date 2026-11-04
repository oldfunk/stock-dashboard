# report-t39 · NEC 引擎环境落位 + bundle + 影子首跑（Qoder，P0·M2 影子期启动）

分支 `worker/t39`（**待 review，未合并**）。基线 `origin/main@aaedea4`。
所有落位动作都在 NEC（192.168.50.71）上，**生产交易/面板 venv 与现网账本零改动**。

## 0. 一句话结论

NEC 引擎侧已可跑：独立引擎 venv（`/home/nec/venvs/rq-engine`，41 包，700 MB，
numpy 2.3.5 + rqalpha 6.4.2，`pip check` 干净，x86-64-v1 全链 import 通过）、
bundle 3.3 GB 落位 `~/.rqalpha/bundle`（14 文件 sha256 已记录）、
**用真实生产原始价（2026-10-29 收盘）跑通 `rq shadow-day`**：
两侧现金 **827,339.1198** 分文不差、费用 **44.8802** 分文不差、
`cash_delta=0.0 / fee_delta=0.0 / fill_delta=0 / violations=[]`，
过户费 overlay 实测 `wrapped=["CS"]`。开发机同数据同 bundle 复跑得到**同一组数字**（§5）。

但 **两个必须先定的问题**：① 生产 `data.db` 的 `daily_bars_raw` 是 **0 行**——
派发文本说的"生产 daily_bars_raw 真数据"目前不存在，我的首跑用的是 scratch 库；
② raw 管道的**参照窗口与采集窗口不一致**，导致每批约 1/3 的行**结构性无法验料**
（母库其实有这些天）。两条都在 §6，需要拍板，我没有自己动生产。

## 1. 裁决/派发逐条核销清单（协议条款 `docs/WORKER.md:22`）

| 拍板原文（要点） | 状态 | 实测落点 |
|---|---|---|
| 批复 ④：NEC 专用引擎 venv + bundle 全放 NEC（370G 空闲无压力） | ✅ | venv `/home/nec/venvs/rq-engine`；bundle `/home/nec/.rqalpha/bundle`（3,537,975,667 B）；`df /home` 剩余 363 G |
| 派发①：按 `requirements-rqalpha.lock.txt`，**先 `numpy<2.4` 再装 rqalpha**，不与交易/面板 venv 混装 | ✅ | `python3 -m venv` → `pip install 'numpy<2.4'`（20 s）→ `pip install rqalpha==6.4.2`（182 s）→ `pip install -r requirements-rqalpha.lock.txt`（2 s，收敛无改动）；`pip check` = No broken requirements；freeze 41 包；`du -sh` = 700 M；现网 venv 仍无 rqalpha（`import rqalpha` → ModuleNotFoundError，符合"不混装"） |
| 派发①：二进制基线在 NEC 真机可用 | ✅ | numpy 2.3.5 / pandas 3.1.0 / h5py 3.16.0 / scipy 1.18.1 / matplotlib 3.11.2（Agg 出图）/ statsmodels 0.15.0 / patsy / jsonpickle / logbook / click / rqalpha 6.4.2 全部 import 通过；`rqalpha --help` 与 `check-bundle` 可执行 |
| 派发②：bundle 下载落位 + **sha 记录** | ✅ | `rqalpha download-bundle --confirm`，源 `http://bundle.assets.ricequant.com/bundles_v4/rqbundle_202610.tar.bz2`，16:59→17:02（约 3 min）；14 个文件的 sha256 见 §3；`check-bundle` 提示"日线数据不完整"（快照日 2026-10-01，月中必然落后，见 §4） |
| 派发③：`rq shadow-day` 在 NEC 生产环境首跑（只读影子、写 parity:diff 留痕），输出与开发机同场景对齐 | ✅（留痕位置待拍板） | 首跑 5 s：`fee_delta=0.0`、两侧 `cash=827,339.1198`、`fees=44.8802`、2 成交 + 1 拒单（可读中文原因）、`gates.ok=true`；默认留痕写成 `op_log[parity:diff]` **落在 scratch 账户库**（`/home/nec/tmp/t39_acct.db`），**没写现网 `paper_account.db`**——`worker/t38` 还没过 review，且红线不写账本，见 §6-C |
| 派发④：`report-t39.md` 记耗时/磁盘占用/首跑对账结果 | ✅ | 本文件 §2 §3 §5 |
| 派发④后半：cron 行等影子期跑顺再提请 | 未做（按派发） | 我只把建议行写在 §7，不代提 cron |
| 采集时刻 ≥15:35（批复 ③，M3 清单） | 不适用本任务 | 本次手工采集在 17:00（北京时间）之后执行，符合该规矩；cron 行按此时刻 |

## 2. 引擎 venv 落位事实

```
机器   nec (192.168.50.71)  Linux Mint 22.3  x86-64-v1 (Core2 Duo T9900)
路径   /home/nec/venvs/rq-engine          Python 3.12.3
安装   numpy<2.4 先钉（20s）→ rqalpha==6.4.2（182s）→ lock 收敛（2s）= 合计 204s
结果   41 包 / 700 MB / pip check 干净 / numpy 2.3.5 pandas 3.1.0
对照   /home/nec/paper-trading/venv（交易·面板·采集）仍无 rqalpha —— 未混装
```

`requirements-rqalpha.lock.txt` 头部写的"先钉后装"姿势在 NEC 复核成立：
**直接装 rqalpha 会解析到 numpy 2.5.4 并 import 即 `RuntimeError (X86_V2)`**（T37 已三次现场复现）。
本次 freeze 与 lock 逐包一致（差 0 项），已把 `t39_engine_freeze.txt` 留在 NEC。

T38 的新模块在这台机器上真跑：
```
costs.stock_cost_mod_config() = {'enabled': True, 'stock_commission_multiplier': 0.3125,
                                 'stock_min_commission': 5.0, 'tax_multiplier': 1.0, 'pit_tax': False}
costs.install_transfer_fee(env, 1e-05) -> ['CS']
overlay.calc(...) -> commission=25.0000 tax=0.0000 other=1.0000 TOTAL=26.0000
isinstance(overlay, AbstractStockTransactionCostDecider) = True   # order_target_* API 不会被卡死
```

## 3. bundle 落位与 sha256（NEC `~/.rqalpha/bundle`，快照日 2026-10-01）

总字节 `3,537,975,667`；`sha256sum` 清单同时留在 NEC `/home/nec/t39_bundle_sha256.txt`。

| 文件 | sha256（前 16 位） | 字节 |
|---|---|---|
| dividends.h5 | `eb66ef0d86b22915` | 6,721,992 |
| ex_cum_factor.h5 | `c8a67e8a0c549bf6` | 3,651,120 |
| funds.h5 | `114eaa0a70dc3028` | 259,898,208 |
| future_info.json | `e040dbc9ebd26c6c` | 1,759,224 |
| futures.h5 | `d288f6e4c7468981` | 377,901,136 |
| indexes.h5 | `d19667a1b26bad50` | 1,551,265,192 |
| instruments.pk | `1edb7b5cade6c6f4` | 9,714,521 |
| share_transformation.json | `b1c1c22e85b46ad8` | 3,802 |
| split_factor.h5 | `4e3212f0918aeb11` | 2,940,696 |
| stocks.h5 | `3f7891ec4dd94ee4` | 1,309,672,704 |
| st_stock_days.h5 | `91b9bdbd6634b8e2` | 7,690,072 |
| suspended_days.h5 | `f7f70485fb95a85a` | 5,752,536 |
| trading_dates.npy | `57dd7f539d1cdbef` | 44,824 |
| yield_curve.h5 | `50d61b53484e133e` | 959,640 |

开发机 `C:\Users\yanzh\.rqalpha\bundle` 与 NEC 是**同一份 202610 快照**
（逐文件字节数一致），所以 §5 的跨机对照没有"两份 bundle 不同"这个混淆因子。

## 4. `check-bundle` 会说"日线数据不完整"——这不是故障，但要知道它的含义

```
$ rqalpha check-bundle -d ~/.rqalpha/bundle
Bundle日线数据不完整，请及时更新bundle
```

快照是月度（202610 打包于 10-01），而影子期要跑的是当月交易日
⇒ **框架自带的日线与 `ex_cum_factor` 必然落后到月中**。M2 的行情来自我们自己的
`daily_bars_raw`（适配器 `RawDayBarStore` + `override_available_range`），
撮合价不依赖 bundle；但**复权因子 `ex_cum_factor.h5` 也来自同一份快照**，
所以"除权日当天或之后因子是否已更新"仍是真实约束（G-T13-2 `factor_freshness_ok`）。
建议：把"bundle 月度更新"写进 M3 运维清单（谁跑、跑完核 sha），
并明确更新前不许跨除权日跑历史。

## 5. 影子首跑（真实生产价）与开发机对齐

场景（两机完全相同）：`--today 2026-10-29`，池内 3 只，
买 600036 2000 股、买 000858 1200 股、卖 601318 100 股（无持仓，应拒），`--init-cash 1,000,000`。

```
NEC（引擎 venv，5 s）/ 开发机（refactor-spike venv，2 s）——以下两侧机器输出逐字段相同：
  rqalpha 现金   827,339.1198      旧引擎现金 827,339.1198
  rqalpha 费用   44.8802           旧引擎费用 44.8802
  持仓           600036×2000 @42.94 / 000858×1200 @72.28（旧引擎 closable=0 ⇒ T+1 未放开）
  成交           2 笔（15:00 current_bar）  拒单 1 笔："仓位不足，目标平仓量为 100，可平仓量为 0"
  diff           cash_delta=0.0  fee_delta=0.0  fill_delta=0  violations=[]  ok=True
  evidence.cost  wrapped=["CS"]  applied=8  added_total=6.90464
  gates          ok=true errors=[] warnings=[] event_counts{BEFORE_TRADING 1, AUCTION 1,
                 BAR 1, AFTER_TRADING 1, PRE_SETTLEMENT 1, SETTLEMENT 1, POST_SETTLEMENT 1}
费用手算复核：600036 2000×42.94=85,880 ⇒ 佣金 max(21.47,5)=21.47 + 过户费 0.8588 = 22.3288
             000858 1200×72.28=86,736 ⇒ 佣金 21.6840 + 过户费 0.8674 = 22.5514  合计 44.8802 ✓
```

两机的 `session_fingerprint` 不同（NEC `b27699cd…` / 开发机 `c7f23456…`）是**设计如此**：
指纹里含 `db` 与 bundle 的绝对路径（`runner.py:121-122`，main 口径；
`worker/t38` 上是 `runner.py:137-138`），跨机必然不同；
它不是语义漂移，账本数字才是可比对象。

`--no-op-log` 在 NEC 复跑：对账结果相同、新账户库 `op_log` 行数 0 ⇒ 关闭旗在现网机型上生效。

## 6. 必须先拍板的三件事（我没有自己动生产）

**A. [P1] 生产 `data.db.daily_bars_raw` 是 0 行** —— 现网只读体检：
`tables=[daily_bars, daily_bars_raw, minute_bars, quote_log, stock_pool]`，
`daily_bars_raw rows=0 latest=None`；`minute_bars` 主键已是 `(symbol,ts,period)`
且 `.pre-t35.bak` 已在（说明 T35 迁移在现网已发生），但 `paper_account.db.op_log` 里
**没有任何 `data:migrate` 行**（迁移发生在 T38 留痕落地之前）。
⇒ 派发文本"生产 daily_bars_raw 真数据"目前不成立；影子期每天要读的就是这张表。
请派一件"生产 raw 回填 + 每日采集"的任务（我在 scratch 库跑的 590 行是流程验证，
不代表现网有料）。

**B. [P1] 参照窗口比采集窗口短，导致每批约 1/3 的行永远验不了料**（scratch 实测）：
`raw_feed.py:239-241` 采集起点 = `today - (days*2+10)` **自然日**（`--days 40` ⇒ 2026-07-31 起，
59 个交易日），而 `mother.raw_close_map(days=40)` 是 `LIMIT 40` **交易日**
（`mother.py:533-540`）⇒ 老的一半没有参照。实测：
590 行里 `checked=1` 只有 400 行，未验料的 21 个日期中 **17 个是 8 月**（母库明明有：
母库对 000858 覆盖 2026-06-08…2026-10-27），另外 4 天是母库自己的滞后（10-26…10-29）。
更糟的是 `data raw-coverage --gate` 此时报 `violations=[]`——
`raw_flip_watch` 只看"最后一次 checked 距今几个交易日"（`raw_feed.py:192-218`），
看不见**中段**没翻转的行。建议两条任选其一并配测试：
把参照窗口按采集窗口取齐（`days` 传交易日数或直接给 start/end），
并给 raw-coverage 增加 `unchecked_before` 计数（超过 N 天仍未验料 ⇒ 停线或告警）。

**C. [待拍板] 影子留痕写现网 `paper_account.db` 的时机**：
T38 已把默认改成"写"，但我这次**刻意把 `--account-db` 指到 scratch 库**，
理由：① `worker/t38` 未 review；② 红线"不跑写账本的命令"。
现网 `op_log` 至今 0 条 `parity:diff`。请在你 review 后指定：
是把每日影子 cron 直接指向现网账户库（裁决 4 的原意），
还是先观察一周 scratch 留痕再切换。

## 7. 影子期 cron 建议（按派发：跑顺后再正式提请，这里只给草稿）

```
# NEC，北京时间；15:35 之后（批复 ③：新浪日 K 发布后的安全线）
35 15 * * 1-5  cd /home/nec/paper-trading && venv/bin/python -m paper_trading.cli \
               --data-db data.db --account-db paper_account.db data fetch-raw --days 40 --json \
               >> /home/nec/logs/raw_fetch.log 2>&1
45 15 * * 1-5  cd /home/nec/tmp/pt-t39 && /home/nec/venvs/rq-engine/bin/python -m paper_trading.cli \
               --data-db /home/nec/paper-trading/data.db --account-db /home/nec/paper-trading/paper_account.db \
               rq shadow-day --today $(date +\%F) --symbols <池> --plan '[]' --shadow-dir /home/nec/shadow \
               >> /home/nec/logs/shadow.log 2>&1
```
注意第二条**会默认写现网 op_log[parity:diff]**（§6-C 的待拍板项），
以及 `cd` 到哪个代码树（现网 main 还是 T38 分支）——所以我没替你决定。

## 8. 环境与红线

- NEC 上只写了自己的 scratch：`/home/nec/venvs/rq-engine`（新增引擎 venv，
  与交易/面板 venv 物理隔离）、`/home/nec/.rqalpha/bundle`（批复 ④ 指定的落位）、
  `/home/nec/tmp/*`（代码副本 + scratch 库 + 日志）。
- **没碰现网 `paper_account.db` / 现网 `data.db`**（只读体检走 `mode=ro` + `PRAGMA query_only`）；
  没跑 `agent run/tick/fire`、`buy/sell`、`corporate apply`、面板 POST；
  没碰 Pi、`secrets.local.json`、部署、tag、cron；`config.yaml` 只读未改。
- 代码副本用 `git clone --no-hardlinks` + **git bundle**（`worker/t38`，8.8 MB）方式送达，
  避免在现网仓库 `.git` 里建 worktree 或在 NEC 配置 GitHub 凭据；
  现网仓库 HEAD 仍是 `main@aaedea4`，`git status` 只有未跟踪的 `.pre-t35.bak`。
- 本地只在 `C:\Users\yanzh\AppData\Local\Temp\pt-worker-t38` 与本 worktree 活动。
