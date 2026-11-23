# 开发期加速规程（2026-11-12 起，功能成熟前适用）

> 创建：2026-11-12 · 上游：用户指令（"项目始终不能稳定运行…不能拖下去"）· 状态：生效中

## 为什么改

项目跑了近 6 个月，系统始终不稳定。过去每批都走"生产级纪律"（10 分钟生产门禁 +
15 分钟部署 + 窗口纪律 + 跨团队协调），在功能未成熟阶段，这些纪律**自己拖住自己**。
用户指令：**开发期加速验证，制造环境验证来推进进度**。

## 三条新规

1. **门禁**：**本机不做全仓验证**（AGENTS 纪律：本机只编辑/commit/文本扫描）；
   需要快速反馈时，可在生产机 `bash scripts/gate.sh` 或在 nec 的隔离 worktree 跑**单个测试文件**，
   仅作参考；**交付验证一律以生产 gate 为准**（母项目权威 + vendor 记录）。
   - `scripts/gate.sh` 分层：母项目用例必须全绿、vendor 记录不阻塞；基线只升不降。
   - 门禁环境建议装 `pytest-timeout`（`pip install pytest-timeout`）：脚本检测到它会自动加
     `--timeout=180`（`GATE_TEST_TIMEOUT` 可调），挂起由**单条用例**承担并报出用例名；
     未装则退回整批 600s 兜底，脚本头部会提示。
2. **制造环境**：研究线/筛选器用**合成库**验证，不等真实采集。
   - `python scripts/make_synthetic_env.py` —— 真库，bake 了已知 MOS->超额关系
     （`excess_60 = 5.0 + 0.30*mos + N(0,6)`）
   - `python scripts/make_synthetic_env.py --slope-scale 0 --out data/db/...null.db`
     —— 零关系对照库（验研究 OS 不产生假阳性）
   - `STOCK_DASHBOARD_DB=<库> python scripts/verify_synthetic_env.py` —— 端到端跑研究 OS
   - 实测：真库净增量 18.39pp（理论 18pp）CI 不覆盖 0；零关系库 0.40pp CI 覆盖 0
3. **发布纪律保留**：发布时才走 `scripts/deploy.sh`（生产门禁 + 同步 + 健康检查）
   与部署窗口纪律。

## 发布清单（每周或功能完成时）

- [ ] 生产 gate 全绿（母项目权威 + vendor 记录，基线只升不降）
- [ ] `bash scripts/deploy.sh`（生产门禁 → 合 main → 同步 → 健康检查）
- [ ] 更新 handoff / iteration-log

## 已知取舍

- 生产可能滞后几天——这是有意的：功能未成熟前，生产不是真相源，**代码 + 合成环境**才是。
- 生产数据继续收集（不废弃），用于最终的真实世界校验。
