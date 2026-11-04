# M0 Spike 报告：Pi arm64 headless（2026-10-27）

> 环境：Pi（aarch64，Python 3.13.5），隔离 venv `/home/pi/refactor-spike/venv`，
> **未触碰生产环境**（`/home/pi/paper-trading` 全程未动）。
> 对应方案：`docs/refactor-plan-open-source.md` §5-M0。

## 结论：两大框架均可在 Pi arm64 headless 运行 ✅

| 验证项 | 结果 |
|---|---|
| `pip install --no-deps vnpy` | ✅ **vnpy 4.5.0**，2 秒装完（纯 Python 核心） |
| vnpy 最小依赖补全 | ✅ `tzlocal loguru pyzmq numpy pandas ta-lib tqdm`（ta-lib 0.8.2 **有 aarch64 轮子**，自带 C 库，无需系统编译） |
| vnpy 核心导入 | ✅ event/utility/object/constant/engine/gateway/database/setting/app 全部 OK |
| **MainEngine 实例化（headless）** | ✅ `MainEngine(EventEngine())` 成功，引擎注册：`oms/log/email/wechat`——**交易核心可用** |
| `pip install rqalpha`（完整） | ✅ **rqalpha 6.4.2**，h5py/statsmodels 均有 aarch64 轮子，导入正常 |
| 跳过的 GUI 依赖 | pyside6==6.8.2.1 / qdarkstyle / qrcode（仅 GUI 需要；`--no-deps` 绕过，无警告噪音） |

## 关键发现（影响 M1/M2 设计）

1. **vnpy 4.5.0 wheel 是瘦身核心**：顶层仅 `alpha/chart/event/rpc/trader`
   ——2.x 的 `app/`（backtester/paper_trading 等应用）**不在 wheel 里**。
   `vnpy.trader.template`、`vnpy.trader.backtest` 模块不存在（探针 MISS）。
2. **回测/撮合逻辑新位置**：`vnpy.alpha.strategy.backtesting`（4.0 新 AI 模块，
   含撮合实现）+ `vnpy.alpha.strategy.template`（策略模板）。
3. **未决项（M0b，M1 第一周解决）**：**常驻模拟撮合网关**（live paper trading）
   在 4.x 的载体——PyPI 无 `vnpy_paper_trading` 包；候选：
   (a) GitHub 主仓的 app 层（wheel 外安装）；(b) `vnpy.alpha` 撮合逻辑作库复用；
   (c) VeighNa 文档确认的官方模拟通道。**需要读 4.5 源码/文档定论**，
   已补进方案风险清单。
4. vnpy 4.5 的 OMS 引擎含订单/持仓/成交管理（PositionData 含昨仓/冻结语义，
   T+1 可表达）——账本迁移可行性进一步确认（M2 细验）。

## 遗留

- [ ] 桌面侧 spike（Windows 3.13 venv 全量安装 + GUI 路径）→ 员工 AI T5'
- [ ] M0b：定位 4.x 常驻模拟撮合载体（阻塞 M2 设计，不阻塞 M1）
- [ ] M0b：rqalpha 用我们 SQLite 数据的适配器原型（M1 的第一步）
