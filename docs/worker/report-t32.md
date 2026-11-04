# report-t32 — T13 原型 → 产品适配器 + 双轨影子对照（M2，P0）

执行人：员工 AI（Qoder）｜2026-10-28｜分支 **`worker/t32`（含产品代码，待 review，不合并）**
建立在 `worker/t31`（close_raw 管道）之上 —— **注意：T31 尚未 review，T32 依赖它，
两条分支要一起看**。
运行环境：rqalpha 6.4.2 @ `C:\Users\yanzh\venvs\refactor-spike`（Py 3.13）+ bundle 202610
数据：NEC 生产真实原始价（`ak.stock_zh_a_daily(adjust="")` 全池 38 只 / 12,996 行，
+ 母库 `close_raw` 12,960 行作独立验料），**不写生产库**。

> **一句话结论**：三件套（DayBarStore / DataSource / FilePersistProvider）已按
> rqalpha 官方扩展点进产品，六道 M2 门禁（G-T13-3/5/6/7/8/9）在真实数据上逐项实测，
> **端到端 16/16 PASS**；同日双轨（rqalpha vs 现网 `PaperBroker`）影子对账跑通，
> **持仓/T+1/成交笔数逐字段一致，唯一差异是过户费 0.0222 元**（与 T29 的 F-tier 结论吻合）。
> 全套 **387 passed, 4 skipped**（基线 340+1，新增 33 条，零回归）。
> 过程中挖出 6 条"接 rqalpha 才会踩到"的实现级缺陷（§5），都已落在代码与注释里。

---

## 1. 交付清单

新增包 `paper_trading/integration/rq_engine/`（**目录名不能叫 `rqalpha`**，见 §5-④）：

| 文件 | 内容 | 是否 import rqalpha |
|---|---|---|
| `bands.py` | `band_for/round_tick/limit_prices`：主板 10% / 创业·科创 20% / 北交 30%，**ST 不降档**（AGENTS #4/#5/#6 + 硬规则 2；T33 的 P0 修正延续） | 否 |
| `store.py` | `RawDayBarStore`（鸭子类型 `AbstractDayBarStore`）：`daily_bars_raw` → bar 结构化数组，**必供 `prev_close/limit_up/limit_down`**；`BandMissingError`=G-T13-3；OHLC 不自洽（混合口径特征）也拒 | 否 |
| `provider.py` | `FilePersistProvider`：一 key 一文件 + 原子换名 + **fsync + sha256 sidecar**（T33 点名的两项）；`load()` 校验和不符/无 sidecar → 返回 None 并记 `restore_errors`（rqalpha 会吞异常，错误必须留在我们手里）；`SessionGate`：幂等 + **配置指纹**（G-T13-5） | 否 |
| `data_source.py` | `PaperDataSource(BaseDataSource)`：bundle 静态元数据 + 热替换 CS store + **覆盖 `available_data_range`**；`bundle_price_last/bundle_factor_last`（G-T13-2 回测线取数） | **是** |
| `mod_entry.py` | rqalpha mod 入口（`load_mod()`）：接线 store/data_source/provider + 事件采集（四阶段计数、成交、订单、**`ORDER_CREATION_REJECT` 原因**=G-T13-8）；启动先 `pre_load` 体检状态文件 | **是** |
| `runner.py` | `run_session()` 一天一会话：建配置（`start==end==today`、`run_type=p`、**禁 `sys_analyser`**=G-T13-6、`matching_type=current_bar`、`price_limit=True`）→ 会话闸 → 跑 → **后置门禁**（空转断言 G-T13-7、状态恢复错误、SETTLEMENT 重放告警）→ 成功才推进 `session.json`；`describe_error()` 剥 `ExceptionGroup`；模块级 `_SESSION_USED` 拒绝进程内重入（G-T13-9） | 是（仅 `run_func` 处） |
| `shadow.py` | 双轨影子：rqalpha 侧 + `PaperBroker` 侧（**同 initial_cash、零滑点、同一份 raw bar、同一 6 位代码键**）→ `parity_diff` 逐字段对账；`record_to(broker, res)` 才写 `op_log[parity:diff]`（默认不写） | 部分 |
| `paper_trading/cli.py` | `rq day` / `rq shadow-day` / `rq state`（只读体检）；未装 rqalpha 时 `rq day` 直接 exit 2 说明原因 | 否 |
| `tests/test_rqalpha_adapter.py` | 33 条（无 rqalpha 也能跑 30 条，3 条 `importorskip`） | — |
| `spikes/e2e_t32.py` | 端到端验收 harness：`build`（生产原始价 → 产品 raw 管道）+ `verify`（16 项检查，全部走 CLI 子进程 = 真重启） | — |

**产品 venv（NEC）不装 rqalpha 也能跑全套单测**：store/provider/bands 都是鸭子类型，
只有 `data_source/runner/mod_entry` 需要框架，且 `rq day` 在无框架时明确拒绝而不是静默。

## 2. 端到端实测（`spikes/e2e_t32.py verify`，16/16 PASS）

| # | 检查 | 实测数字 |
|---|---|---|
| E1 | store 构建 + band 齐供（G-T13-3 未开火） | 000792/600519/002648 各 260 根，`bands_ok=259`（首根无前收） |
| E2 | 一个完整模拟日（**一天一进程**） | `rc=0`、`errors=[]`；买 100 股 @**22.21** 成交，费用 **5.00**（rqalpha 万 8 地板 5） |
| E2 | 当日 T+1 挡卖 + 原因可读（G-T13-8） | `ORDER_CREATION_REJECT`：`订单创建失败：000792.XSHE 的仓位不足，目标平仓/行权量为 100，可平仓/行权量为 0` |
| E2 | 冻结态落盘 | 持仓 `quantity=100, closable=0`；`session_marked_done=true` |
| E3 | **跨进程续跑**（重启后读状态） | 昨日持仓 100 且 `closable=100`（T+1 解冻）；卖 @**22.10** 成交，费用 **6.105**（5 佣金 + 1.105 印花税） |
| E3 | SETTLEMENT 重放被点出（G-T13-6） | 警告：`SETTLEMENT 触发 2 次（预期 1）`；`ok=true`（账本无碍、指标不可信） |
| E4 | 同日重跑被拒（G-T13-5） | `rc=4`，文案含"同日重跑会重复下单（resume 只跳 before_trading…）" |
| E5 | 涨跌停带来自**产品 store** | 002648 2026-06-23 一字板：`REJECTED … 已触及涨跌停价：limit_up`；同日对照 000651 **FILLED @37.29** |
| E6 | 双轨影子对账 | rq 现金 **97,774.00** / 旧引擎 **97,773.9778**；`position_delta={}`、`fill_delta=0`；`cash_delta=0.0222` = **过户费 0.001%×2221**（与 T29 一致），`violations=[]` |

管道侧（T31 产品在真实数据上）：`build` 写入 **12,996** 行 raw、
**0 只标的被拒收、0 行口径不符**、**12,939 行经母库 `close_raw` 同日对照**、
57 行无参照（母库缺该日/该列为 NULL）；akshare 与母库 12,939 对可比的
**最大相对偏差 = 0.0**（逐分一致）。

## 3. 门禁落地对照

| 门禁 | 落点 | 证据 |
|---|---|---|
| G-T13-3 band 缺失拒跑 | `store.RawDayBarStore(require_bands=True)` 最新日/中段缺带即 `BandMissingError`；CLI `rq state` 命中即 exit 1 | 单测 + E1 |
| G-T13-5 幂等 + 配置指纹 | `provider.SessionGate.check/mark_done`；`runner.session_fingerprint` 覆盖 accounts/费率开关/bundle/db/symbols | E4 + 单测 3 条 |
| G-T13-6 禁 `sys_analyser` | `runner.build_config` 写死 `enabled: False`，SETTLEMENT 重放降级为**警告** | E3 |
| G-T13-7 空转断言 | `check_session_gates`：`handler_calls<1` 或"有计划下单却既无成交也无拒单原因"→ 门禁不过 | E2 反例（首轮就靠它抓到 §5-① 的真因） |
| G-T13-8 拒单可审计 | `mod_entry._on_order` 收 `ORDER_CREATION_REJECT` 的 reason 进 evidence；runner 返回 `rejects` | E2/E5 |
| G-T13-9 一天一进程 | `runner._SESSION_USED` 进程级拒绝重入；e2e 全部走子进程 | E2→E3 跨进程 |
| G-T13-1/2（承接 T31） | store 只读 `daily_bars_raw`；`raw_freshness` 陈旧 → 引擎侧 `engine_raw_bars` 返回空；`bundle_factor_last` 供回测线门禁 | 单测 + T31 §3 |

## 4. 影子对账的钱类结论（建议进 M2 验收）

- 同日、同输入、同一起点（`TradingConfig(initial_cash=…, slippage_fixed=0)`）下：
  **成交笔数、持仓数量、T+1 可卖数量**在两引擎间**完全一致**；
- 唯一系统性差异 = **过户费**：rqalpha `StockTransactionCostDecider` 不收 0.001%，
  实测现金差 **0.0222 元/笔**（与 T29 R3 的 −1.9896/−1.9584 同源）。
  ⇒ F-tier 建议判据：**费用差 ≤ 0.001%×成交额 + 0.01 元取整**，
  或者直接给 rqalpha 配 `stock_commission_multiplier` 并**在 L2 补记过户费**（二选一，主 AI 拍板）；
- 影子侧写库隔离：`shadow_day()` 显式比对 `shadow_dir/paper_account.db` 与生产账本路径，
  相同直接 `RuntimeError`；`op_log` 只在 `--allow-op-log` 时写。

## 5. 接 rqalpha 才会踩到的 6 条（全部已处理并写进代码注释）

1. **用户函数里不能做相对导入**。现象：`handle_bar` 内 `from .store import …` 报
   `No module named 'rqalpha.store'`（rqalpha 在 `ExecutionContext` 内回调用户函数时
   解析出的包名不是我们的包）。改成**模块级绝对/包内导入**后通过。
   机制我没有读 rqalpha 源码坐实，只登记现象与规避法（不许当结论传播）。
2. **绑定方法不能当 user_funcs**。`UserFuncStrategyLoader` 用 `getfullargspec` 数参数，
   对绑定方法会把 `self` 也算进去 ⇒ `before_trading() takes 2 positional arguments but 3 were given`。
   规避：runner 用模块级 `before_trading/handle_bar/after_trading` 转发到状态对象。
3. **mod 装载失败会被 ExceptionGroup 掩死**。`mod/__init__.py:56-59` 抛 `ModuleNotFoundError`
   后，`main.py:246` 的 `mod_handler.tear_down` 又因 `_mod_dict` 缺键抛 `KeyError('ptpaper')`，
   最终只见 `ExceptionGroup: Mod tear down failed`。⇒ `runner.describe_error()` 递归剥子异常，
   把真因写进 `error` 字段（本轮 E2 就是靠它定位到 ①/④）。
4. **适配包目录绝不能叫 `rqalpha`**。曾用 `sys.path.insert(0, …/integration/rqalpha)`
   让 rqalpha 找 mod，结果**我们自己那个目录遮蔽了真框架包**（`import rqalpha` 命中我们包，
   随后 `No module named 'rqalpha.store'`）。现改为：目录更名 `rq_engine`，
   mod 入口用 `sys.modules["rqalpha_mod_ptpaper"] = <我们的模块>` 预注册，**完全不碰 sys.path**。
5. **旧引擎的拒单原因也只进 logger**。`paper_broker.py:291-295` `_reject()` 只
   `logger.warning(reason)`，Order 对象与库里都没有原因——与 rqalpha 前端风控同病；
   G-T13-8 因此要覆盖两侧，影子对账里我标成 `reason="see_logger"` 并留注释。
6. **影子对账必须先对齐参数**。`TradingConfig` 默认 `initial_cash=1,000,000`、
   `slippage_fixed=0.01`（`models/types.py:117-127`），rqalpha 侧 `init_cash` 与滑点由我们的
   config 决定——不对齐时第一轮对账冒出 90 万现金差（比的是参数不是引擎）。

## 6. 还欠的（不阻塞 review，但 M3 前要闭合）

- `rq day` 的**接线**：cron/调度仍归主 AI（红线 2 我不动 cron）。
- bundle `ex_cum_factor` 末日的**自动**门禁：函数已给（`bundle_factor_last`），
  在 runner 里只用于报告；拒跑动作建议放在主 AI 的 M2 装配层，避免我单方面加开关。
- NEC 装 rqalpha 的依赖与 pin（属 T30），以及 `numpy<2.4` 的上限；
  产品 venv 装上 rqalpha 后 `rq day` 才可能在生产跑，本分支不改动 `requirements.txt`。
- 分钟频率（`1m`）常驻线：本适配器只支持 `1d`（T26 供数后另开任务）。

## 7. 复现

```bash
# 1) 生产只读导出（NEC）：全池原始价 + 母库 close_raw
ssh nec "cd ~/paper-trading && venv/bin/python /tmp/t32dump.py"   # 见 spikes/e2e_t32.py docstring
scp nec:/tmp/t32_raw.csv nec:/tmp/t32_mother_ref.csv .

# 2) 产品管道落库（写的是临时 db，不碰生产）
python spikes/e2e_t32.py build --raw-csv t32_raw.csv --ref-csv t32_mother_ref.csv \
    --out-db e2e_data.db

# 3) 端到端 16 项（每项一个子进程 = 真重启）
python spikes/e2e_t32.py verify --db e2e_data.db --bundle ~/.rqalpha/bundle --work ./work
# 期望末行：==== T32 e2e: 16/16 PASS ====

# 4) 单测（不需要 rqalpha）
python -m pytest tests/test_rqalpha_adapter.py -q     # 30 passed, 3 skipped
python -m pytest tests -q                              # 387 passed, 4 skipped
```

## 8. 待你拍板的设计选择（我不自行定夺）

1. **包名 `rq_engine`**（原名 `rqalpha` 会遮蔽框架包）——如果主 AI 更希望叫
   `rqadapter`/`rqalpha_engine` 请改，`runner` 里的 `sys.modules` 别名与 CLI 引用要同步。
2. **mod 装载方式**：`sys.modules["rqalpha_mod_ptpaper"]` 预注册（不污染 sys.path）。
   备选是把 mod 做成独立 pip 包（`rqalpha_mod_*` entry point），成本更高但更正统。
3. **影子对账默认不写 `op_log`**（`--allow-op-log` 才写）——避免我这条分支
   在生产账本里留下写路径；如果你希望影子期默认留痕，我把默认翻过来并加白名单。
4. **过户费**：给 rqalpha 配 `stock_commission_multiplier` 近似，还是 L2 补记 0.001%？
   影子对账的 F-tier 判据取决于这个选择（§4）。
