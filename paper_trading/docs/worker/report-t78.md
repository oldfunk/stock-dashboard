# report-t78.md — 永久门禁进 CI + 生产机每日实跑（Qoder · 2026-11-05）

派发：Batch 20 · T78（P0·M4-③）——"`m3 verify` + `test_init_positions_e2e` 四场景 +
wiring_audit 固化为 `.github/workflows/test.yml` 的独立步骤（退出码非 0 直接告警）"。
分支 `worker/t75-t80-wrapup`，未合。

## 1. CI 配置 diff（`.github/workflows/test.yml`）

```yaml
      - name: wiring audit (配置接线门禁)          # T67 已有，保持独立一步
        run: python tests/wiring_audit.py --quiet
      - name: m3 daily-check gate (每日核对门禁自检)   # ← T78 新增
        run: python tests/ci_gate_selfcheck.py
      - name: money & compliance contract tests (钱类/合规契约)   # ← T78 新增
        run: >-
          python -m pytest tests/test_init_align_t73.py tests/test_t75_hardening.py
          tests/test_decision_gate_t71.py tests/test_daily_check_t80.py
          tests/test_m3_verify_t46.py tests/test_engine_behavior_t68.py -q
      - name: init_positions 四场景（CI 无 rqalpha 会 skip，见 §4）   # ← T78 新增
        run: python -m pytest tests/test_init_positions_e2e.py -q -rs
      - run: python -m pytest tests/ -q
```

每步都是独立退出码，红在 CI 里直接指名是哪一层塌了（"配置接了线却没人读" vs
"钱类口径变了" vs "会话跑不成"），不再混成"800 条里有一条红"。

## 2. 门禁自己得会失败——`tests/ci_gate_selfcheck.py`

T81 的审点是"门禁**真跑**（不是配置写了）"。所以新增的不是一行 `pytest`，
而是一段会**主动构造必须红的现场**的自检（跑真 CLI、真子进程、真退出码）：

| 断言 | 为什么这条才算"真跑" |
|---|---|
| `m3 daily-check` 在合成库上产出 15 项，且 `init_align/carry_over/intent_chain/raw_gate/t_plus_one/fees/nav/heartbeat` 全在 | 工具本身活着、参数接线没断 |
| 健康基线**必须无红** | 永远红的门禁就是新壁纸（Q5 同一课） |
| 退出码与结论自洽：无红=0 / 有红=1 / 钱类红=3 | 尺子与 `m3 verify` §5.6 一致 |
| 造一个"该有会话留痕却没有"的现场 ⇒ `init_align` 变红且退出码非 0 | **门禁如果永远不会失败，它就不是门禁** |

本地复跑（Linux 与 CI 同一条路径）：

```
$ python tests/ci_gate_selfcheck.py
{"items": 15, "rc": 0, "forced_red_rc": 3, "rollback_required": true, "red_ids": ["init_align"]}
CI 门禁自检：4 项全过            # 退出码 0
```

## 3. 四场景的真身在生产机（`deploy/daily-check.sh`）

CI 跑不动四场景是**事实**，不是偷懒：需要 ① rqalpha（引擎 venv 才有）
② bundle（`~/.rqalpha/bundle`，28,063 合约）③ 当日分钟线（`minute_bars`）。
GitHub 托管 runner 三样都没有，`pip install rqalpha` 也变不出 bundle。
硬塞进 CI 只会得到一个"永远 skip 的绿色步骤"——那是假绿，比没有更坏。

所以四场景挂在生产机的每日核对里（`PT_RUN_SCENES=1` 默认开，`0` 可关）：
自己造账本副本 + `data.db` 副本 + 临时状态目录，`ledger=False`，
**不写 `paper_account.db`、不写 `op_log`、不碰 `var/rq_state`**。

现网彩排（候选树 `/home/nec/tmp/t75/tree`，非部署目录）：

```
[daily-check] 2026-11-05 daily_check exit=3      # nav/intent_queue/init_align 红（现状真实）
[daily-check] 2026-11-05 scenes exit=0           # A/B/C/D 全绿 + guard 绿，约 15s
SCRIPT_RC=3                                       # 取两者最差：钱类红 ⇒ 3
paper_account.db / data.db md5 前后同值（d3d98da6…）
```

彩排本身抓出两个洞（都已修，都是"门禁谎报"级别）：
1. `emit_line` 把解析结果丢了（`rec.update({k: rec.get(k)})` 读的是自己）⇒ JSONL 全 null；
2. `RC/SC_RC` 初始化成 1、只在失败时赋值 ⇒ **成功时也说 1**，门禁天天红。
   这种洞只有真跑一遍才会露出来，也正是不动 crontab、彩排到临时目录的价值。

## 4. 装法（我没碰 crontab，运维/主 AI 执行）

```
30 15 * * 1-5 /home/nec/paper-trading/deploy/daily-check.sh >> /home/nec/paper-trading/logs/daily_check.log 2>&1
```

产出 `logs/daily_check.jsonl`：一天两行（`kind=daily_check` 与 `kind=scenes`），
含 `exit_code/red_ids/warn_ids/scenes`——T79 的观察报告框架按天聚合就吃这个。

## 5. 门禁覆盖了什么、没覆盖什么（说人话）

- 覆盖：配置接线（wiring_audit）、钱类/合规纯契约（形态、复核、T+1 只加不减、
  时点门、账本口径、1d 聚合回退、raw_gate 分档、每日核对判别力）、
  全套单测/行为测（836 条）、生产机上每天一次的**真会话四场景**。
- 未覆盖：现网账本的真实一致性（那是 `m3 verify` 在生产机每天跑的那部分，
  见 §3 的 `daily_check` 行）；bundle 版本漂移（无 CI 侧物证，只能靠生产机红）。
- 若要把四场景也搬进 GitHub：需要一个仓库内的最小合成 bundle（`instruments.pk` +
  `ex_cum_factor.h5` + `stocks.h5`），那是独立一件活（我会估成 T 号任务），
  现在不做是为了不在 CI 里造一个"看起来像生产"的假环境。

## 6. 回归

全量 836 passed / 11 skipped；`wiring_audit` 退出码 0；
四场景在 T75 重构后重跑仍全绿（`report-t75.md` 末尾）。
