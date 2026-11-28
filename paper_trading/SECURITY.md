# 安全政策（Security Policy）

## 报告漏洞

发现安全问题请直接提 GitHub Issue（标题前缀 `[security]`），
请勿在公开 Issue 中粘贴真实 Key、Token 或账户数据（用 `sk-***` 掩码代替）。
暂不设赏金，确认有效的问题会优先修复并致谢。

## 已知安全边界（设计如此，不是漏洞）

- 面板无登录鉴权：仅限局域网使用，禁止暴露到公网。
- LLM Key 存本机 `secrets.local.json`（0600），面板/日志永不输出原文。
- 面板写操作凭 `admin_token`（本机生成）；换浏览器凭 API Key 接管。
- `GET /api/llm/models`（拉模型列表）**不设口令**：它只回传模型 id，Key 不出本机、不回显；失败原因（未配置/401/超时）会如实回给调用方。
- 本项目为模拟盘：不连接券商、不触碰真实资金。

## 仓库治理（D6，2026-11-28）

- `main` 禁强推/禁删（admins）；`v*` tag ruleset 禁删/禁移/禁强推；
- `.github/CODEOWNERS` 默认由仓库所有者复核，账本/风控/发布路径显式列出；
- 建议对发布 tag 使用 **signed commit/tag**（本地配置
  `git config commit.gpgsign true` + GPG/SSH key），仓库不强制以免阻断无签名环境；
- 外部身份（如历史 Qoder 提交）按 `.mailmap` + GitHub 账号 block 处理，
  不重写历史；详见 `docs/worker/report-qoder-provenance-20261124.md`。

