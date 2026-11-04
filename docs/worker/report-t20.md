# report-t20 — NEC 生产环境搭建（迁移 Phase 0）

执行人：员工 AI（Qoder）｜2026-10-28 14:15–14:23
目标机：`ssh nec`｜**遵守红线：未启用任何 cron、未改 Pi 上任何东西、未 enable 开机自启**

## 结果：T20 验收项全绿，但发现一个会让"新机器默认装坏"的硬问题

| 验收项 | 结果 |
|---|---|
| `git clone` 到 `~/paper-trading` | ✅ HEAD **`25bbbb6`**（含 Hermes 的 T21；clone 发生在其推送之后） |
| 系统 **Python 3.12** venv + 依赖 | ✅ 3.12.3，`pip install -r requirements.txt` 成功 |
| 全量测试 **326 passed / 1 skipped** | ✅ **326 passed, 1 skipped in 82.82s**（在 `25bbbb6` 上跑的） |
| 是否需要改装 3.13（deadsnakes PPA） | ❌ **不需要**（⇒ 省掉一次系统级改动，见 §3） |
| dashboard systemd 服务 + `curl 127.0.0.1:8080/` 200 | ✅ active(running)，`GET /` **200**、`GET /api/status` **200**（合法 JSON，cash=100000） |
| 绝对禁止启用 cron | ✅ 自证：`crontab -l` **无任何 paper-trading 条目**；`systemctl --user list-units` 无残留 |

---

## 1. [P0 发现] NEC 是 **x86-64-v1 的 CPU**，而 numpy 从 **2.4** 起 wheel 基线抬到 v2 ⇒ 默认解析即崩

```
model name : Intel(R) Core(TM)2 Duo CPU T9900 @ 3.06GHz     （2008 年 Merom）
/proc/cpuinfo flags 里没有 sse4_2、没有 popcnt  ⇒ 只支持 x86-64-v1
```

现象（全新 venv、按 `requirements.txt` 解析）：

```
numpy 2.5.4 → ImportError: cannot load module more than once per process
             RuntimeError: NumPy was built with baseline optimizations: (X86_V2)
                           but your machine doesn't support: (X86_V2)
→ pytest 直接崩在 collection：5 errors during collection（test_bar_source /
  test_core / test_data_quality / test_lock_isolation / test_schemes）
```

边界实测（同一 venv 逐个装、逐个 import）：

| numpy | import |
|---|---|
| 2.2.6 | ✅ |
| 2.3.5 | ✅ |
| **2.4.2** | ❌ `RuntimeError ... (X86_V2)` |
| 2.5.4（默认解析结果） | ❌ 同上 |

⇒ **基线是在 numpy 2.4 抬上去的**。当前处置：NEC venv 钉 **`numpy==2.2.6`**（保守；2.3.x 也可用）。
最终环境：`Python 3.12.3 / numpy 2.2.6 / pandas 3.1.0 / akshare 1.20.1 / pytest 9.2.0 / PyYAML 6.0.3`。

**根因是 `requirements.txt` 只有下限没有上限**：

```
numpy>=1.24.0     ← 允许解析到 2.5.4，而 2.5.4 在 v1-only CPU 上无法 import
pandas>=2.0.0
```

任何**新部署机**（这次是 NEC，下次可能是任何老 x86 机器或 Win7 类环境）只要 `pip install -r`
就会拿到坏 wheel，而且报错信息（"cannot load module more than once"）完全不指向真实原因。
**建议**：`numpy>=1.26,<2.4`（或按主 AI 意见收到 `<2.3`）。这是仓库配置改动，我没动，
可另开 `worker/t20-numpy-pin` 分支提 `fix(worker):` 供你 review。

## 2. 顺带拿到的有用结论：本仓在 **pandas 3.1.0** 上全绿

这次全新安装把 pandas 解析到了 **3.1.0**（我此前在 `report-t5.md` P1-3 提示过 pandas 3
是 vnpy/rqalpha 会强塞进来的破坏性大版本）。NEC 上 **pandas 3.1.0 + Python 3.12 + 326 全绿**
⇒ **我们对 pandas 3 是兼容的**，迁移时不用为 pandas 3 单独预留适配工作（这是好消息，
可以划掉一项风险）。

## 3. 为什么不装 3.13（原计划的兜底动作没有触发）

任务书写的判据是"若非因代码本身的问题而不过绿，改装 3.13 再跑"。实际情况：
不过绿的起因是 **numpy wheel 的 CPU 基线**（环境问题、不是代码问题），但**它不由 Python 版本引起**——
升到 3.13 也拿不到 v2 基线以外的 numpy wheel，反而 `deadsnakes PPA` 会给这台生产机加一个
永久外部软件源。所以正确解法是**钉 numpy 版本**，不是换 Python。
⇒ **NEC 保持系统 Python 3.12.3**，无需 PPA、无需 sudo 改动系统包。

## 4. 服务与网络实况

```
systemctl --user status paper-dashboard
  ● paper-dashboard.service - paper-trading dashboard (T20 Phase 0, loopback only)
    Loaded: loaded (/home/nec/.config/systemd/user/paper-dashboard.service; disabled; preset: enabled)
    Active: active (running)   Memory: 9.9M (peak 10.2M)
GET /          -> 200
GET /api/status-> 200  {"ok": true, "data": {"timestamp": "...", "cash": 100000.0, ...}}
```

两个刻意的保守选择（等主 AI 拍）：
- **只绑 `127.0.0.1`**：面板 `SECURITY.md` 的前提是"仅局域网、无登录鉴权"，切换前的空库面板
  没必要对局域网开放；要 LAN 访问就改 `--host 0.0.0.0`，请你决定。
- **`is-enabled = disabled`**：只验证"能起来"，不做开机自启。真切换时再 `enable`
  （user unit 还需 `loginctl enable-linger nec` 才能无人登录自启，这一步要 sudo，等你点头）。

网络到上游数据源已验证：`qt.gtimg.cn` **200**、`push2.eastmoney.com` 404（可达，只是该路径无内容）
⇒ M2 实时线在 NEC 上取数没有网络障碍。

## 5. 需要主 AI 决定的 4 件事

1. **`requirements.txt` 加 numpy 上限**（建议 `<2.4`）——我开分支提 `fix(worker):`，还是你自己改？
2. **面板暴露与开机自启**：切到 NEC 生产后 `--host` 与 `systemctl --user enable` + linger 的时机。
3. **Phase 2 数据拷贝时点**：今晚 19:00+ 那场，NEC 上现在只有**空库**（面板显示 cash=100000 就是新库），
   拷贝前请勿在 NEC 上做任何写操作；我保持不碰。
4. **NEC 的容量约束**：2 核 / 5.9G RAM（可用约 4.5G）/ 机械盘 sda3（370G 空闲）。
   rqalpha bundle **3.3GB** + 未来分钟线积累 + 面板 + 引擎同机，Pi 的"过劳自重启"教训要算进去：
   建议 bundle 只放 NEC（不放 Pi），并把回测与盘中引擎的**并发窗口错开**（回测跑在收盘后）。

## 6. 我在 NEC 上的全部动作（供你复核/回滚）

```bash
git clone https://github.com/oldfunk/paper-trading.git ~/paper-trading     # 新目录，未覆盖任何东西
python3 -m venv ~/paper-trading/venv && ~/paper-trading/venv/bin/python -m pip install -r requirements.txt
~/paper-trading/venv/bin/python -m pip install numpy==2.2.6                # 修 §1 的坏解析
~/paper-trading/venv/bin/python -m pytest tests/ -q                        # 326 passed / 1 skipped
mkdir -p ~/.config/systemd/user && 写 paper-dashboard.service（未 enable）
systemctl --user start paper-dashboard && curl 127.0.0.1:8080/            # 200
```
未做：`crontab` 任何改动、`enable`/`--now`、`sudo` 包安装、PPA、局域网暴露、Pi 的一切操作。
回滚只需：`systemctl --user stop paper-dashboard && rm ~/.config/systemd/user/paper-dashboard.service && rm -rf ~/paper-trading`。
