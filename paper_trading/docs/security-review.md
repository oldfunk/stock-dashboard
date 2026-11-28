# 安全复核（B3，2026-11-28）

> 目标：面板、Key、口令的**可复现**检查与攻防演练。本文只写步骤与判据，
> 不记录真实 Key/口令。自动化部分跑 `python -m paper_trading.cli security audit`。

## 1. 自动化检查（只读）

```bash
cd paper-trading
python -m paper_trading.cli security audit --json
```

判据：

| id | 通过条件 | 失败处理 |
|---|---|---|
| `secrets-gitignored` | `secrets.local.json` 被 `.gitignore` 命中 | 加入 `.gitignore`，不要提交 |
| `secrets-untracked` | `git ls-files` 不含 secrets | `git rm --cached secrets.local.json` |
| `secrets-mode` | POSIX `mode & 077 == 0`（0600） | `chmod 600 secrets.local.json` |
| `admin-token` | 长度 ≥32 | 删除旧 secrets 后重新 `llm config` 生成 |
| `config-no-secrets` | `config.yaml` 无 `api_key/password/token` 值 | 移到 secrets 文件 |
| `dashboard-bind-default` | 仅提示 | 本机用 `--host 127.0.0.1`；公网必须加反代鉴权 |

Windows 无 POSIX mode，检查标 `warn`；用 ACL 收紧：

```powershell
icacls secrets.local.json /inheritance:r /grant:r "$env:USERNAME:F"
```

## 2. 无口令写入演练

前置：面板运行在测试实例（副本账本），不要用生产。

```bash
# 1) 无口令保存配置 → 应 401/403，且 secrets 不变
curl -s -X POST http://127.0.0.1:18080/api/llm/config \
  -H 'Content-Type: application/json' \
  -d '{"preset":"custom","base_url":"http://evil.example/v1","model":"x"}'

# 2) 无口令切换方案 / 下单 → 应拒绝
curl -s -X POST http://127.0.0.1:18080/api/scheme/use \
  -H 'Content-Type: application/json' -d '{"name":"custom"}'

# 3) 检查账本/ secrets 未被修改
python -m paper_trading.cli ledger inspect --json
```

判据：写接口全部拒绝；`op_log` 无新增成功写；`secrets.local.json` 哈希不变。

## 3. CSRF / 跨站演练

面板对写请求做同源检查（`Origin.host == Host`）。演练：

```bash
curl -s -X POST http://127.0.0.1:18080/api/llm/config \
  -H 'Origin: http://evil.example' \
  -H 'Content-Type: application/json' \
  -d '{"admin_token":"wrong","preset":"custom"}'
```

判据：跨源请求被拒；即使带错误 token 也不得更进一步。
浏览器侧再从 `http://evil.example` 页面发 `fetch`（no-cors 也应无效），
确认响应不可读、状态未变。

## 4. 跨实例接管演练

场景：同一台机器跑两个面板（不同端口/账本），第二个实例没有 provider Key。

1. 实例 A 已配置 Key 与 `admin_token`；
2. 实例 B 打开设置页，尝试“接管/修改 Key”；
3. 判据：B 必须要求 A 的 `admin_token`，不能用本地文件里另一个 token
   或空口令接管；失败信息明确，不泄露 Key。

## 5. Key 泄露面复核

- `op_log` / `ai:*` 结果里不得出现 Key 原文（只允许掩码）；
- 面板“设置”页只显示 `key_masked`，不回显 token；
- 日志（`G:\opencode\paper-trading\logs` / systemd journal）grep：
  `rg -n 'sk-|api_key|admin_token' logs/` → 只允许字段名/掩码；
- 命令行传 Key 会留在 shell history：只用面板保存，或 `--api-key` 后清理历史；
- 第二实例 / 母项目不得读取子项目 secrets（母库只读边界）。

## 6. 面板暴露面

- 默认 `--host 0.0.0.0` 是**局域网便利**，不是公网建议；
- 公网部署必须：反代 TLS + 鉴权 + 限制来源 IP + 关闭不必要的写接口；
- 面板无用户体系，`admin_token` 是唯一写口令；不要发到聊天/截图；
- 只读接口也可能泄露持仓/策略，按内部系统对待。

## 7. 复核记录模板

```
日期：
版本/commit：
security audit：fail=? warn=?
无口令写入：通过/失败（证据：HTTP 码 + op_log）
CSRF：通过/失败
跨实例接管：通过/失败
Key 泄露 grep：通过/失败
结论：可继续 / 需修复（issue 链接）
```
