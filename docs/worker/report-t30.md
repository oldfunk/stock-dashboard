# report-t30 — requirements 上限 pin（x86-64-v1 部署安全，P0）

执行人：员工 AI（Qoder）｜2026-10-28｜分支 **`worker/t30`**（只动 `requirements.txt` + 本文档，待 review）
依据：`docs/worker/report-t20.md` §1（我发现 NEC 是 x86-64-v1 CPU）＋ 本轮双端 fresh-venv 实测

> **一句话结论**：`numpy>=1.26,<2.4` 是**必须的上限**——不 pin 时 NEC 上
> `pip install numpy pandas` 直接装到最新版并
> `RuntimeError: NumPy was built with baseline optimizations: (X86_V2)`
> **装得上、跑不了**（本轮现场复现）；pin 后全新 venv 安装解析到
> `numpy 2.3.5 / pandas 3.1.0 / akshare 1.20.2 / pytest 9.2.0`，
> `pip check` 干净，**340 passed, 1 skipped** 与现网基线一致。

---

## 1. 现役环境盘点（NEC 生产 venv，只读采集）

```
$ ssh nec "cd ~/paper-trading && venv/bin/python -V; venv/bin/pip list"
Python 3.12.3   sqlite 3.45.1
numpy   2.2.6      # 手工钉住的保守值（2.3.x 亦实测可用）
pandas  3.1.0
akshare 1.20.1
pytest  9.2.0
PyYAML  6.0.3
requests 2.34.2 / tqdm 4.70.1 / openpyxl 3.1.5
```

现网"看起来没问题"的原因：这些包是迁移时就装好的，**上限缺失只在重装/新机时暴露**——
正是这类"今天不炸、部署日炸"的问题最容易漏。

## 2. 改了什么，以及每条上限的证据强度

```diff
-akshare>=1.12.0
-pandas>=2.0.0
-numpy>=1.24.0
+akshare>=1.12.0,<2.0     # 现网 1.20.1；接口随数据源改名，跨大版本不做假设
+pandas>=2.0.0,<4.0       # 现网 3.1.0 全绿；4.x 未发布，这里只是护栏
+numpy>=1.26.0,<2.4       # ← 硬上限：v1-only CPU（唯一有实测失败证据的一条）
 pyyaml>=6.0
 pytest>=8.0
```

- **numpy `<2.4`**：有实测失败证据（下表 2.4.2 / 2.5.4 均 import 即 `X86_V2` RuntimeError），
  且官方 wheel 基线变化是单向的，不会自己好回去。
- **下限从 `>=1.24.0` 提到 `>=1.26.0`**：与 pandas 3.x 实际要求一致（1.24 已装不上 pandas 3），
  避免解析出一个装不上别的包的"合法下限"。
- **akshare / pandas 的上限是护栏，不是证据**：akshare 的历史接口改名是真实风险；
  pandas 4.x 尚未发布，写 `<4.0` 只是防止未来某天自动跨大版本。**这两条可以驳掉**。
- **pyyaml / pytest 不设上限**：没有任何实测失败，给它们加天花板只会增加未来解冲突成本。

## 3. 双端 fresh-venv 实测（任务书的 ③）

### NEC（生产同架构 x86-64-v1，Python 3.12.3）

```bash
python3 -m venv /tmp/t30fresh
git clone -q --branch worker/t30 https://github.com/oldfunk/paper-trading /tmp/t30repo
cd /tmp/t30repo && /tmp/t30fresh/bin/pip install -r requirements.txt
```

| 步骤 | 结果 |
|---|---|
| pip 解析 | `numpy 2.3.5 / pandas 3.1.0 / akshare 1.20.2 / pytest 9.2.0` |
| `pip check` | `No broken requirements found.` |
| `import numpy` | ✅ 通过（2.3.5 在 v1 上可用，与 T20 结论一致） |
| **`pytest tests -q`** | ✅ **340 passed, 1 skipped in 100.85s** —— 与现网基线逐条一致 |
| 反例（不 pin） | `python3 -m venv /tmp/t30bad && pip install numpy pandas` → **`RuntimeError: NumPy was built with baseline optimizations: (X86_V2) but your machine doesn't support: (X86_V2)`**（本轮现场复现） |

临时 venv 与临时 clone 已全部删除（`rm -rf /tmp/t30fresh /tmp/t30repo /tmp/t30bad`），
**没有动生产 venv、生产 checkout、cron、部署**。

### 本地（Windows）—— 两条较轻的证据，如实标注强度

| 步骤 | 结果 |
|---|---|
| `pip install --dry-run -r requirements.txt`（Py 3.13 venv，只做元数据解析） | **可解析**：`Would install … numpy-2.3.5 … akshare-1.20.2 … pytest-9.2.0`，与 NEC 解析结果同一组版本 ⇒ 上限没有过度约束 |
| `pytest tests -q`（现役 Py 3.14 + numpy 2.3.4 环境，本分支工作树） | ✅ **340 passed, 1 skipped** —— 与 NEC 基线逐条一致 |
| 本地**完整 fresh venv 安装** | ❌ **未完成**：`akshare` 的 `lxml/mini-racer` 在 Py3.14 上装到十分钟仍未出结果，我终止了它（不影响结论：架构相关证据是 NEC 那台 v1 机器） |

## 4. numpy 兼容矩阵（v1-only CPU 实测汇总）

| numpy | 来源 | import 结果 |
|---|---|---|
| 2.2.6 | T20 + 现网 | ✅ |
| 2.3.5 | T20 + **本轮 fresh venv 解析到的版本** | ✅ |
| 2.4.2 | T20 | ❌ `X86_V2` RuntimeError |
| 2.5.4 | T20 | ❌ `X86_V2`（并伴随 `ImportError: cannot load module more than once per process`） |
| pip 默认最新版 | **本轮复现** | ❌ `X86_V2` |

## 5. 这条上限的代价（要主 AI 知道的取舍）

1. **永久停在 2.3.x**：`<2.4` 意味着以后 numpy 的安全修复/性能改进 wheel 我们都装不到。
   三条出路，按代价排序：
   - (a) 维持上限 + 定期复核（几乎零成本，风险是长期用旧版）；
   - (b) **换 CPU 到 x86-64-v2+**（一次硬件/迁移成本，之后彻底自由）；
   - (c) 从源码编译 numpy（每次升级都要在 T9900 上编译，实测那台机器满载 30 分钟 85°C，
     编译属高风险长时间任务，我不推荐）。
2. **M2 装 rqalpha 时这条线要重测**：rqalpha 6.4.2 的依赖里还有
   `h5py / jsonpickle / logbook / tables(实为 h5py) / matplotlib` 等，
   每个都可能有自己的二进制基线；本报告的矩阵**不覆盖它们**。
   建议 M3 上 NEC 之前，用同一套 fresh-venv 方法在 NEC 试装 `rqalpha`
   （我可以在你点头后做一次只读 spike，装到 `/tmp` 里，不碰生产 venv）。
3. **上限不等于锁定版本**：真正想复现现网，应当用 `pip freeze` 出一份 lock 文件。
   要不要引入 `requirements.lock.txt`（NEC 生成、CI/新机使用）是主 AI 的决定，
   我不擅自加第二套依赖清单。

## 6. 我不做的事

- 不改生产 venv、不改部署脚本、不动 cron、不碰 `config.yaml` 默认值。
- 不在 main 上直接改 `requirements.txt`（走 `worker/t30` 分支，待你 review）。
- §2 里 akshare/pandas 两条护栏如果你认为多余，直接删——**numpy 那条不要删**。
