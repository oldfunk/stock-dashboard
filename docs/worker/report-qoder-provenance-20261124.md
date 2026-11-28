# Qoder 贡献溯源审计（2026-11-24）

> 触发：用户发现仓库中出现独立 GitHub 账号 `qoder` 的提交，担心“第二个人”改过代码。
> 范围：只读审计（git 历史 + GitHub API + 生产 op_log），未改任何代码。

## 1. 结论

- **代码内容**：5 个提交，全部围绕 T62「账本重置后运行态清理」：
  `ledger_state.py`（445 行）、`cli ledger reset-state`、两处 CLI 修复、报告/文档；
  **没有** CI/依赖/配置改动，**没有**外联、`eval/exec`、`subprocess`。
- **推送权限**：**没有证据** `qoder` 账号有仓库写权限。该分支所有 PushEvent 的
  actor 都是 `oldfunk`；`qoder` 不在 collaborator/invitation 列表，无 PR、无 fork、
  无 hook、无 Actions secret。
- **归属**：`qoder` 是提交的 author/committer 身份（`qoder@users.noreply.github.com`），
  不是推送者。GitHub Contributors 按提交邮箱归因，**不等于**账号有访问权。
- **风险**：代码风险低（钱类守卫 + 26 条测试 + 生产两次只清会话目录）；
  归属/流程风险中等（外部身份进入历史，需留存审计）；未发现后门或数据外泄路径。
- **处置（2026-11-24）**：新增 `.mailmap` 把 `Qoder <qoder@users.noreply.github.com>`
  合并回 `oldfunk`（本地 `git shortlog` 已只剩 oldfunk）；GitHub 账号 `qoder`
  已在账号级 **block**。**GitHub Contributors 图不会因 `.mailmap` 自动移除**
  （GitHub 按 commit author email 归因），要彻底移除只能重写历史 + 重打 tag；
  已发布 tag v0.8.0/v0.9.0/v0.10.0/v0.10.1 与母项目 vendor 引用都会失效，
  **不建议为 5 个无恶意提交做这次重写**。

## 2. 提交与时间线

| 提交 | 时间（+08） | 内容 |
|---|---|---|
| `d635d60` | 2026-11-03 12:44:56 | T62 清单 + `ledger reset-state` + `ledger_state.py` |
| `9e3ae70` | 2026-11-03 12:47:13 | `reset-state` 的 `--stamp` 只给 apply |
| `147b396` | 2026-11-03 12:49:00 | no-op 不许自称 dry-run |
| `9e13a62` | 2026-11-03 12:55:14 | T62 报告 + Batch 13 核销 |
| `ada5279` | 2026-11-03 12:56:06 | T62 报告补 md5 说明 |

- 分支：`worker/t59-t60-caliber`；创建事件 **2026-11-03 03:25Z**，actor **oldfunk**；
- 该分支 PushEvent（03:36Z / 04:18Z / 04:45Z / 04:47Z / 04:55Z / **04:56:11Z**）
  actor 全部是 **oldfunk**；
- 最后一个 qoder 提交时间 **04:56:06Z**，5 秒后的推送 actor 是 **oldfunk**；
- 合并：`3c8431f`（2026-11-04 22:56，opencode），前置裁决 `a0ec482`
  （T62 merge approved）。

## 3. 代码内容与守卫

- 只清运行态：`risk_state` 峰值/日亏基线、`engine_state` 触发/冷却/预算、
  pending 意图（标 rejected）、armed 条件单（标 cancelled）、`var/rq_state/{live,<日>}`
  （改名不删）；
- **钱类只读**：`account/positions/orders/fills/nav_history/t1_freeze/op_log`
  前后快照（行数 + cash + 持仓总股数）必须一致，否则抛错并回滚（会话目录改名回来）；
- 清理前的原值写入 `op_log[ledger:reset-state]`；默认 dry-run，`--yes` + `--reason`
  才写；未知项 id 直接拒；只报不清项红则退出码 1；
- 只暴露在 CLI，**不在面板 HTTP 接口**里；
- 测试：`tests/test_reset_state_t62.py` **26 passed**；全量 1017 passed / 13 skipped；
- 生产使用：`op_log` 有 2 条 `ledger:reset-state`（11-12、11-19），均
  `--only rq_session_state`，`cleared=1`，`money_untouched` 有快照；无钱类改动。

## 4. 权限与账号核查

| 项 | 结果 |
|---|---|
| 当前 collaborators | 仅 `oldfunk`（admin） |
| pending invitations | 空 |
| 仓库 hooks | 空 |
| Actions secrets | 空 |
| Deploy keys | 1 个，`pi-paper-trading-deploy2`，**read_only=true** |
| PR（全量） | 仅 2 个（#6 已合、#4 open），无 qoder PR |
| Forks | 0 |
| Contributors | oldfunk 423、qoder 5 |
| qoder 账号 | 2014 年创建、2 个 2014 年 iOS fork、无 profile、无公开事件 |
| 仓库可见性 | **public**（代码本身不是秘密） |

**关键判据**：`qoder` 的提交能被 GitHub 归因到该账号，只需要 commit email 写成
`qoder@users.noreply.github.com`；这不代表该账号推送或拥有仓库权限。PushEvent
的 actor 才是推送者——本次全部是 `oldfunk`。

## 5. 风险清单

| 风险 | 级别 | 说明 |
|---|---|---|
| 恶意代码/后门 | **低** | 无网络/命令执行/依赖/CI 改动；钱类守卫；代码已审 |
| 外部账号写权限 | **低（无证据）** | 所有推送 actor=oldfunk；无 collaborator/邀请/hook |
| 归属混淆 | **中** | 外部身份进入历史；未来审计需知道“作者≠推送者” |
| 守卫强度 | **低-中** | 守卫是行数/cash/总股数，不是全内容哈希；本代码未改钱类行内容 |
| 本地工具访问面 | **待确认** | 若 Qoder 工具在本机运行，可能读过工作区；本机无 `secrets.local.json`，生产 secrets 不在本机 |

## 6. 建议

1. **保留代码**：功能有用、测试完整、生产使用记录干净；不建议回滚。
2. 可选加固：把 `_money_snapshot` 从“行数/cash/总股数”升级为**钱类全内容哈希**，
   让“改一行价格但行数不变”也能被抓住（需补测试）。
3. 在 GitHub **Security log**（账号设置，非 API）确认 2026-11-01～11-04 是否有
   `repo.add_member/remove_member`；若有，确认邀请与移除都是本人操作。
4. 若 Qoder 工具曾在你本机运行并有工作区/密钥访问，**轮换本地 LLM Key 与面板
   admin_token** 作为预防（本仓库历史无真实 Key：`sk-` 命中均为测试占位符，
   真实 Key 前缀未出现在 git 历史）。
5. 敏感路径（`paper_trading/ledger_state.py`、`cli.py`、`integration/rq_engine/`）
   可加 CODEOWNERS/要求 PR 复核；`main` 已禁强推/禁删，`v*` tag 已有 ruleset。

## 7. 复现命令

```bash
git log --all --author='qoder@users.noreply' --format='%h %ad %an <%ae> %s' --date=iso
gh api --paginate 'repos/oldfunk/paper-trading/events?per_page=100' \
  --jq '.[] | select(.payload.ref=="refs/heads/worker/t59-t60-caliber") | {actor:.actor.login,created_at}'
gh api 'repos/oldfunk/paper-trading/collaborators?affiliation=all' --jq '.[].login'
python -m pytest tests/test_reset_state_t62.py -q
```

## 8. 处置记录（2026-11-24）

| 动作 | 结果 |
|---|---|
| `.mailmap` 合并归因 | `git shortlog -sne --all` 中 qoder 的 5 次已并入 `oldfunk`（86 次），本地 git 工具链不再单列 qoder |
| GitHub 账号 block | `PUT /user/blocks/qoder` 成功；`GET /user/blocks` 可见 qoder |
| GitHub Contributors 图 | **未移除**：GitHub 按 commit author email 归因，`.mailmap` 不改变该图；只有重写历史才能去掉 |
| 历史重写 | **未做**：会改变 11-03 之后的全部提交 hash，v0.8.0/v0.9.0/v0.10.0/v0.10.1 tag 与母项目 vendor 引用失效；为 5 个无恶意提交不值得 |
| 代码 | **保留**（T62 工具已生产使用 2 次，钱类未动） |

如果未来必须让 GitHub Contributors 图也干净，唯一路径是：
维护窗口内停用 main/tag 保护与 ruleset → `git filter-repo --mailmap` 重写全部 refs →
强制推送 → 重新打四个 tag → 母项目重新 vendor → 重新发布。属独立发布级操作，
不在 v0.11.0 观察期做。
