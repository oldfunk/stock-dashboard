# AGENTS.md

AI 驱动的 A 股价值投资选股看板。每日 15:30 自动跑完全市场约 5500 只股票，经 7 条门规筛选 + LLM 结构化分析，输出候选池 ≤20 只。生产实例跑在 pi1（192.168.50.210:9527）。

## 运行

```bash
python -m src.main serve    # Web + 内置调度器（默认）
python -m src.main run      # 执行一次完整流水线
```

测试门禁：`bash scripts/gate.sh`（全仓 pytest，基线 286 passed，只升不降）。

## 技术栈

Python 3.10+ · FastAPI + Jinja2 · SQLite (WAL) · AKShare + 腾讯行情 · OpenAI 兼容 LLM API（OpenCode Zen 免费模型池）· 内置 scheduler daemon 线程。

## 架构

`src/` 分层：`collector/`（数据采集）→ `screener/`（7 门规筛选 + 五维打分）→ `analyzer/`（LLM 结构化分析）→ `web/`（FastAPI 展示）。模块边界禁令见 `docs/architecture.md` §3。数据源注册表 S1–S7 及三条铁律见 `docs/architecture.md` §5。

## 硬规则

- 零 emoji（仅允许 → ↑ ↓ ✓ 排版符号）
- 每次迭代只做一件小而实的事，禁止改多个无关模块
- `collector/` `screener/` `analyzer/` 改动必须附单测
- 全仓 pytest 零失败（基线 286 passed）
- 禁删 S1–S7 适配函数（除非替代 + 单测同 commit）
- 提交信息中文，写清「改了什么 + 为什么」
- 改完更新 `docs/iteration-log.md`（勾掉 backlog + Changelog 追加）

## Git 工作流

1. `git fetch origin && git rebase origin/main` 拉平上游
2. 在 `nightly/YYYYMMDD` 分支上 commit（Hermes 作者）
3. `git push origin HEAD`（只推 nightly，不碰 main）
4. 多日累积在同一 nightly 分支，审计时 `git log origin/main..HEAD --stat`

**绝不**：在本地 main 上 commit、`git push origin main`、重启生产服务 `stock-dashboard.service`。pi1 部署由用户手动 pull + restart。

## 冒烟测试

改完代码后 `ssh pi@192.168.50.210 "cd /home/pi/stock-dashboard && .venv/bin/python -c 'import 改动的模块'"` 确认无 import 错误。不依赖 pi2 venv（pi2 仅开发副本，不运行服务）。

## 文档

单一事实源：`docs/architecture.md`（架构真相源）、`docs/iteration-log.md`（迭代进程账）、`docs/roadmap.md`（总路线）。改系统前先读这些文件。
