# report-t23.md — coverage 严格门禁（Hermes）

## 实现
- `paper_trading/cli.py` 新增 `data coverage --strict [--whitelist FILE]`。
- 白名单：`docs/coverage-downgrade-whitelist.txt`（每行一个 coverage 顶层 key，`#` 注释）。
- strict 模式：任一 coverage 顶层 key 的 source 含 `unavailable` 或以 `local:` 开头，
  且不在白名单 → 退出码 1 + violations 列表。
- 对照脚本：`spikes/coverage_diff.py before.json after.json`。

## 40 键归属
母项目迁移进行中，40 键降级清单需在母库恢复后由 `data coverage` 两次输出 diff 生成；
当前白名单为空，strict 模式将对任何降级失败（符合用户红线）。
白名单填充时每行注明归属母库表与迁移状态。

## 验证
- `python -m paper_trading.cli data coverage --strict` 在无母库环境应失败（降级白名单外）。
- 非 strict 模式行为不变。
