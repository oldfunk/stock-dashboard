# report-t70.md — Day-1 P0 跟进审查（Hermes）

## 复核
commit 979fece：rq 分支补 `bump_budget` + `tests/test_m3_day1_p0s.py` 7 例。

- 全量测试：**745 passed / 9 skipped**（复跑一致）。
- wiring audit：断层无，exit 0。
- bump_budget 金额=决策价×数量，与 max_turnover 口径一致；mark_order 入队即标，防重灌。
- T60 可证伪判据已验证成立；T62 分支未合并，仍待拍板。

## 结论
跟进修复正确，测试锁定；仅剩 3 件拍板项（questions.md 2026-11-04）。
