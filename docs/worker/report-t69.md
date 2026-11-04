# report-t69.md — Day-1 P0 修复审查（Hermes）

## 范围
commit d1a487d：① rq 分支入队即 `mark_order`；② `record_close_nav` 让位检查加入 `nav_history` 当日行。

## 审查
1. ① 正确：异步入队 ≠ 成交，必须入队即 mark，否则每分钟重复入队。与 legacy 语义对齐。
   - 风险：rq 分支**未 bump_budget**，`max_orders_per_day` 在异步路径下可能不生效；建议补或确认由 rq 消费侧记账。
   - 未有测试锁定；建议加“同一规则同日只入队一次”断言。
2. ② 正确：RunDaily 直写 nav_history 且无 op_log，前两条让位检查看不见；补第三条。
   - `_nav_history_has_today` 查不到按“无”处理（宁可多记），符合 fail-open 但 money-class 略松；建议至少 warn。
   - LIKE 前缀匹配 timestamp，格式需 YYYY-MM-DD；当前 timestamp 写法一致，OK。

## 结论
修复方向正确；建议补 2 条测试并跟进 budget 记账。
