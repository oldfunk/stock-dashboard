# report-t66.md — T63/T64 交付审查（Hermes）

## 复核方法
在 worktree（origin/worker/t63-t64-perf-wiring，3d05747）上重跑全量测试与 wiring audit，对照报告声明。

## 验证结果
- `python -m pytest -q tests/`：**701 passed / 9 skipped**（与报告一致）。
- `tests/wiring_audit.py --quiet`：断层无，退出码 0（与报告一致）。
- `tests/test_wiring_t64.py` + `tests/test_context.py`：27 passed。

## 审查结论
1. **T63 诚实**：上下文 3.5× 小、请求体 4.1× 小，但 ≤20s 未达成；报告明确归因 max_tokens 上限，并把 1536 作为不截断答案的折中，符合 AGENTS「不编造」。
2. **T64 v2 有牙齿**：合成断层测试通过（5 例），门禁 exit 0/1 可用；F1/F2 修法收敛为 chat_with_retry 单原语，避免各写一份。
3. **分支未合并**：main 仍 523bfc9，生产树未含 T63/T64；合并前需确认 questions.md 待拍板 3 项（ASK_MAX_TOKENS、CI 接入、engine 行为级断言）。
4. **残留风险**：engine/strategy 面仅静态读点检查；长答案截断信号未实现。建议 T66b 跟进。

## 建议
合并前先拍板 3 项；合并后跑 Monday `m3 verify` 全绿。
