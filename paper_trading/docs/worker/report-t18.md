# report-t18 — 分钟频率可行性（D1 关键实验）

执行人：员工 AI（Qoder）｜2026-10-28
环境：`C:\Users\yanzh\venvs\refactor-spike`（Py3.13 + rqalpha 6.4.2 + bundle `rqbundle_202610`）

## 结论

> **机制可行，数据不可行。**
> rqalpha 在**分钟频率下确实支持 T+1 开盘撮合**（`next_bar` 只在日线被强制降级）；
> 但**bundle 完全不含分钟数据**，且我们的分钟线只有 **5 分钟 × 约 22 个交易日**，
> 覆盖不了 8 个月回测 ⇒ **D1「回测线口径统一到 T+1 开盘」不能靠"rqalpha 1m + bundle"实现**。
>
> **给主 AI 的裁决建议**：
> - **M2 模拟线（T13）是 T+1 的现实路径**（事件源天然"信号 T 收盘、成交 T+1 开盘"）；
> - **回测线（M1）在补齐自有分钟库之前，只能接受 rqalpha 日线 `current_bar` 语义**，
>   并按 §5.3 的 **F 级临时容差**书面声明固有偏差（实测 mean 0.375%/笔，见 `report-t9.md` L4）；
> - 另开一条**低成本数据积累线**：从现在起每日落 5 分钟线入库（T+1 首根 5 分钟 ≈ 开盘价），
>   攒够窗口后再把回测线升到分钟频率。这条不做，D1 永远闭不掉。

## 一、机制侧证据：分钟频率下 `next_bar` 不被降级

`rqalpha/mod/rqalpha_mod_sys_simulation/mod.py`

```
L52-57  else:                       # 非 tick 频率
            if mod_config.matching_type not in [
                MATCHING_TYPE.NEXT_BAR_OPEN,      ← T+1 开盘语义
                MATCHING_TYPE.VWAP,
                MATCHING_TYPE.CURRENT_BAR_CLOSE,
            ]: raise RuntimeError(...)
L59-62  if env.config.base.frequency == "1d" and matching_type == NEXT_BAR_OPEN:
            mod_config.matching_type = CURRENT_BAR_CLOSE
            user_system_log.warn("matching_type = 'next_bar' is abandoned when frequency == '1d'")
```

⇒ 降级条件**只写 `frequency == "1d"`**。分钟频率下 `NEXT_BAR_OPEN` 保留，
撮合语义与"无未来函数（T 收盘信号 → T+1 成交）"要求一致。

## 二、数据侧证据：bundle 零分钟数据 + base source 显式拒绝非日线

1. `~/.rqalpha/bundle` 全量 14 个文件（**无任何 minute/1m/tick 文件**）：
   `dividends.h5 ex_cum_factor.h5 funds.h5 future_info.json futures.h5 indexes.h5
   instruments.pk share_transformation.json split_factor.h5 st_stock_days.h5
   stocks.h5 suspended_days.h5 trading_dates.npy yield_curve.h5`
2. 源码只注册了 5 个 store 抽象（`data/base_data_source/storage_interface.py`：
   `AbstractDayBarStore(L29)` / `Calendar(38)` / `DateSet(44)` / `Dividend(51)` / `SimpleFactor(57)`）
   ——**没有分钟 store**。
3. `data/base_data_source/data_source.py`：
   - `L230-232 get_bar()`：`if frequency != '1d': raise ...`
   - `L321-334 history_bars()`：只支持 `'1d'/'1w'`
   - `L406-412 available_data_range()`：只识别 `'tick'/'1d'`，其余返回 **None**
4. **实测崩溃**（`frequency: "1m"` + `matching_type: "next_bar"`，2 天窗口）：

```
File ".../rqalpha/main.py", line 52, in _adjust_start_date
    start, end = data_proxy.available_data_range(config.base.frequency)
TypeError: cannot unpack non-iterable NoneType object
ERROR: user_system_log: 策略运行产生异常
```

⇒ 想跑分钟回测，**必须自己实现一个 DataSource**，至少覆盖 4 个方法：
`available_data_range('1m')`、`get_bar(...,'1m')`、`history_bars(...,'1m')`、
`current_snapshot(...)`（`interface.py` 对应 L522/L379/L429/L491，与 T7 接口表同一张表）。
这**不是配置能解决的**，而是 T13（live-sim 原型）那类工程量。

## 三、我们手上的分钟数据现实（决定性约束）

`paper_trading/data/minute.py`：

```
L1   """1 分钟 K 线采集（盘中量化用）：新浪 N 分钟 K 线 + 腾讯分时。"""
L3   为什么不用东财：东财 1 分钟接口在部分网络环境（如 Pi）会直接断连   ← AGENTS 引擎纪律 6
L6   新浪 N 分钟 K 线（默认 5 分钟）：多日历史（实测 22 个交易日）、真实 OHLC
L8   腾讯分时：今日 1 分钟 …… 只在显式要 1 分钟时用
L27  MAX_DAYS = 30        # 新浪可取的历史长度（按 N 分钟折算交易日）
```

| 需求 | 现实 | 缺口 |
|---|---|---|
| T9 回测窗口 2026-01-05→09-10 ≈ **167 个交易日** | 新浪 5 分钟最多 **约 22 个交易日**，且不可回补 | **约 7.6 倍**，且历史无法追补 |
| 1 分钟粒度 | 只有"今日"1 分钟（腾讯分时） | 无历史 1 分钟 |
| 5 分钟能否代表"T+1 开盘" | 可以：T+1 首根 5 分钟的 open ≈ 开盘价（9:30–9:35） | 需要落库口径统一 |

⇒ 就算写了 DataSource，**没有 8 个月的分钟历史**，回测线仍跑不起来。
这是数据可得性问题，不是框架问题。

## 四、代价与建议路径

| 方案 | 做法 | 代价 | 评价 |
|---|---|---|---|
| P1 接受日线 `current_bar`（M1 现状） | 回测按当日收盘成交，F 级容差书面声明 | 0 | **短期唯一可签**；但回测口径与实盘"T+1 成交"**不一致**，需在文档写明"回测偏乐观"（用 L4 数据量化：mean 0.375%/笔、max 7.016%） |
| P2 自建分钟 DataSource + 自有分钟库 | 实现 4 个接口 + 每日落 5 分钟线，攒 ≥170 交易日 | 中等；**需 ~8 个月自然积累**（无法追补历史） | **推荐并行启动**：从今天开始落库，等数据够了再把回测线升频；这条做完 D1 才真正闭合 |
| P3 走 M2 模拟线验证 T+1 | T13 的 live-sim 原型（事件源天然 T+1） | 已在做 | **现实路径**，但它验证的是模拟盘，不是回测；两者口径仍不同 ⇒ 需要 S 级信号门禁兜住（T19） |
| P4 换支持分钟的第三方数据 | Tushare Pro / RQData 付费 | 花钱 + 与"免费 akshare"路线冲突 | 主 AI 决策；若买，优先买 5 分钟历史（够用且便宜） |

**建议组合**：**P1（现在可签）＋ P2（今天开始落库，作为长期解）＋ S 级门禁（T19）保证判定层不漂移**。
不要选"为了 D1 立刻上分钟回测"——数据不支持，只会做出一堆空跑的 spike。

## 五、顺带抓到的一个 rqalpha 缺陷（值得上游报或我们防御）

`frequency` 配错（bundle 无该频率数据）时，rqalpha 不是给出清晰的用户错误，
而是 `main.py:52` 拿 `None` 解包抛 `TypeError`，并被包成 `CustomException("策略运行产生异常")`。
我们的胶水层若替它包一层配置校验（`available_data_range(freq) is None` ⇒ 直接拒跑并说明原因），
可以避免运维看到无信息量的 TypeError。建议列入 M1 的"配置前置校验"清单。

---

### 复现命令

```bash
V=/c/Users/yanzh/venvs/refactor-spike/Scripts/python.exe
$V - <<'PY'
from rqalpha import run_func
import os
cfg={"base":{"start_date":"2026-06-01","end_date":"2026-06-05","accounts":{"STOCK":100000.0},
             "frequency":"1m","data_bundle_path":os.path.expanduser(r"~\.rqalpha\bundle"),
             "capital_gain_tax_rate":0.0,"locale":"zh_CN"},
     "mod":{"sys_simulation":{"matching_type":"next_bar","slippage":0}}}
run_func(init=lambda c: None, handle_bar=lambda c, b: None, config=cfg)
PY
ls ~/.rqalpha/bundle                       # 14 个文件，无分钟
sed -n '36,62p' <site-packages>/rqalpha/mod/rqalpha_mod_sys_simulation/mod.py
sed -n '230,232p;321,334p;406,412p' <site-packages>/rqalpha/data/base_data_source/data_source.py
```
