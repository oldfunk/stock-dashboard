# report-t6 — akquant 挑战者 spike（两个否决性问题，二元结论）

执行人：员工 AI（Qoder）｜2026-10-27 深夜
任务：`docs/WORKER.md` T6 ｜背景：主线已定 rqalpha 单引擎（`refactor-plan-open-source.md` §7）
方法：PyPI/pip 解析实测 + 全仓源码取证（13 MB 源码树拉到临时目录，逐文件 grep）
红线遵守：**未碰 Pi、未碰生产环境、未改任何产品代码**

---

## 结论（不许含糊）：**rqalpha 定案，akquant 不反超**

否决题① 通过；否决题② **不过**——akquant 的 Rust 内核**没有 A 股涨跌停撮合带**。
在"撮合语义必须在引擎内"（纪律 3 + §1 红线）这条硬约束下，缺这一项就是否决级：
补它只能在外层做"拒单"，而**成交判定在引擎里**，外层拒单 ≠ 引擎不成交，
等于把 10-27 事故的"同源自洽盲区"重新请回来。

> 但 akquant 不是失败者，是**带条件的 Plan B**（见 §4）。如果哪天母项目对许可洁净度
> 的要求高过对口径完备性的要求，或者我们愿意给上游提 PR 把涨跌停带补进 Rust 内核
> （上游化 = 不算我们自养），它随时可以复议。

---

## ① aarch64 可装性 —— **通过（且比预期更好）**

PyPI `akquant 0.3.71`（2026-10-22）发行文件：

```
akquant-0.3.71-cp310-abi3-manylinux_2_17_aarch64.manylinux2014_aarch64.whl  6.8 MB  ← Pi
akquant-0.3.71-cp310-abi3-musllinux_1_2_aarch64.whl                         7.1 MB
akquant-0.3.71-cp310-abi3-win_amd64.whl                                     8.0 MB  ← 开发机
akquant-0.3.71-cp310-abi3-manylinux_2_17_x86_64.whl                         7.5 MB
akquant-0.3.71-cp310-abi3-macosx_11_0_arm64.whl                             6.4 MB
akquant-0.3.71.tar.gz                                                       2.8 MB
```

**`cp310-abi3` = Rust 稳定 ABI**：一个轮子覆盖 Python 3.10→3.14，不需要按版本重编。

实测（在我本机用 pip 跨平台解析器，等价于在 Pi 上首次求解依赖）：

```bash
pip download --no-deps --only-binary=:all: \
  --platform manylinux2014_aarch64 --python-version 313 -d /tmp/aq_dl akquant
# → 成功取回 akquant-0.3.71-cp310-abi3-manylinux2014_aarch64.whl（无需交叉编译）
pip download --no-deps --only-binary=:all: \
  --platform win_amd64 --python-version 314 -d /tmp/aq_dl2 akquant
# → 成功取回 win_amd64 轮子（开发机 3.14 也能装）
```

运行时依赖 `pandas>=2.2 / numpy>=2.2 / pyarrow / polars / plotly / pydantic`，
**无 Qt、无 TA-Lib**（比 vnpy 干净一个量级，见 `report-t5.md` P1-3）。

**残留不确定性**（需要主 AI 那一枪，我做不了——红线 2 不碰 Pi）：
aarch64 上 `polars`/`pyarrow` 的轮子覆盖与 Pi 内存/SD 卡占用。
一条命令收口：`/home/pi/refactor-spike/.../pip install akquant && python -c "import akquant"`。

## ② 模拟撮合 / T+1 能力 —— **部分通过，缺涨跌停（否决点）**

全仓源码取证（`codeload` 拉 main 分支 13 MB，逐文件 grep）：

| 能力 | 结论 | 证据（文件:行） |
|---|---|---|
| **T+1 冻结/解冻（引擎级，不是字段摆设）** | ✅ | `src/settlement/manager.rs:75`「Centralizes daily settlement logic including T+1 settlement」；`:106-130` `process_daily_settlement` 注释明确 `MarketManager::on_day_close` 把持仓从 positions 移入 `available_positions`（可卖）；开关 `src/engine/python.rs:989 set_t_plus_one`；个股默认 `src/market/stock.rs:20,31 t_plus_one: true`；测试 `src/settlement/manager.rs:989`（T+1 锁定到期不留负可用）、`src/market/stock.rs:157 test_stock_t_plus_one`、`tests/test_t_plus_one.py` |
| **卖出可用校验（含挂单扣减）** | ✅ | `src/risk/stock.rs:7,25-47`：`available - pending_sell < order.quantity` → 拒单 |
| **100 股整手** | ✅ | `src/execution/stock.rs:39`「`check_lot_size = true for Stock`」、`:73 lot_size: dec!(100)`；通用校验 `src/execution/common.rs:126-140` |
| **印花税卖出单边** | ✅ | `src/market/stock.rs:59-61`：注释「印花税 (仅卖出收取)」+ `if side == OrderSide::Sell` |
| **佣金/最低 5 元/过户费** | ✅ | `src/market/stock.rs:15-30`：`commission_rate 0.0003`、`min_commission 5`、`stamp_tax 0.0005`、`transfer_fee`；`:64-65` 过户费双边 —— **与本仓 AGENTS 合规表 #7/#8/#9 默认值逐条一致**（这点比 rqalpha 更贴我们口径，值得记一笔） |
| **模拟撮合 / 常驻（非纯回测）** | ✅ | `python/akquant/execution/sim.py`；`src/execution/simulated.rs:320-323`（成交时投影 available）；live 层 `python/akquant/gateway/{base,__init__}.py`、`docs/zh/advanced/live_functional_quickstart.md`、`docs/zh/textbook/15_live_trading.md`、`examples/31_streaming_live_console.py`、`38/39/41/42_live_*_demo.py`、`docs/{en,zh}/advanced/broker_capability_matrix.md` |
| **涨跌停撮合带（±10/20/30%）** | ❌ **缺失** | 全仓 `grep -rniE "涨跌停\|price_limit\|uplimit\|downlimit" src/` **零命中**；命中项全是限价单语义（`limit_price`，`src/execution/common.rs:37,264-273`）与 MAE/MFE 注释（`src/analysis/tracker.rs:269-490`）。**没有任何按板块分档（主板 10% / 创业·科创 20% / 北交所 30%）的成交拒绝逻辑** |

对照参照物：
- rqalpha：`rqalpha/utils/price_limits.py`（`reaches_limit_up/limit_down` + 向量化版）
  + `mod/rqalpha_mod_sys_simulation/matcher/bar_matcher.py` + `mod/rqalpha_mod_sys_risk/validators/price_validator.py`
- 我们现网自研：`paper_broker.py:310-318` 的 ±10/20/30% 带（AGENTS #4/#5/#6 标"已建模"）

⇒ 迁移到 akquant 会**丢掉一项我们现在有的合规建模能力**，而 §7 定的正是"计算/市场语义交框架"。

## 3. 我没做的两件事（如实）

1. Pi 真机 `pip install akquant`（红线 2；上面是 pip 解析级 + 轮子标签级证据，
   结论方向不会变，但 polars/pyarrow 在 aarch64 的运行期只能真机确认）。
2. 用 akquant 跑通一条 A 股策略 hello（本次只做了源码取证，没执行）。
   如果要执行验证，成本约半天：它的输入是 polars/pyarrow DataFrame，
   我们现有数据是 pandas + SQLite，适配器要现写——**这本身就是 §7 遗留的 M0b 同类工作量**。

## 4. 留给主 AI 的 Plan B 触发条件（三条任一成立就复议 akquant）

1. 母项目/子项目对**许可洁净度**要求上升（rqalpha 是 Apache-2.0 + 非商业条款，
   "任何法人不得为任何目的使用"，与本仓 MIT + subtree 计划有现实张力——见 `report-t5.md` P1-4）。
   akquant 是**纯 MIT**，这条上它是零风险。
2. **3.3 GB bundle / 月度更新**在 Pi 上成为运维痛点（akquant 是 8 MB 轮子 + 自带数据适配器接口）。
   §7.3 已把"新引擎只吃我们两源之一、bundle 仅作日历/权息/停牌/ST 元数据"定死，
   所以这条痛是可承受的——但如果它疼，akquant 是出口。
3. 我们决定给 akquant 上游 PR 补涨跌停带（补进 Rust 内核，**上游化后由社区维护**）。
   这是唯一能让 akquant 变成"既 MIT 又口径完备"的路径，也是社区协作的正解。

## 5. 对 §7.3 M0 收口的影响

- [x] akquant 挑战者 spike（本报告）→ **不翻盘，rqalpha 定案**
- [ ] rqalpha `AbstractDataSource` 接 SQLite 原型（M0b，主 AI 或我下一轮）
- [ ] H-1..H-8 复核修复（主 AI 在做）
- 建议把「涨跌停带必须在引擎内」写进 M1 验收硬条款——这样任何后续框架更换
  都自动被这一条筛掉，不用重新开一轮选型讨论。

---

### 复现命令（本报告全部已执行）

```bash
# ① 轮子覆盖与跨平台解析
curl -s https://pypi.org/pypi/akquant/json | python -c "import json,sys;d=json.load(sys.stdin);v=d['info']['version'];print(v);[print(f['filename'],f['size']//1024,'KB') for f in d['releases'][v]]"
pip download --no-deps --only-binary=:all: --platform manylinux2014_aarch64 --python-version 313 -d /tmp/aq_dl akquant

# ② 源码取证
curl -sL https://codeload.github.com/akfamily/akquant/tar.gz/refs/heads/main | tar -xz
grep -rniE "涨跌停|price_limit|uplimit|downlimit" akquant-main/src          # 零命中
grep -rn  "t_plus_one\|available_positions\|check_lot_size\|stamp_tax" akquant-main/src | head -40
sed -n '58,66p' akquant-main/src/market/stock.rs        # 印花税仅卖出
sed -n '106,131p' akquant-main/src/settlement/manager.rs # T+1 日终结算
```
