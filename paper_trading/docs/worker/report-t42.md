# report-t42 · 切换前对账验证 + 现网留痕启动（Qoder，P0·Batch 9，依赖 T40/T41）

分支 `worker/t42`（堆叠在 `worker/t40`→`worker/t41` 之上，**待 review 按序合并**）。
按 §5.6 节奏变更，影子期从"一周"压成"1-2 个交易日 + 切换就绪结论"。

## 0. 一句话结论

**2026-10-29 这一个交易日的全链跑通了**：采集（18:22，>15:35）→
`rq shadow-day`（现网真数据，默认写现网账本）→ `raw-coverage --gate`，
**`cash_delta / fee_delta / fill_delta` 三个全 0、violations=[]、gate 退出码 0**，
并且**首个现网 `op_log[parity:diff]` 已落库**（id=327，`rqalpha_cash == legacy_cash == 743559.3428`）。
账本影响只有 2 行审计流水，余额/持仓/成交逐位未变（§3）。

但**我不给无条件的"切换就绪"**：这一跑顺带把一件结构性失效的防线照出来了——
**"当日跨源对照"（§5.5 裁决 2 的 A 半边）在规定的 ≥15:35 采集时点上永远跑不到**，
所以今天那 38 根当日 bar 是在**没有当日独立参照**的情况下进库、并被引擎取数口接受的
（引擎口不要求 `checked=1`）。细节与三个修法选项在 §4，需要拍板。

## 1. 逐条核销清单

| 拍板/派发原文（要点） | 状态 | 证据 |
|---|---|---|
| T42①「`rq shadow-day` 默认账本指生产 `paper_account.db`；首个现网 `op_log[parity:diff]` 落库贴证据」 | ✅ | 现网 `op_log` id=327 `action=parity:diff ok=1 result={"cash_delta":0.0,"fee_delta":0.0,"fill_delta":0,"rqalpha_cash":743559.3428,"legacy_cash":743559.3428}`；写之前该库 `parity:%` 行数 = 0 |
| T42②「切换前对账验证日：回填后连续 1-2 个交易日全链（采集 ≥15:35 → shadow-day → raw-coverage --gate），全零即出'切换就绪'结论」 | ⚠️ **1 个交易日达成，但结论带条件** | 全链三步见 §2（三个 delta 全 0、gate 0）；条件 = §4 的 P1（当日跨源防线关闭）先定处置 |
| T42③「cron 行草稿提请（切换后转正用，由我装）」 | ✅ 草稿在 §5 | 三步命令 + 15:40 时刻 + 日志/锁文件路径，参数与本次实跑一致 |
| T42④「`report-t42.md`：验证日对账汇总 + 切换就绪结论」 | ✅ | 本文件 |
| 批复 3「留痕切换时机 → **现在切**：影子对账正式写现网 `op_log[parity:diff]`，这是影子期启动的标志」 | ✅ | 上面 id=327 那一行；本任务起不再指 scratch |
| `b5983f6` §5.6 底线四件：T41 fix / T40 backfill / ≥1 天零差 / T44 no-P0 | 3/4 | T41 ✅（`worker/t41`）、T40 ✅（现网 28,339 行 gate 全绿）、≥1 天零差 ✅（本文件）、**T44（Hermes）还没交** |
| 红线「账本红线不变」 | ✅ | 账本指纹前后一致（§3），只多 2 行审计 |

## 2. 验证日全链（NEC 现网，2026-10-29）

```
① 18:22:xx  data fetch-raw --days 560 --symbols <现网 stock_pool 38 只>   （>15:35 规矩 ✓）
   written=28339  checked=28264  no_reference=37  拒收=0
   flip: stop_line=False unflipped_days=0 waiting_days=37 no_reference_days=0
   当日跨源: enforced=True  checked=0  missing=38  no_today_bar=0     ← §4 的问题就在这行

② 18:23:0x  rq shadow-day --today 2026-10-29 --symbols 000651 300750 600519 601318
             --plan [买 000651 2000 / 买 300750 200 / 买 600519 100 / 卖 601318 100]
             --account-db <现网 paper_account.db>（默认留痕，不指 scratch）
   cash_delta=0.0  fee_delta=0.0  fill_delta=0  violations=[]  ok=True
   两侧现金 743,559.3428 == 743,559.3428    两侧费用 66.6572 == 66.6572    成交 3 == 3
   拒单 1 笔（两侧一致）：601318 仓位不足（新引擎原因可读；旧引擎只有 "see_logger"）
   evidence.cost: wrapped=["CS"] applied=12 added_total=10.25496
   （300750 是 300 开头 ⇒ 顺带证明 20% 带与创业板撮合都工作）

③ 18:23:0x  data raw-coverage --gate --days 560 --symbols <38 只>
   退出码=0
   audit = {since:2023-09-25, audited_days:28339, verified_days:28264,
            unflipped_days:0, waiting_days:37, no_reference_days:0,
            incomplete_days:0, complete:true}   violations=[]  waiting 标的=18 只
```

第 ② 步的费用逐笔手算复核（佣金万 2.5 + 过户费 0.001% 双向，均高于 5 元地板）：
`75,160→18.79+0.7516=19.5416`、`45,814→11.4535+0.45814=11.9116`、
`135,400→33.85+1.354=35.204`，合计 `66.6572` —— 与两侧引擎一致。

## 3. 账本影响核对（这是"写现网账本"最要命的部分，所以单列）

写前冷备：`/home/nec/paper-trading/paper_account.db.pre-t42-20261029-182221.bak`。

| 项 | 跑前 | 跑后 |
|---|---|---|
| `account.cash` | 71,922.004 | **71,922.004** |
| `positions`（5 只） | 000792×100 / 000858×200 / 002215×100 / 600036×200 / 600989×100（available 全等） | **完全相同** |
| `orders` / `fills` / `nav_history` / `agent_plans` / `conditional_orders` | 12 / 11 / 15 / 17 / 37 | **12 / 11 / 15 / 17 / 37** |
| `op_log` 总行数 | 325 | **327**（+2：`data:raw_no_intraday_ref`、`parity:diff`） |
| `parity:diff` 行数 | 0 | 1 ← 影子期启动的标志 |
| `data:migrate` 行数 | 0 | 0（现网 `data.db` 无新迁移 ⇒ 迁移留痕机制待下一次真迁移时生效） |

影子成交本身落在 `--shadow-dir`（`/home/nec/tmp/t42_shadow/paper_account.db` + `rq_state/`），
不碰现网持仓/余额——`shadow.py` 的路径拒绝逻辑（与生产账本同路径直接 RuntimeError）这一跑再次验证有效。

## 4. [P1·新发现] 当日跨源对照在规定采集时点上**结构性跑不到**

**症状**：`intraday_enforced=True` 但 `intraday_checked=0 / missing=38`，
于是写了 `data:raw_no_intraday_ref`，38 只标的的**当日那根**全部 `checked=0` 入库。

**根因（读码 + 实跑，全部有输出）**：
1. `raw_feed.intraday_reference()` 走 `realtime.get_quotes(codes)`；
2. `realtime.get_quotes()` 在非连续竞价时段**直接返回空**
   （`realtime.py:149-150`：`if not is_trading_session(tnow): return {}`）——18:22 已收盘 ⇒ 空；
3. 即便盘中，`quote_fresh()` 还会丢掉"距当前 > `QUOTE_MAX_AGE=180` 秒"的报价
   （`realtime.py:19/54`），而腾讯收盘后的最后一笔时间戳是 `16:14:45`
   ⇒ 距 15:35 采集已远超 3 分钟，同样被丢；
4. 网络和源都没问题：同一时刻 `realtime.fetch_batch(["000651","600519"])` 正常返回
   `000651=37.58 / 600519=1354.0`，`parse_line` 解出 `date=2026-10-29`，
   直连 `https://qt.gtimg.cn/q=sz000651` 也通（503 字节）。**是我们自己的新鲜度闸把 A 防线关掉了。**
5. 后果不是"少个告警"：`source.engine_raw_bars()`（`source.py:76-104`）只查
   "是不是 raw + 是否新鲜"，**不要求 `checked=1`** ⇒ 引擎会吃下当天这根没有独立参照的 bar。

**为什么这是 P1**：§5.5 裁决 2 的 A 半边，初衷正是"母库 T 日 `close_raw` 是 NULL，
所以收盘后拿腾讯快照对照当日那根"。而批复 ③ 又把采集定在 ≥15:35——
两个规定叠在一起，A 半边在生产时点上恒不成立。M3 切换后引擎当天就下单，
这条防线是不是真在跑，属于钱类判定。

**三个修法（我不自选，请拍板）**：
- (a) **给收盘后的对照开专用通道**：`intraday_reference()` 用 `get_quotes(codes, force=True)`
  并且用一个"当日盘后新鲜度"判据（`date == today` 且报价时间在 `[15:00, 采集时刻+缓冲]` 内），
  不再套 `QUOTE_MAX_AGE=180s`。改动小、不动撮合侧，我倾向这个。
- (b) 把 raw 采集挪到盘中（如 14:55）——但新浪**当日日 K** 实测 15:35 前后才发
  （T34 现场撞到的），移动时点会换回"没有当日 bar"的老问题，我不建议。
- (c) **加严引擎入口**：`engine_raw_bars` 对"今天那根"要求 `checked=1`，
  当日没独立参照就按缺数据不下单。语义最干净，但当天就可能停单（今天这 38 只就会全部不下单）。
  若选 (c)，(a) 必须先修好，否则 M3 后每天 15:35-母库更新前都无法交易。

顺带同一族的第二条（弱一档，P2）：`raw_flip_watch` 现在能看见 `waiting_reference`，
但母库在 10-23/10-27 这种"近端整段缺"的情况下，`no_reference_available` 与
`waiting_reference` 的边界依赖 `ref_asof` 单点；如果母库出现"跳过一天再补上"的形状，
分类会偏保守（不误停线，但会多报 waiting）。要不要按母库逐日集合判存在，
可以在 T41 的 `ref_days` 基础上再加一条断言——等 (a)/(c) 定了一起做，避免两批改同一函数。

## 5. cron 草稿（T42③，按派发由主 AI 安装；时刻沿用批复 ③/§5.6 的 15:40）

```cron
# NEC crontab（北京时间；15:40 = 新浪日K发布安全线之后、母库夜间更新之前）
40 15 * * 1-5 cd /home/nec/paper-trading && \
  venv/bin/python -m paper_trading.cli --data-db data.db --account-db paper_account.db \
  --lock-file var/fetch.lock data fetch-raw --days 560 --json >> logs/raw_fetch.log 2>&1
# 影子对账要两个 venv：采集用交易 venv（有 akshare），shadow-day 用引擎 venv（有 rqalpha）
45 15 * * 1-5 cd /home/nec/paper-trading && \
  /home/nec/venvs/rq-engine/bin/python -m paper_trading.cli \
  --data-db data.db --account-db paper_account.db --lock-file var/shadow.lock \
  rq shadow-day --today "$(date +\%F)" --symbols <当日池> --plan '[]' \
  --shadow-dir /home/nec/shadow >> logs/shadow.log 2>&1
50 15 * * 1-5 cd /home/nec/paper-trading && \
  venv/bin/python -m paper_trading.cli --data-db data.db --account-db paper_account.db \
  --lock-file var/gate.lock data raw-coverage --gate --days 560 >> logs/gate.log 2>&1
```

装之前要知道的三件事：
1. **代码得先在现网树里是 T41+**（我这次是用 `/home/nec/tmp/pt-t41` 副本跑的，
   现网 `main` 还没有 T41 的窗口对齐与逐日闸）。
2. `--plan '[]'` 是"只对照不下单"的空跑；如果影子期想让两侧同受真实计划，
   计划要从现网 `ai:plan` 取，那是另一件事（**影子期只对照，不动账本**是当前边界）。
3. `--symbols` 我留了占位：现网 `stock_pool` 是 38 只，命令里不写 `--symbols` 就会用
   `config.yaml` 的 10 只——**两个池不一致**这件事本身要不要统一，也请一并定
   （本次全链我用的是显式 38 只，与现网 `stock_pool` 表一致）。

## 6. "切换就绪"结论（带条件）

- 按派发判据（全链三步 + 三个 delta 全零）：**已满足 1 个交易日**。
- §5.6 底线四件里 **T44（Hermes 钱类复核）还没交**，那是硬底线，不由我判定。
- 我给的条件：**§4 的 (a)/(c) 至少落地一个**。理由——切换就绪的含义是
  "引擎当天吃的价有独立参照"，而现在这句话在生产里不成立；
  其余部分（费率口径、T+1、涨跌停带、创业板 20%、拒单原因、留痕、门禁）都已经在现网数据上验过且全零差。
- 第二个验证日：明天（10-30）15:40 之后同一条链再跑一次即可（命令与本次完全一致）；
  或者 §5 的 cron 装好后由它自动产出。**要我等明天再跑一次、还是先把 §4 修掉再说，请给个优先级**
  ——我接下来按计划做 T45（切换 runbook）与 T43（架构地图），不空等。

## 7. 环境与红线

- 动过现网两处，都在授权范围内：`data.db` 的 `daily_bars_raw`（T40 授权）、
  `paper_account.db` 的 **op_log 审计行**（T42①/批复 3 明确要求"现在切现网"）。
- 账本没被交易语义动过：余额/持仓/`orders`/`fills`/`nav_history` 前后逐位一致（§3）；
  没有跑 `agent run/tick/fire`、`buy/sell`、`corporate apply`、面板 POST；
  没碰 Pi、`secrets.local.json`、部署、tag、cron；`config.yaml` 只读未改。
- 冷备两份：`data.db.pre-t40-20261029-181759.bak`、`paper_account.db.pre-t42-20261029-182221.bak`。
- 复算材料在 NEC：`/home/nec/t42_collect.json`、`/home/nec/t42_shadow.json`、
  `/home/nec/t42_gate.json`、`/home/nec/tmp/t42_shadow/`、代码副本 `/home/nec/tmp/pt-t41`。
