# AGENTS.md

AI 驱动的 A 股价值投资选股看板。工作日 15:30 后自动跑完全市场约 5500 只股票，经 7 条门规筛选输出候选池 ≤20 只（AI 分析由外部 AI 执行，面板不触发）。生产实例部署于生产服务器（端口 9527，部署目标见本地 `deploy.local.md`，该文件不入库）。

## 运行

```bash
python -m src.main serve    # Web + 内置调度器（默认）
python -m src.main run      # 执行一次采集 + 筛选（不含 AI 分析）
```

测试门禁：`bash scripts/gate.sh`（**在生产服务器执行**——开发在本机、测试在生产，本机不跑 pytest；全仓零失败；基线 348 passed，只升不降）。

## 技术栈

Python 3.10+ · FastAPI + Jinja2 · SQLite (WAL) · AKShare + 腾讯行情 · AI 分析由外部 AI 执行（通用协议见 `docs/ai-proxy-ai-analysis.md`；本地 LLM 通道不可用）· 内置 scheduler daemon 线程。

## 架构

`src/` 分层：`collector/`（数据采集）→ `screener/`（7 门规筛选 + 五维打分）→ `analyzer/`（本地分析代码，pipeline 已停用触发，仅手动脚本可用）→ `web/`（FastAPI 展示）。模块边界禁令见 `docs/architecture.md` §3。数据源注册表 S1–S8 及三条铁律见 `docs/architecture.md` §5。

## 硬规则

- 零 emoji（仅允许 → ↑ ↓ ✓ 排版符号）
- 每次迭代只做一件小而实的事，禁止改多个无关模块
- `collector/` `screener/` `analyzer/` 改动必须附单测
- 全仓 pytest 零失败（基线只升不降）
- 开发与测试分离：本机只做编辑、commit、文本扫描，不跑 pytest；全仓验证一律在生产服务器 `gate.sh` 执行
- 禁删 S1–S8 适配函数（除非替代 + 单测同 commit）
- 提交信息中文，写清「改了什么 + 为什么」
- 验证声明必须精确：手动分步验证 ≠ live 验证通过。任何"验证通过"必须附带实际执行的命令和输出作为证据，不能夸大验证范围。主动验证所有风险点，而非选择性验证。
- 任务颗粒度必须细化到"单一可验证步骤"：复合任务必须拆分为独立子任务，每个子任务有明确的完成标准和验证命令。
- 所有任务完成后必须自动执行收尾流程（无需用户提醒）：1) 推送 nightly 分支（合并须用户批准，绝不自动合并）；2) 同步生产服务器（ssh git pull + gate.sh 验证）；3) 更新 handoff.md；4) 更新迭代日志。这个流程是强制性的，不是可选的。
- 环境差异必须验证：PC 上测试通过 ≠ 生产服务器上能跑。涉及生产服务器的改动必须在生产服务器上实际执行验证，不能只在 PC 上跑测试就声称完成。
- 文档更新是交付物的一部分：交接文档和迭代日志必须与代码同步更新，不能作为可选步骤。

## 开工与交接

开工前必须先读 `docs/handoff.md` + `docs/iteration-log.md` + `docs/architecture.md`。

收工后：重写 `docs/handoff.md` 三段（最后状态 + 下一步方向 + 已知隐患；历史交接区只追加不删），`docs/iteration-log.md` 勾掉 backlog 并追加当日条目（日期标题 + 改了什么 / 为什么 / 验证）。

## Git 工作流

1. `git fetch origin && git rebase origin/main` 拉平上游
2. 在 `nightly/YYYYMMDD` 分支上 commit
3. `git push origin HEAD`（只推 nightly 分支）
4. 多日累积在同一 nightly 分支，审计时 `git log origin/main..HEAD --stat`

**绝不**：在本地 main 上 commit、`git push origin main`、自动合并到 main。默认推送到 `nightly/*` 分支；合并须用户批准，合并后同步生产服务器并验证运行（`git pull` + 重启 `stock-dashboard.service` + 确认运行正常）。

## 冒烟测试

改完代码后在生产服务器冒烟（本地环境不全，验证以生产服务器为准；改动先同步到生产服务器；目标值见 `deploy.local.md`）：`ssh <生产服务器> "cd <部署目录> && .venv/bin/python -c 'import 改动的模块'"` 确认无 import 错误。

## 文档

单一事实源：docs/architecture.md（架构真相源）、docs/iteration-log.md（迭代进程账）、docs/roadmap.md（总路线）。

### 文档约定

1. 文档放 `docs/`，文件名英文小写连字符（如 ai-proxy-ai-analysis.md）
2. 格式跟随现有文档（参考 architecture.md），勿自创版式：`> 创建：日期 · 上游：来源 · 状态：…` 头部元信息行 + `## N.` 编号章节；表格、代码块按需
3. 新文档先在 `nightly/*` 分支起草，用户批准后合入 main
4. 不重复维护：同一信息只写一处，其他文件引用链接
