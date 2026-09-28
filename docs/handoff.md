# Stock Dashboard — 班次交接 (Handoff)

> 账本 `iteration-log.md` 是真相源，本文件是下一班开工的 10 行速览。
> 每班收尾必须重写本文件（"最后状态"+"下一步方向"+"已知隐患"三段重写，"历史交接区"只追加不删），写完再 push + 简报。后一班只读它 + 账本当日条目就能接上。

## 最后状态（2026-09-28 纸盘 subtree 机制化完成，gate 473，随即合并）
- **机制落地**：`paper-upstream` remote 已配；plain copy 转 subtree（删旧+graft 重建，内容一致）；首 `pull --squash` 带回上游 3 提交（单测禁网/上下文覆盖/NAV全口径），零冲突；以后更新一条命令
- **双向独立验证**（grep 实证）：母仓 src/tests/scripts 零引用 `paper_trading.*`；vendored 零引用 `src/stock_dashboard`——父开发不被分心，子独立演进
- **规则**：vendored 文件绝不手改（修先上游再 pull）；母推远端天然带上子树（单仓单推）
- **验证（生产服务器 worktree）**：`gate.sh` **473 passed / Gate passed**（470+3 上游新单测）；三扫描全绿（vendored 豁免延续）
- **文档全量**：本批次 README/AGENTS/architecture/roadmap/账本/paper-trading.md/agent-api 一次对齐
- 合并后同步生产：git pull + gate + 远端含树实证（`git ls-tree`）+ HTTP（纯代码无变更不重启——subtree 嫁接只动 `paper_trading/`，服务不读它）

## 下一步方向
1. 收盘后（15:00+）跑一次真 agent run（母 universe + 我们的 Key），看 AI 下不下单
2. 自动调度是否配——等用户明确批准（交易自动化）
3. K 线双 pipeline v2 统一；双份 JS 收敛；队列持久化——按需再说
4. P2 仍冻结；v2（Anthropic/Gemini 原生、金额折算）以后再说

## 已知隐患
- vendored 运行 CWD 必须是 `paper_trading/`（import 与相对路径都依赖它）
- `:8080`（子项目独立部署）vs `:8081`（合并树面板）——互不干扰，别混淆
- 问答/解盘不落库；S8 47%、S4/S5 挂、82 只无 roic/fcf；快照滚动记录；重启验证 ≥10s——沿用既有结论

## 历史交接区（追加，不删）
- 2026-09-28 纸盘 subtree 机制化已合（plain→subtree graft + 上游 3 提交 + 双向独立验证 + 文档全量；生产 gate 473；以后更新一条 pull）
- 2026-09-28 纸盘全量合并已合（整体迁入顶层 + 原生包删除 + /paper 改直达 :8081；生产 gate 469→470；pool screening/Key/MA/agent dry/llm ask 全通；:8081 面板已起；真 agent 决策待收盘）
- 2026-09-28 双 bug 修复已合（nightly/20260928c → main：KlineDAO 恢复 + 钉选卡跳转 + 静态版本戳；生产 gate 440；K 线表停更 7 天，明早流水线自动追平）
- 2026-09-28 AI 队列并发+UI 重组已合（nightly/20260928a → main：生产 gate 437；排队/N 并发/主页常驻/润色/大盘解盘；队列内存态重启丢任务已知）
- 2026-09-27 首只真分析跑通 + 0927e 已合（DAO 排除复盘轮；盐湖股份 once 1/1，用量 3337/5103；用户自改对模型名 glm-4.5-flash；生产 gate 408）
- 2026-09-27 用户报障修坑+卡片按钮+直接提问已合（nightly/20260927d → main=577b8e0：生产 gate 405；报障真因系 UX 坑非代码错）
- 2026-09-27 /llm 页参数收折叠（temperature/max_tokens/间隔默认+高级设置；docs 随 nightly/20260927c 合并）
- 2026-09-27 LLM 自带 Key 已合已同步（nightly/20260927a → main=45be212：生产 gate 396 + 重启三路 200 + /llm 页 live；用户长期授权直接合并/推送/生产调试，不再逐次请示）
- 2026-09-27 LLM 自带 Key 分析复活待合（nightly/20260927a：OpenAI-compatible + /llm 设置页 + 三触发 + 用量显示，生产 worktree gate 396；提示词/压缩不重建；端到端待用户 Key）
- 2026-09-23 deep_research 废表已删（用户批准"脏数据别留"；5 行备份后 DROP，表已不存在；08-31"表不存在"系误记，今日才真删；docs 随 nightly/20260923b 合并）
- 2026-09-23 维护模式收尾已合（nightly/20260923a → main：AI 零触发审计 + 备份清理约 480M + 文档冻结；停止投入，转维护模式）
- 2026-09-23 P0-2 修复已合已同步（nightly/20260922e → main=c6175d1：生产 gate 367 + 重启三路 200；用户授权由 agent 把关合并）
- 2026-09-22 四项拍板落地已合已同步（nightly/20260922d → main=1f0427c：生产 gate 360 + 重启 200 + sector 回填 2594 行 + 页面 16 行行业均值；同日 0922c/0922d 双合）
- 2026-09-22 四项拍板落地待合（nightly/20260922d：残留④ + 测试规则落档 + S8 新浪行业回填，生产 worktree gate 360 + live 2999 只映射 + 缓存落盘；同日 0922c 已合入 main=2f0384e）
- 2026-09-22 开发者身份清除待合（nightly/20260922c：23 文件占位符化 + deploy.local.md 本地化，本地与生产 worktree gate 均 348，身份扫描 0 命中）
- 2026-09-22 P1② 行业均值收口 + C3 取消已合已同步（远端 nightly/20260922f 已删；生产 pull + gate 348 + http 200；同日晚些 P0-1 重启令新代码全路由生效）
- 2026-09-22 误入内容清除 + 职责定位已合已同步（`e954b29`→`2ff32b1`，gate 342 全绿；AGENTS.md:53 经用户批准于同日补修，设备名全仓归零）
- 2026-09-22 Agent 接入指南已合已同步（`1499691`：agent-api.md + README 挂链，gate 342 全绿）
- 2026-09-22 ABC 方案已合已同步（`bb64119`：反面检验/论文追踪/复盘三问内化 + 架构方法论固化 + 解绑上游镜像与对照工具，gate 342 全绿）
- 2026-09-22 残留清理已合已同步（nightly/20260922b：hermes_proxy 空目录 + scheduler 纸盘注释尸体，生产服务器 328 全绿）
- 2026-09-22 残留清理待合（nightly/20260922b：hermes_proxy 空目录 + scheduler 纸盘注释尸体）
- 2026-09-22 文档复检修正已合已同步（9 处写错：AGENTS 工作流/基线 317/归档表述/代理契约，纯文档未重启）
- 2026-09-21 nightly/20260921b 已合已部署（分析师整改 T1–T4 + 去 kanban，生产 gate 328 全绿）
- 2026-09-21 分析师整改 T1+T2 待合（nightly/20260921b：数据质量标注/估值自选/纸盘 schema 删除/笔记 model 列，生产服务器 323 全绿）
- 2026-09-21 nightly/20260921 已合已部署（去 AI 内联 + ai_proxy 改名 + 文档清包袱，生产服务器 gate 317 全绿）
- 2026-09-21 去 AI 内联 + 去 Hermes 化待合（nightly/20260921：卡片去 AI/停本地触发/ai_proxy 改名/文档清包袱）
- 2026-09-18 文档复检：修复 handoff 残留冲突标记（21615d2 合并遗留）+ AGENTS.md 校订（基线 317/运行命令/交接段）+ README 校正（run-once→run、基线 313→317），分支 nightly/20260918 待合
- 2026-09-17 收尾（AGENTS.md 入 main + README 如实化 + 删 0917/0917b + 0917c 已合）
- 2026-09-17 备用通道 nightly/20260917c（Zen 全灭实证 + Pollinations 兜底 + 生产冒烟通过）
- 2026-09-17 AI 修复+UI 收敛已部署（ling 优先/Retry-After/观察池卡片统一，生产服务器 307 全过；发现 403 需 Zen Key）
- 2026-09-17 nightly/20260917 已合已部署（/paper 面板上线 + B6/B7，生产服务器 297 全过，删废分支0916）
- 2026-09-16 M4a 纸盘引擎：撮合+信号编排+scheduler 触发（37 单测，main 已合，生产服务器已同步）
- 2026-09-15 README 全面重写（项目结构/贡献流程/配置表）+ handoff 状态更新
- 2026-09-15 nightly/20260914 合并到 main（9 commits, +1463/-76, 22 files）
- 2026-09-15 账本对齐 P0#1-3/P1#5-6 标完成（纯文档，402cd5e/293008d/6256d58/44959c 注记 + Changelog）
- 2026-09-14 M2 多策略后端 + AI 摘要前置合并（nightly/20260914 + nightly/20260913 → main）
- 2026-09-14 M2 多策略筛选后端核心落地 + verify（strategy_tags + multi 开关 + 5 单测，全仓 229 passed）
- 2026-09-12 架构治理 + README 重排（architecture.md + paper-trading.md + roadmap 升级 + README 重构）
- 2026-09-12 C2.5 东财 datacenter 补 roic/fcf P0（全仓 221 passed，822/904 股有 roic/fcf）
- 2026-09-11 C2 东财第二财务源 P0（全仓 218 passed）
- 2026-09-09 建交接文件，三任务串联启动
- 2026-09-09 白班试跑：豁免D夹具修复落袋（577cc83）
