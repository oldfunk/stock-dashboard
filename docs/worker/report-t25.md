# report-t25 — 部署文档同步 NEC（AGENTS.md）

执行人：员工 AI（Qoder）｜分支 `worker/t25`｜性质：纯文档，无代码改动

## 改了什么

| 位置 | 改动 |
|---|---|
| `AGENTS.md` Cron Integration（5 条 cron 示例） | `cd /home/pi/paper-trading` → `cd /home/nec/paper-trading`；节标题注明"生产机 NEC；路径已同步" |
| `AGENTS.md` L76 日志轮转安装 | "Pi 日志轮转…" → "NEC 日志轮转（生产机；Pi 冻结期不适用）…"（这是**操作指引**，不改会让人往错机器装） |
| `AGENTS.md` 末节 `## Pi Deployment Sync` | 改名 `## Deployment Sync（生产机 = NEC，2026-10-28 起）`；写明生产机是 NEC、**Pi 已冻结为回退副本（观察期 2-3 交易日，结束前勿回收数据）**；指向唯一真相源 `docs/migration-nec.md`，**只给指引不复制内容**（防两处漂移）；保留 `git pull` only / 不 scp 的既有铁律 |
| 同节新增"已知环境坑"3 条 | ① NEC 是 **x86-64-v1（Core2 Duo）** ⇒ numpy ≥2.4 wheel 装得上但 `import` 失败，`requirements.txt` 只有下限 ⇒ 新机器默认解析即坏（需 `numpy<2.4`）；② cron 里 `systemctl --user` 必须给 `XDG_RUNTIME_DIR=/run/user/1000`，否则探测恒 `unknown`；③ ufw 需为 :8080 放行局域网来源，否则"面板挂了"与"防火墙拦了"症状不可区分 |

## 刻意没改（留给主 AI 判断）

- `AGENTS.md` L158「Pi 已于 2026-10-23 验收后在本机 `engine.local.yaml` 开启」、
  L168「10-23 核对 Pi 旧规则集时发现并合并」——**历史事实陈述**，改了等于篡改记录。
  若要保留可读性，建议在 `migration-nec.md` 里注明"历史文档中的 Pi 指当时的生产机"。
- `config.yaml` 的 `engine.enabled` 等默认值：红线（员工不动默认值）。
- `README.md` 里若还有 Pi 部署提法，本次未在范围内（T25 只指 AGENTS）；
  要我一并扫一遍请下条任务。

## 自检

- `grep -c "cd /home/pi/paper-trading" AGENTS.md` → **0**（5 条全部替换）
- `python -m pytest tests/test_caliber_consistency.py tests/test_config_wiring.py -q` → **18 passed**
  （本仓有"文档/配置口径一致性"测试，确认没踩到它们）
- 新增内容全部可核验：三条坑分别对应 `report-t20.md` §1（numpy 基线）、
  `report-t22.md` §3-1（XDG_RUNTIME_DIR）、主 AI 的 `2d47289`（ufw :8080）。
