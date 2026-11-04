# report-t37 — lock 文件 + rqalpha 二进制基线 spike（部署，P1，§5.5 裁决 8）

执行人：员工 AI（Qoder）｜2026-10-29｜分支 `worker/t37`（只加两份 lock + 本文档 + 一行注释）
① 生产 venv freeze → `requirements.lock.txt`；② NEC `/tmp` 临时 venv 装 rqalpha 6.4.2
并做 import 全链实测（**没碰生产 venv、没装到 ~/paper-trading**，spike 用完即删）。

> **一句话结论**：rqalpha 在这台 x86-64-v1 机器上**可用**，但有一个必须先做的动作——
> **先钉 `numpy<2.4` 再装 rqalpha**。直接 `pip install rqalpha==6.4.2` 会解析到
> **numpy 2.5.4**，本机 import 当场
> `RuntimeError: NumPy was built with baseline optimizations: (X86_V2)`（本轮现场复现）；
> 按正确顺序装完后，h5py / scipy / matplotlib(Agg) / statsmodels / jsonpickle / logbook /
> pandas 3.1.0 / rqalpha 6.4.2 全部 import 通过，`rqalpha --help` 正常，`pip check` 干净。
> 体积 697 MB、安装 2 分 10 秒 ⇒ **引擎必须用独立 venv**，不与生产 venv 合并。

---

## 1. 交付物

| 文件 | 内容 |
|---|---|
| `requirements.lock.txt` | 生产 venv（NEC Py 3.12.3）35 个包的精确版本 + 头部说明（生成机/日期/用途/两条不要动 numpy 与不要合并 venv 的警告） |
| `requirements-rqalpha.lock.txt` | rqalpha 引擎 venv 的 41 个包精确版本 + **安装顺序坑**与可复制命令 |
| `requirements.txt` | 只加一行指针注释（区间 vs 钉死点的分工），**未改任何版本约束** |

## 2. 生产环境现状（freeze 摘要，只读采集）

```
Python 3.12.3   numpy==2.2.6   pandas==3.1.0   akshare==1.20.1   pytest==9.2.0
PyYAML==6.0.3   requests==2.34.2   lxml==6.1.3   curl_cffi==0.17.0   py-mini-racer==0.6.0
（共 35 个包；无 rqalpha、无 matplotlib/scipy/h5py —— 生产进程不需要它们）
```

与 `requirements.txt` 的区间一致：`numpy 2.2.6 < 2.4`、`pandas 3.1.0 < 4`、`akshare 1.20.1 < 2` ⇒
**现网本来就落在允许区间内**，lock 只是把"重装/新机"从运气活变成可复现活。

## 3. rqalpha spike 实测（NEC `/tmp/t37venv`，用完已删）

| 步骤 | 结果 |
|---|---|
| `python3 -m venv /tmp/t37venv && pip install rqalpha==6.4.2` | 成功，**自动解析 numpy 2.5.4**；耗时 2m10s；venv 697 MB |
| `python -c "import numpy"` | ❌ `RuntimeError: NumPy was built with baseline optimizations: (X86_V2) but your machine doesn't support: (X86_V2)` |
| `pip install 'numpy<2.4'` 后再 import | ✅ numpy **2.3.5**（与 T30 fresh venv 解析到的同一版本） |
| `import rqalpha / h5py / scipy / matplotlib / statsmodels / patsy / jsonpickle / logbook / click / pandas` | ✅ 全部通过（h5py 3.16.0、scipy 1.18.1、matplotlib 3.11.2、statsmodels 0.15.0、pandas 3.1.0） |
| `import run_func / BaseDataSource / SimulationMod / AbstractPersistProvider` | ✅ 我们适配器要用的四个入口都能取到 |
| `h5py` 写-读往返 | ✅ （适配器读 bundle 依赖它） |
| `matplotlib` Agg 后端出图 | ✅ （我们用不到，但它是 rqalpha 的硬依赖，headless 环境必须能 import） |
| `rqalpha --help` | ✅ 子命令正常列出 |

未做（按裁决 8 之外的范围，等主 AI 点头）：**在 NEC 下载 3.3 GB bundle 并跑一次真回测**。
理由：bundle 是 RiceQuant 公开包（体积大、要联网 `rqalpha bundle update`），
下载与放置位置属于部署决定；spike 只证明"装得上、导得进、CLI 起得来"。

## 4. 部署建议（M2/M3 用）

1. **两个 venv**：`~/paper-trading/venv`（交易/面板/采集，`requirements.lock.txt`）
   与 `~/venvs/rq-engine`（引擎进程，`requirements-rqalpha.lock.txt`）。
   引擎进程通过 `rq_engine` 适配器读写同一份 `data.db`，**不共享 Python 进程**。
2. **CI/新机**：先 `pip install -r requirements.lock.txt` 跑 404 测试，
   再单独建引擎 venv；不要把 rqalpha 塞进 `requirements.txt`（会让所有部署拖 700 MB）。
3. **升级窗口**：`numpy<2.4` 是硬上限，若将来要升 numpy，必须先在这台机上重跑
   T30 那份兼容矩阵（2.4+ 目前必炸）。
4. 磁盘余量实测 `367 GB free`（`/dev/sda3`）⇒ 双 venv + bundle(3.3 GB) + 分钟线积累都够。

## 5. 我不做的事

- 不装生产 venv、不改部署脚本、不动 cron、不打 tag（红线 2）；
- spike 全程在 `/tmp`，结束即 `rm -rf`（`df` 复核已回收），`~/.cache/pip` 也清了；
- 不下 bundle（见 §3 末，等主 AI 决定放置位置与是否需要离线包）。
