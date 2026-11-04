# 生产迁移 runbook：Pi → NEC（2026-10-28）

> 背景：Pi 过劳实锤（load 4.4 持续满载 / free 59MB / 用户观察连续自重启）。
> 迁移目标：`192.168.50.60`（pi） → `192.168.50.71`（nec，Linux Mint x86_64 /
> 2 核 / 5.9G RAM / Py 3.12.3 / 370G 空闲）。
> **最高风险：双活实例重复下单**——同一 AI 决策在两台机器各执行一遍。
> H-5 的 consume-once 防的是单机崩溃重跑，防不了双活。

## 规格对比（迁移收益）

| | Pi 4 | NEC |
|---|---|---|
| 负载 | 4 核 load 4.4（满载） | 2 核 load 0.01 |
| 内存 | 954MB，free 59MB | 5.9GB，avail 4.6GB |
| 磁盘 | SD 59G（写磨损=重启元凶候选） | 468G（bundle 3.3GB 无压力） |
| 架构 | aarch64 | x86_64（whl 通用） |

## 已拍板（用户，2026-10-28）

- **切换时机：今晚 19:00 后**执行 Phase 1（Pi cron 静默期，18:55 后无活动）
- **范围：本次只迁 paper-trading**；stock-dashboard 留 Pi，观察期后另行评估

## 迁移对象清单（逐条核销，T21 对照 Pi 现状）

| # | 对象 | Pi 现状 | NEC 方式 |
|---|---|---|---|
| 1 | 代码 | `~/paper-trading` @ v0.7.10+ | git clone（同 origin） |
| 2 | venv | Py3.13.5 | 重建（**禁止拷贝 venv**，跨架构二进制不兼容） |
| 3 | 账本 | `paper_account.db` 452K | scp（停 cron 后拷，见 P1） |
| 4 | 行情库 | `data.db` 6.4M | scp（同上） |
| 5 | cron | paper-trading 5 条（见下） | **P1 尾部由主 AI 启用** |
| 6 | dashboard | systemd `paper-trading-dashboard` | 重建 unit（同名同端口 8080） |
| 7 | stock-dashboard | Pi 上另一项目（cron 年报回填） | **不迁**（用户未指定，留 Pi） |

### Pi cron 清单（逐条搬，漏一条=漏一个功能）

```
# paper-trading（迁 NEC）
* 9-15 * * 1-5        engine --once          # 引擎每分钟
30,35,40,45,50,55 9   agent tick             # 早盘计划
*/5 16-18             agent tick             # 盘后计划窗口
1-59 9-11,13-14       agent event            # 事件回顾每分钟
0 10 * * 6            calendar sync          # 周六日历同步
# stock-dashboard（留 Pi，不迁）
30 2 * * *            backfill_financial_extra.py
```

## 三阶段执行

### Phase 0 —— NEC 环境搭建（T20/Qoder，Pi 不动）

1. clone + venv + 依赖 + **326 passed, 1 skipped** 与基线对齐
2. dashboard systemd 起服务，`curl 127.0.0.1:8080/` → 200
3. **不启用任何 paper-trading cron**（红线）
4. 冷备份 Pi 数据库一份（本地留底）：`scp pi:~/paper-trading/*.db ./pi-backup-<date>/`

### Phase 1 —— 切换窗口（主 AI 亲自执行）

**时机红线：19:00 之后或周末**——Pi cron 最后活动是 18:55 的 agent tick；
盘中/活动期切换会漏决策或双写。

```
① Pi 停 paper-trading cron（stock-dashboard 条目保留）：
   crontab -l | grep -v 'paper-trading' → crontab -   # 或手工注释 5 条
② 确认无残留进程：pgrep -f paper_trading 应为空
③ 拷数据：scp pi:~/paper-trading/paper_account.db pi:~/paper-trading/data.db → NEC ~/paper-trading/
④ NEC 验证（启用 cron 前）：
   - sqlite3 paper_account.db "SELECT COUNT(*) FROM orders;"  # 与 Pi 同数
   - venv/bin/python -m paper_trading.cli ... dry-run         # 不写库冒烟
   - curl 127.0.0.1:8080/ → 200
⑤ NEC 启用 5 条 cron（crontab -e 手工贴，路径改 /home/nec/...）
⑥ 观察下一轮 cron 日志无异常 → 切换完成
```

**顺序铁律：① 在 ⑤ 之前。宁可空窗几分钟（漏一轮分钟级 tick，无资金影响），
不可双活一秒（重复下单）。**

### Phase 2 —— 观察期（2-3 交易日）

- NEC 生产运行；Pi 冻结为**只读回退副本**（cron 保持停用，db 不再写入）
- 每日核对：agent_decisions 有新记录、dashboard 正常、engine.log 无错误
- 3 天无异常 → Pi 上 paper-trading 正式退役（保留目录只读，不删）
- **异常回退（T21 P0 修正后的正确顺序，与切换同一条铁律）**：
  ① 停 NEC cron（先断源头）→ ② scp NEC db → Pi → ③ Pi 校验
  COUNT/integrity/sha256 → ④ Pi 恢复 cron → ⑤ Pi dashboard 起服务。
  **先停 NEC 再恢复 Pi**——绝不可先恢复 Pi cron（否则 Pi 在旧账本上先跑一轮
  = 账本分叉 + 双活窗口）。

## 执行记录（2026-10-28 14:40-14:47，主 AI 亲执行；用户指示提前切换）

1. 冷备份：Pi 5 文件（2 db + 3 local 配置）→ 开发机 `G:\opencode\pi-backup-20261028\`
2. Pi 停 paper-trading cron（stock-dashboard 保留）+ 停 dashboard + 等 engine 残留进程退净
3. scp → NEC；**sha256 两库逐字节一致**；integrity ok；14 表账本 + 27,888 根日线完整
4. NEC dashboard systemd 化（unit 照抄 Pi 改路径，enable --now，HTTP 200）
5. NEC 启 5 条 cron（与 Pi 逐条一致，路径改 /home/nec）
6. 验证：14:46/14:47 engine cron 连续正常触发、风控熔断态正确传递（回撤 36% held
   10 条条件单）、Pi 0 进程 0 cron = 零双活
7. **补漏（用户反馈打不开网页，14:5x）**：NEC 的 Linux Mint **ufw 防火墙激活**
   （只放行 Samba），8080 被拦——Pi OS 默认无 ufw 故此前无此问题。
   修复：`ufw allow from 192.168.50.0/24 to any port 8080`（局域网限定）。
   **教训：跨机迁移的 checklist 必须含防火墙/监听地址外部可达性检查**
   （本机 curl 200 ≠ 局域网可达）；已列入下方 checklist。

**观察期进行中**；Pi 回退副本冻结（本机+开发机双份备份）。

## 跨机迁移 checklist 增补（每次换机器必查，2026-10-28 教训）

- [ ] `ss -tlnp` 确认监听 `0.0.0.0:<port>`（不是 127.0.0.1）
- [ ] 防火墙状态（`ufw status` / `firewalld` / `iptables`）+ 放行**限定网段**
- [ ] **从另一台机器**（不是本机）curl 目标端口 → 200 才算通过

## 数据一致性验证（T21 审查后补齐：sha256 + 扩展表清单）

- **文件级**：拷贝前后 `sha256sum` 对比两个 db（本次实测逐字节一致）
- **结构级**：`PRAGMA integrity_check` = ok（两库）
- **内容级**：`orders`/`nav_history`/`agent_decisions`/`op_log`/`risk_state`/
  `t1_freeze`/`positions`/`conditional_orders`/`engine_state` COUNT 与 Pi 一致
- **本地覆盖配置**：`engine.local.yaml`/`strategy.local.yaml`/`secrets.local.json`
  一并 scp（T21 P1-3；`.bak*` 备份文件不迁）
- **凭证提醒**（T21 P1-4）：admin_token 在浏览器 localStorage——NEC 面板
  `http://192.168.50.71:8080/` 首次写操作前需用已存 API Key 重新保存一次
- 观察期末：Pi（冻结值） vs NEC（新增量） 差值 = 观察期新增行数，逐表核对
