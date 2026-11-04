# report-t22 — NEC 生产心跳与看门狗（fail-loud）

执行人：员工 AI（Qoder）｜2026-10-28
分支：`worker/t22`（**产品代码，按协议不直接进 main，等主 AI review 合并**）
验收：全量 **336 passed, 1 skipped**（基线 326 + 本次新增 10 条）；变异测试可杀；
CLI `--json` 模式 stdout 纯 JSON；不健康 ⇒ 退出码 **1**。

## 1. 设计：为什么长这样

**一条原则**：告警疲劳会杀死 fail-loud。所以判据不是"心跳新不新鲜"，而是
**"在应该跳的时间窗里跳没跳"**（`heartbeat.due()`）：

| 名字 | 应跳窗口 | 阈值 | 不跳说明什么 |
|---|---|---|---|
| `engine` | 交易日 09:00–15:59（cron `* 9-15 * * 1-5`） | 300s | engine cron 没跑 / 跑了就崩 |
| `agent` | 交易日 20:30 之后（收盘计划窗口 16:30–18:30 + 4h 容错） | 64800s | 收盘计划/决策链当天没跑成 |

⇒ 周末、夜里、节假日**一律不告警**（`state="not-due"`），不会像"18 小时阈值"那种
写法每逢周末必误报。

**心跳写在轮次末尾**（不是开头）：进程中途被 SIGKILL/断电就不会跳 ⇒
看门狗能区分"没跑成"和"跑成但没动作"（休市跳过算跑成）。

**只在状态翻转时落流水**（`transitioned()`：健康→告警=degraded、告警→健康=recovered），
持续故障期间每分钟刷 `op_log` 会把审计链冲垮（和 `engine_triggers` "同 key 每天一条"同一精神）。

## 2. 改动清单

| 文件 | 改动 |
|---|---|
| `paper_trading/utils/heartbeat.py` | 新增：`touch/load/age/due/probe/dashboard_active/check/write_status/read_status/transitioned`；原子写（tmp+rename）、坏文件容错、**永不抛错** |
| `paper_trading/engine/runner.py` | `run_loop` 每轮末尾 `touch("engine", ok=…, detail=两个模块的错误)`（两模块都错才算不健康） |
| `paper_trading/agent/loop.py` | `run()` 决策完成后 `touch("agent", …)`（含 mode/decisions/dry-run） |
| `paper_trading/cli.py` | 新子命令 `health check` / `health show`（各自带 `--json`） |
| `paper_trading/dashboard.py` | 新端点 `GET /api/health`（只读）+ 顶部红条；恢复后 `healthy=true` ⇒ 红条自动消失 |
| `tests/test_heartbeat.py` | 10 条测试（含时间注入，不依赖真实时钟） |

状态文件：`var/health_status.json`；心跳：`var/heartbeat.json`（都在 `var/`，已 gitignore）。

## 3. 看门狗 cron（**我没有装**，等主 AI 决定）

NEC 已是生产机，我不擅自改它的 crontab。**建议由主 AI 执行**，并附两条我实测到的坑：

```cron
*/10 * * * * cd /home/nec/paper-trading && XDG_RUNTIME_DIR=/run/user/1000 \
  venv/bin/python -m paper_trading.cli health check >> health.log 2>&1
```

1. **`XDG_RUNTIME_DIR` 必须显式给**：cron 环境里 `systemctl --user` 看不到用户会话，
   会一律返回 `unknown`（我在 Windows 上实测就是这个分支）。代码已把 `unknown`
   当成"告警但注明原因"，不假装健康。
2. 退出码 1 表示不健康 ⇒ `health.log` 里按行 grep 即可；如果主 AI 想让"红条 + 流水"
   是唯一告警面，那 cron 行末尾加 `|| true` 更干净（由你定，我不替你决定生产 crontab）。
3. 恢复不需要人工动作：下一轮心跳一写，`check` 就翻回 `healthy=true` 并记一条 recovered。

## 4. 注入停滞的手工验证（部署后照做即可，5 分钟）

```bash
cd /home/nec/paper-trading
# ① 假装 engine 停了：把心跳时间戳改回 20 分钟前
python - <<'PY'
import json,pathlib,datetime as dt
p=pathlib.Path("var/heartbeat.json"); d=json.loads(p.read_text())
d["engine"]["ts"]=(dt.datetime.now()-dt.timedelta(minutes=20)).isoformat(timespec="seconds")
p.write_text(json.dumps(d,ensure_ascii=False))
PY
venv/bin/python -m paper_trading.cli health check; echo "exit=$? (期望 1)"
curl -s 127.0.0.1:8080/api/health | head -c 200      # 期望 healthy:false + alerts 非空
# ② 面板顶部应出现红条「🔴 生产健康告警：engine 应跳未跳…」
# ③ 恢复：手动跑一轮 engine（或等 cron），再 check
venv/bin/python -m paper_trading.engine --once --json >/dev/null 2>&1 || true
venv/bin/python -m paper_trading.cli health check; echo "exit=$? (期望 0)"
# ④ 流水里应恰好两条：一 degraded 一 recovered（中间不刷屏）
venv/bin/python -m paper_trading.cli history --type ops --limit 20 --json | grep -c data:health
```

Windows 侧我已实测过的等价结果：
```
health check --json → exit=1
  healthy = False
  alerts = ['engine 应跳未跳（最后心跳 无记录，阈值 300s）',
            '面板服务状态无法判定（缺 systemctl 或 user 会话不可见）']
  checks = {'engine': 'stale', 'agent': 'not-due'}     ← 夜里 agent 不误报
```

## 5. 测试与质量

- 10 条新测试：新鲜心跳健康 / 时段内陈旧=不健康 / **缺记录=fail-closed 不健康** /
  非应跳时段不告警 / 坏 JSON 降级 / 写不进去不抛错 / systemd 三态（active/inactive/unknown）/
  故障→恢复红条自动消 / **持续故障不刷屏** / 面板宕机单独告警 /
  **两个模块都崩 ⇒ 心跳照写但 ok=False**。
- 变异测试（临时改坏实现，看测试是否变红，之后恢复）：
  ① `due()` 取消时段保护 ⇒ `test_not_due_window_never_alerts` 红；
  ② `transitioned()` 恒返回 None ⇒ `test_check_writes_status_then_recovery_clears` 红。
  ⇒ 这两道保护不是装饰，测试真在守着。
- 全量：`336 passed, 1 skipped`（本地 3.14 与 NEC 3.12 基线一致，本次未在 NEC 跑改动）。

## 6. 已知边界与后续（诚实清单）

1. **`health check` 是 pull 模型**：必须有人跑它（cron）。若 cron 本身没被装/被删，
   看门狗自己就哑了 —— 这仍是"静默故障"。真正的兜底是**外部探测**（如家里另一台机器
   curl `/api/health`）。要不要加一条"面板进程自己在渲染时顺带判一次"
   （`/api/health` 无状态文件时已回退到实时 `probe()`，但**没人跑 check 就没有红条**，
   因为红条数据源是状态文件）。我倾向：`/api/health` 在无状态文件时也把 `probe()`
   结果直接渲染成红条 —— 一句话改动，等主 AI 定，我不擅自扩面。
2. **dashboard 宕机时红条自然也没了**（面板本身就是那个服务）：所以 engine 停滞告警
   依赖"面板活着"。要闭环需要第二个通知面（`op_log` 已有 + `health.log` 退出码）。
   如果主 AI 想更硬，可让 `health check` 在被 cron 调时把不健康状态**也写进心跳文件的
   consecutive_fail 计数**，面板起来后先显示"曾经连续 N 次不健康"。这条我没做，避免过度设计。
3. **agent 阈值语义**：20:30 后追问"今天没跑成"。若某交易日主动不跑 AI（用户停 cron），
   会每天告警一次 —— 可接受（那本身就是需要知道的事）。
4. **SIGKILL 中途**：心跳不写 ⇒ 5 分钟后告警，符合预期；但注意与 H-5/T24 的
   "计划 consume-once"交叉：崩溃的轮次可能已消费计划而没成交，这正是 T24（Hermes）
   在查的窗口，不在本任务范围。
5. 我没有动 `config.yaml`（红线：员工不改默认值）。若要可调阈值，需主 AI 加
   `health:` 段并同步 `wiring_audit`；现在是代码默认 + CLI `--engine-max-age/--agent-max-age` 覆盖。
