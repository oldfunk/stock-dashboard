# report-t5 — 迁移方案反驳性审查（T5'）

审查对象：`docs/refactor-plan-open-source.md`（commit `5c572c6`，2026-10-27 18:06）
审查人：员工 AI（Qoder）
实测环境：Windows / `C:\Users\yanzh\venvs\refactor-spike`（Python 3.13 隔离 venv）
　　　　　+ 系统 Python 3.14.0（本仓 306 项测试当前所用）
方法：pip 解析实测 + GitHub 源码/元数据取证 + PyPI 发布事实；每条结论附复现命令（§9）

> **一句话结论：方案的框架组合（vnpy 交易线 + rqalpha 回测线）建立在 §2 表格里三条可证伪
> 断言之上，本审查用实测推翻了其中三条中的两条半。M0 不建议按现方案执行——它会把 1–2 天
> 花在一个不存在的前提上（vnpy 内建 T+1 撮合），并把本仓最硬的纪律（回测与实盘同一判定）
> 主动打破。§7 的用户拍板是在错误事实下做的，需要重新告知后再拍一次。**

---

## P0-1 vnpy 不含撮合引擎，也不含 A 股 T+1 语义 —— 实测推翻

**方案原文**（§2 vnpy 行）：「撮合引擎+账户+风控+条件单（STOP/LIMIT/MARKET）……
**T+1 冻结/可用语义内建**」，并据此把 vnpy 定为「✅ 交易执行底座首选」。

**实测**（Python 3.13 venv，`pip install vnpy` → 装到 4.5.0）：

```
vnpy 4.5.0
vnpy subpackages: ['alpha', 'chart', 'event', 'rpc', 'trader']
trader has matcher? False []
vnpy_ctastrategy:   MISSING (ModuleNotFoundError)
vnpy_paperaccount:  MISSING (ModuleNotFoundError)
vnpy_riskmanager:   MISSING (ModuleNotFoundError)
```

**源码取证**：撮合/回测在**独立插件仓库** `vnpy/vnpy_ctastrategy`（227★、2026-10-05 更新），
其 `backtesting.py`（1294 行）的撮合与成本入参只有：

```
self.rate / self.slippage / self.size / self.pricetick      # 期货式：合约乘数 + 双边费率
```

——没有 T+1 冻结/可用、没有涨跌停拒单、没有 100 股整手、没有卖出单边印花税。
核心仓检索 `T+1` 只命中 `vnpy/alpha/dataset/factor_performance.py`（因子评测，与撮合无关）。

**后果（这就是"狠狠地"那一条）**：选 vnpy 做交易线，A 股合规层必须**自己再写一遍**——
而 §1 红线明写"胶水层不含任何撮合/计价/判定逻辑"。也就是说：**照方案做，第一周就会违反
方案自己的红线，而且"零自研"的目标原地落空**。用户这次的诉求恰恰是"别再什么都自己维护"。

**补充**：vnpy 的股票实盘路径依赖券商网关（XTP/OES/华鑫奇点 Tora/宽睿 OES/QMT/PTrade），
与本仓红线「不接券商实盘」冲突；`vnpy_paperaccount` 是"镜像真实网关撮合"的模拟账户，
没有行情网关就没有它——它不是"离线模拟盘"。

---

## P0-2 「rqalpha 仅回测、本地无实盘撮合」不成立 → 双框架的立论前提消失

**方案原文**（§2 rqalpha 行）：「模拟盘撮合：**仅回测（本地无实盘撮合）**」——这是把交易线
分给 vnpy 的**唯一理由**。

**源码取证**（`ricequant/rqalpha@master`，今天仍有提交）：

- `rqalpha/mod/rqalpha_mod_sys_simulation/simulation_event_source.py`（模拟事件源）
- `rqalpha/mod/rqalpha_mod_sys_simulation/matcher/{bar_matcher,tick_matcher,signal_matcher,base}.py`
- `rqalpha/utils/price_limits.py`：`reaches_limit_up/limit_down` + **向量化版本**（涨跌停判定）
- `rqalpha/mod/rqalpha_mod_sys_risk/validators/price_validator.py`（事前风控拒超限价单）
- `rqalpha/mod/rqalpha_mod_sys_transaction_cost/deciders.py` 内含
  `STOCK_PIT_TAX_CHANGE_DATE = datetime(2023, 8, 28)`——**正是 AGENTS.md 合规表 #8 引用的
  财政部 2023-39 号公告（2023-08-28 印花税减半）**，说明它的费用模型是照 A 股真实规则做的
- `rqalpha/mod/rqalpha_mod_sys_accounts/`（T+1 出现在其 README/API 中）、`data/trading_dates_mixin.py`
- README.rst:32 自述：「从数据获取、算法交易、回测引擎，**实盘模拟**，实盘交易到数据分析」

**后果**：同一个引擎、同一份策略即可覆盖回测与模拟盘 ⇒ **双引擎架构不必要，而且有害**：

| 冲突点 | 本仓既有约束 |
|---|---|
| AGENTS.md 路线图纪律 3 | 「回测与实盘**必须共用同一套判定**；改任一侧必须同步另一侧并补对照测试」——双引擎=永久化的"两侧" |
| 10-27 事故复盘 §5 | 「独立基准原则」：**同源自洽挡不住尺度错误**；两套撮合互为对照会产生"假一致性" |
| §5 M2 验收 | 「一周影子对照**零分歧**」——两个引擎撮合语义/取整/时点不同，零分歧不可能，验收会长期红，或被迫把容差松成没意义 |

---

## P1-3 依赖现实被低估：vnpy 会把 pandas 静默升到 **3.1**，并拖 1 GB GUI 栈 —— 实测

`pip install vnpy`（3.13）实际落盘：

```
Successfully installed ... vnpy-4.5.0 pyside6-6.8.2.1 shiboken6 ta-lib-0.8.2 deap-1.4.4
  pyqtgraph qdarkstyle qtpy nbformat jupyter-core plotly pyzmq ...
venv 体积: 1061 MB / 73 个包 / 安装耗时 120 s
pandas 版本: 3.1.0        ← 关键
```

- 本仓 `requirements.txt` 是 `pandas>=2.0.0`，306 项测试与 akshare 管线当前跑在 pandas 2.x。
  **pandas 3.0 是破坏性大版本**（Copy-on-Write 默认、字符串 dtype 变更、弃用 API 移除）。
  方案 §6 风险清单只写了"GUI 体积/TA-Lib/arm64"，**完全没提 pandas 3 迁移**——而这是最先炸的一环。
- 方案 §6-1 的机制也说错了：`vnpy 4.0.0/4.3.0/4.4.0/4.5.0` 的 `requires_python` **全是 `>=3.10`**
  （PyPI 元数据），3.14 下 pip 退回 4.0.0 是**传递依赖 wheel 覆盖**造成的（同一 dry-run 里
  PySide6 在 3.14 解析到 6.12、且 3.14 结果里 `narwhals/tzdata` 集合都变了）。
  结论"降 3.13"碰巧成立，但**如果 vnpy 被否（见 P0-1），§7-C 的"全线统一 3.13"就没有必要**——
  `rqalpha 6.4.2` 与 `akquant` 在 3.14 都能干净解析（本审查实测）。请不要为一个不存在的约束
  把全仓解释器降级。

---

## P1-4 许可证：rqalpha 的"个人研究可用"比方案写得重，且**现在就**与本仓 MIT + subtree 契约冲突

`LICENSE` 原文（raw 抓取，非 SPDX 标准条款，GitHub 侧显示 `NOASSERTION` 即因此）：

> 商业用途指个人出于任何商业目的使用本软件……未经米筐科技授权，任何个人不得出于任何商业目的
> 使用本软件（**包括但不限于……引用或借鉴了本软件功能或源代码的产品或服务**），
> **任何法人或其他组织不得出于任何目的使用本软件**。

事实叠加：本仓 `LICENSE` = **MIT（公开 GitHub 仓）**，且 `MERGE_GUIDE.md` 计划把本仓 subtree
并入母项目 stock-dashboard。把带非商业条款的依赖写进核心代码路径 = 下游接收者拿到的组合
无法合规商用；「未来商业化再处理」（§6-3）低估了：**合并契约 §2/§3 的措辞现在就要写清**。

另一个方案没说的点：rqalpha 的 mod 生态**全线停更**——`rqalpha-mod-tushare`(2018)、
`mod-ctp`(2018)、`mod-stock-realtime`(2018)、`mod-vnpy`(2021)。**不要指望现成的实时/数据 mod**，
接 akshare/母库必然是自研 `AbstractDataSource`（见 P1-5）。

---

## P1-5 数据账没算：免费 bundle 是月度；自定义 DataSource 要你补"权息/停牌/ST/日历/元数据"

- rqalpha 官方说明（README/gitee 镜像）：**4.x 起的 bundle 改为月度更新**，随时更新需 RQData
  （商业 API，试用/私有化部署）。
- 自定义数据需实现 `AbstractDataSource`（仓内 `rqalpha/examples/data_source/`、
  `docs/source/development/event_source.rst`），并喂给它：交易日历、**停牌**、**除权除息/复权因子**、
  ST 标记、instrument 元数据（`calc_cash_occupation` 等按类型分支）。
- 本仓现状：AGENTS.md 合规表 #16 明写除权除息只做"检测 + 人工 `corporate apply`"，
  **没有** 复权因子/停牌/ST 结构化数据。
- ⇒ 迁移把"删掉的自研撮合"换成"新增的自研数据适配器 + 权息数据采购"，
  与用户诉求（少维护）**方向相反**。这笔账要量化后再选，不是风险清单里一句话。
- 顺带：本仓已有**只读母库**（stock-dashboard）含 `kline_daily / trading_calendar / dividend`，
  是天然适配器源，但方案 §3 只写"数据适配器"，**没定走母库还是 akshare**——而"两套口径谁做准"
  正是 10-27 事故的根因。这里不定清楚，尺度污染会换个位置复发。

---

## P1-6 候选评估漏项：至少两个更贴合的框架没进表

| 候选 | 客观事实（本审查取证） | 为什么该进表 | 如实说风险 |
|---|---|---|---|
| **akquant**（`akfamily/akquant`） | MIT、2469★、pushed **2026-10-26**、v0.3.71（2026-10-22）、**近 7 天发 5 版**、Rust 内核、`requires-python>=3.10`、依赖仅 `pandas>=2.2 / numpy>=2.2 / pyarrow / polars / plotly / pydantic`（**无 Qt、无 TA-Lib**） | 与本项目**数据源同 org（AKShare）**；文档明确 A 股 **T+1**（`tplusone=True`、`getavailableposition` / `Position.available` 区分总持仓与可用）、可配佣金率 + 印花税；许可干净、无 GUI → Pi/Windows 都装得动 | 0.3.x 年轻、社区小；是否覆盖实时/模拟盘需 spike 实测；与本仓 SQLite 账本对接是工作量 |
| **hikyuu**（`fasiondog/hikyuu`） | Apache-2.0（GitHub）/ MIT（PyPI 标注，**两处不一致需确认**）、3645★、pushed **今天**、**3.0.0 今天发布**、C++ 内核 | 第三方实测覆盖 A 股 T+1/涨跌停/手续费滑点/权息，性能量级高于纯 Python；组合级回测体系完整 | **3.0.0 的 PyPI wheel 只有 `macosx_11_0_arm64` 与 `manylinux2014_aarch64`，没有 Windows**（实测列表）⇒ 本机开发要自编译 C++（硬伤，反过来说 Pi aarch64 可直接用）；3.0 是大版本重写，API 迁移成本另评 |
| nautilus_trader | LGPL-3.0-or-later、30929★、pushed 今天、v1.231、`py<3.15,>=3.12` | **回测与实盘同一引擎**是它的立身之本 = 最合纪律 3 的架构 | 无中国数据/无 A 股规则，instrument+fee 要自写 = 回到自研；工程量最大 |
| backtesting.py | AGPL-3.0、9150★、2026-08 更新 | 轻量单标的 | **AGPL 对 MIT 仓 + subtree 合并基本否决**，写进表是为了留下"为什么不选"的证据 |
| quantstats | Apache-2.0、7757★、2026-09 更新 | 可只替换自研 Sharpe/MDD/tearsheet 这一段（低风险、独立收益） | 不是引擎，只解决一小块 |

方案的表里有 qlib / QUANTAXIS / backtrader，却**没有 akquant**——而 akquant 与本项目已经在用的
akshare 出自同一个 org。这是选型评估的实质性遗漏，不是口味差异。

---

## P2-7 §1 的"零自研"红线与 §3 的映射自相矛盾（第一天就会被破）

- §1 红线：胶水层「**不含任何**撮合/计价/判定逻辑」。
- §3 映射却写：规则指标计算「改用框架技术指标**或 pandas 直算**（指标非撮合判定，允许薄层）」，
  且「AI 条件 JSON → 翻译器」。
- 但"触发价取实时价还是日线收盘价""条件值带宽判定""计价基准"本身就是**判定与计价**——
  10-27 事故根因恰恰是价口径在自研代码里。
- 建议把红线改成可判定形式：**"任何影响成交价、持仓、盈亏、下单/拒绝结论的数值，必须来自框架
  API 返回值；框架不做的合规判定必须登记为『自研保留件』并由 parity 测试锁死"**。
  口号式的"零自研"只会让第一个 Sprint 就违反规则、然后规则被集体忽略（本仓历史上配置白名单
  断层就是同类教训）。

---

## P2-8 验收门禁设计缺陷

1. M1 写「容差书面化」但**未给容差**——无法验收。先定：成交笔数差 0、费用差 <1e-6、
   收益差 <0.1pp、逐单价格取整一致。
2. M2 写「影子对照一周零分歧」——见 P0-2，双引擎下不可能。改为**单引擎**后此项才有意义。
3. **缺事故回归门禁**：应把 10-27 事故日（母库后复权 175.96 vs 真实 22.24、虚付 15,372）
   固化为强制回归用例，新引擎先过它（`agent replay` + 尺度闸 + 基准价 fail-closed），
   再看均值收益。任何"框架接管撮合"的 PR 不带这条回归 = 不放行。
4. 缺**数据口径门禁**：迁移期必须双跑 `data compare`/`data coverage`（本仓已有），
   并强制走 `_scale_guard` 后的数据；否则尺度污染只是换了宿主。

---

## P2-9 流程问题（对事不对人）

- §7 的"用户已拍板"三项，前提是 §2 表格的实测断言；其中 P0-1、P0-2、P1-3 的机制已被本审查
  实测推翻 ⇒ **拍板需重新告知**：vnpy 内建 T+1 不存在、rqalpha 能做模拟撮合、3.13 降级只为 vnpy。
- §2 标称"2026-10 实测 + 检索证据"，但"T+1 冻结/可用语义内建"这一格没有代码出处。
  建议选型表每格挂**证据（文件:行 / 命令 / URL）**，本报告的 §9 全部命令可直接引用。

---

## 反建议（最小改动版，供主 AI 取舍）

1. **冻结 vnpy 交易线**（除非能给出 vnpy 侧 A 股 T+1/涨跌停/印花税的代码出处）。
   M0 改为**三选一 spike**：rqalpha（单引擎覆盖回测+模拟撮合）/ akquant（数据同族、无 GUI）/
   hikyuu（性能与权息，Windows 需编译）。评估维度固定并打分：
   A 股口径内建度 · 依赖体积与可装性(Windows+aarch64) · 许可证 · 数据供给成本 ·
   是否同一判定(纪律 3) · 升级跟踪成本。
2. **交易线与回测线必须同一引擎**。若确有实时缺口，用「引擎 + 自研薄事件源」（只搬运 bar/tick，
   不含判定），而不是引入第二个撮合引擎。
3. **账本先不迁**。`paper_account.db` 被 MERGE_GUIDE §2 表契约、面板、op_log 审计共用；
   新引擎以影子账本并行，通过"事故回归 + 逐单对照"≥N 周后再切，切换日按 §4 方案归档新开。
4. **Python 3.13 vs 3.14 的决策挂到第 1 条之后**，不要提前定。
5. **许可与合并契约**：若最终保留 rqalpha 路径，需在 README/CONTRACT/MERGE_GUIDE 明示
   「依赖含非商业条款，本仓组合不可商用分发」，或改选纯 MIT/Apache 路线。

---

## §9 复现命令（本审查全部已执行，结果贴在上文）

```bash
# 隔离 venv（不碰生产环境、不碰现有 venv、不改产品代码）
py -3.13 -m venv C:/Users/yanzh/venvs/refactor-spike
V=/c/Users/yanzh/venvs/refactor-spike
$V/Scripts/python.exe -m pip install vnpy           # → vnpy 4.5.0 + PySide6 + ta-lib + pandas 3.1.0
$V/Scripts/python.exe -m pip install rqalpha        # → rqalpha 6.4.2
du -sm $V ; $V/Scripts/python.exe -m pip list | wc -l   # 1061 MB / 73 包

# vnpy 核心包里没有撮合引擎/插件
$V/Scripts/python.exe -c "import pkgutil,vnpy,vnpy.trader as t;print([m.name for m in pkgutil.iter_modules(t.__path__)])"

# vnpy 撮合在独立插件仓，成本模型为期货式
curl -sL https://raw.githubusercontent.com/vnpy/vnpy_ctastrategy/main/vnpy_ctastrategy/backtesting.py \
  | grep -nE "self\.(rate|slippage|size|pricetick)"

# rqalpha 的 A 股语义文件
gh api "repos/ricequant/rqalpha/git/trees/master?recursive=1" --jq '.tree[].path' \
  | grep -E "price_limits|matcher|transaction_cost|simulation_event_source|trading_dates_mixin"

# 许可证原文
curl -sL https://raw.githubusercontent.com/ricequant/rqalpha/master/LICENSE | head -25

# PyPI 事实（requires-python / 发布节奏 / wheel 平台覆盖）
for p in vnpy rqalpha akquant hikyuu backtesting nautilus-trader quantstats; do
  curl -s "https://pypi.org/pypi/$p/json" | python -c "import json,sys;d=json.load(sys.stdin)['info'];print(d['name'],d['version'],d['requires_python'],d['license'])"
done
```

---

## 本审查自身未完成项（如实交代）

- vnpy 无 GUI 的「下单→成交→T+1 冻结→账户」hello **没跑**——因为 P0-1 已经证明核心包不含
  撮合引擎，要跑得先把 `vnpy_ctastrategy` + 数据库后端 + (可选)GUI 组装起来；组装本身就是
  M0 要回答的问题（1061 MB / 73 包 / 120 s 是实测底数）。
- akquant / hikyuu 我只做了**发布事实与许可/依赖取证**，没跑通策略；表里的"该进表"是
  评估资格，不是选型结论。
- 本报告**不改任何产品代码**（红线遵守：未跑任何写账本命令，未碰 Pi/secrets/cron，未 force push）。

---

## §10 spike 后半程实测（补测后追加：含对我自己 P1-5 的更正）

### 10.1 rqalpha 端到端确实跑得通（M0-③ 技术可行）

```
pip install rqalpha      → 6.4.2（与 vnpy 4.5.0 并存于同一 venv，无冲突）
rqalpha version          → Current Version: 6.4.2
rqalpha download-bundle  → 数据源 http://bundle.assets.ricequant.com/bundles_v4/rqbundle_202610.tar.bz2
                            264 s 完成，落盘 3.3 GB，14 个数据文件
run_func(BACKTEST)       → 正常返回 sys_analyser.summary（无异常、无网络依赖）
pandas                   → 3.1.0（rqalpha 同样把 pandas 拉到 3.1，见 P1-3）
```

### 10.2 **更正 P1-5**：bundle 自带权息/停牌/ST，我原话"要你补元数据"说重了

`~/.rqalpha/bundle` 实际内容：

```
dividends.h5  ex_cum_factor.h5  split_factor.h5  share_transformation.json
suspended_days.h5  st_stock_days.h5  trading_dates.npy(20050104→20271231)
instruments.pk  stocks.h5  indexes.h5  funds.h5  yield_curve.h5  future_info.json
```

⇒ 除权除息累计因子、拆股、停牌日、ST 日这些**本仓没有**的结构化元数据，rqalpha 是**白送**的
（本仓 AGENTS 合规表 #16 目前只做"检测 + 人工 `corporate apply`"）。这一条对 rqalpha 有利，
我不含糊。P1-5 的真正成本要改成下面这三项：

1. **3.3 GB + 月度刷新**：Pi（SD 卡）上放 3.3 GB bundle 且每月整包更新，是运维成本不是零成本；
   `trading_dates.npy` 虽预生成到 2027-12-31，但**价格数据只到 2026-09/10**（月度包），
   实时性必须靠自写 `AbstractDataSource` 接 akshare/母库——适配器工作仍在，只是范围比我原写的窄。
2. **第三个价格口径**：本仓现在已经有 ①akshare 本地前复权 ②母库 `close_qfq`（缺列逐行降级
   `close_adj`）。rqalpha bundle 是**第三个源**（自带复权因子）。10-27 事故的根因就是口径混用；
   方案 §3 没写"新引擎吃哪份价、与现有两源如何对账"。这是必须在 M0 定死的一条，否则尺度污染换宿主复发。
3. **免费包与付费包的界线**：随时更新走 RQData（商业），mod 生态（tushare/ctp/stock-realtime）
   全部 2018–2021 停更——别指望现成实时源。

### 10.3 [P2] rqalpha 对越界/未配置窗口**不报错，只吐 NaN + warning**（对我自己的配置失误敏感）

我这次把 `start_date/end_date` 放在 config 顶层（rqalpha 6 期望在 `base` 下），于是它按
bundle 的日历把窗口当 **2015-06-01 → 2027-12-31** 跑，尾部没有价格数据，结果：

```
summary: {'start_date': '2015-06-01', 'end_date': '2027-12-31', 'starting_cash': '',
          'alpha': nan, 'beta': nan, 'sharpe': nan, 'excess_sharpe': nan,
          'information_ratio': nan, 'downside_risk': nan, 'tracking_error': nan, ...}
RuntimeWarning: divide by zero encountered in log1p   (rqrisk/risk.py:119, pandas arraylike.py:438)
WARN: system_log: The strategy requires explicit configuration of base.capital_gain_tax_rate …
```

**没有异常、没有非零退出码**——只有 stderr 里的 warning。对本仓的直接含义：
`engine/optimize.py` 按 `excess_pct/sharpe` 排序、`walkforward` 用样本外超额做晋级判断，
一旦接上这类指标，**NaN 会静默进入排名**（NaN 比较恒为假 → 排名错位而不报错）。
本仓纪律要求"不静默"（路线图纪律 4），AGENTS 硬规则 1 要求"绝不编造数据"；
框架拿 NaN 当结果返回时，**必须在胶水层做 `math.isfinite` 白名单校验**，并把 `starting_cash: ''`
这类空串字段一并拦下。建议写进 M1 验收条款：
「新引擎结果进 optimize/walkforward/面板之前，必须过非有限值门禁，违反即整轮作废并落 `op_log`」。

### 10.4 对结论的影响

- P0-1 / P0-2 / P1-3 / P1-4 / P1-6 **不变**（都是实测/源码事实）。
- P1-5 按 10.2 收窄：数据适配成本从"高"降为"中"，但**新增"第三价格口径"这一硬风险**，
  并且 3.3 GB/月更对 Pi 是实打实的运维项。
- 反建议第 1 条（三选一 spike）现在有了基线数据：rqalpha 装得起、跑得通、指标会吐 NaN；
  vnpy 装得起但要 1 GB 且核心无撮合；akquant/hikyuu 待测（hikyuu **无 Windows wheel** 已知）。
