# report-t36 — `walk_forward` 的过拟合量度修复（P1，§5.5 裁决 7）

执行人：员工 AI（Qoder）｜2026-10-29｜分支 `worker/t36`（含产品代码，**待 review 不合并**）
来源：我在 T14 映射研究纪律时发现该缺陷（`report-t14.md` §6），主 AI 裁决"修 + 回归"。

> **一句话结论**：旧代码把"事后最优（oracle）"和"样本内第 1 名（picked）"
> 取成**重排后的同一行**，于是纪律 7 要的过拟合差距**从来没被算过**，
> 而 `picked` 还是个从未使用的死变量。修好后差距 = 事后最优 − 样本内第 1 名，
> 面板直接显示；**变异测试证明这三条回归测试在旧实现下必红**（我把旧形状临时
> 复原跑了一遍：3 failed；换回修复：6 passed）。
> 全套 **403 passed, 4 skipped**（基线 397+1，零回归）。

## 1. 缺陷本体（`engine/optimize.py:249-252`，修复前）

```python
oos_rows.sort(key=lambda x: -x["oos_excess_pct"])   # 先按样本外重排
oracle = oos_rows[0] ...                            # = 样本外最好的那个
picked = oos_rows[0] ...                            # 同一行；且 picked 全文未被使用
```
`walk_forward` 的 docstring 承诺"**样本内第一名与样本外第一名的差距，就是过拟合的量度**"，
但返回体里只有 `oracle_oos_excess`（事后最优）与 `sample_in_best_excess`
（**样本内**第 1 名的样本内超额）——**"样本内第 1 名到了样本外还剩多少"这个数缺失**，
面板与结论都拿不到它。

## 2. 修法

| 位置 | 改动 |
|---|---|
| `optimize.py` `walk_forward()` | 在**重排之前**按样本内顺序取 `in_best = oos_rows[0]`（候选列表本就是按样本内排名生成的），`oracle = max(oos_rows, key=oos_excess)`；再重排展示 |
| 返回体新增 | `in_best_oos_excess_pct / in_best_oos_symbol / in_best_oos_params`、`oracle_oos_params`、`overfit_gap_pct`（= oracle − in_best，两位小数）；`note` 改为写明差距含义 |
| 向后兼容 | 旧键 `sample_in_best_excess / oracle_oos_excess / n_oos_positive / out_of_sample / in_sample_top` **全部保留**（面板 `dashboard.py:687-690` 在读），新增 `oracle_oos_excess_pct` 与旧键同值 |
| 死变量 | `picked` 删除 |
| 面板 | walkforward 结果多一行：`样本内第 1 名到样本外还剩 X%；事后最优 Y%（标的）；过拟合差距 Z%`，差距>0 标红 |

**为什么取 `oos_rows[0]` 而不是 `max(rows, key=in_excess)`**：`grid_search` 的
`in_sample_top` 已经按 `(excess_pct, -mdd)` 排好序，`oos_rows` 按它的顺序生成；
用 `max(..., key=in_excess)` 在同分候选上会挑到不同行（我第一版就这么错过，
两个 `in_excess=-27.85` 的候选并列）——**"样本内第 1 名"的权威定义是排名列表的第 0 行**，
不是重新比较。测试也据此用 `in_sample_top[0]` 反查，而不是重算 max。

## 3. 回归测试（`tests/test_walk_forward_overfit_gap.py`，6 条）

fixture 刻意造出"A 前半段单边涨、后半段震荡；B 前半段震荡、后半段单边涨"，
使样本内第 1 名与事后最优**必然不是同一候选**：

| 测试 | 锁什么 |
|---|---|
| `test_fixture_really_has_divergence` | 前提：两候选 (symbol, params) 确实不同（否则下面的断言会空过） |
| `test_in_best_and_oracle_are_different_quantities` | `in_best_*` 必须等于 `in_sample_top[0]` 那一行的样本外值；`oracle_*` 等于样本外最大者 |
| `test_old_bug_shape_would_be_caught` | **`overfit_gap_pct > 0`** 且 oracle > in_best（旧实现恒为 0） |
| `test_overfit_gap_is_oracle_minus_in_best` | 差距就是两数之差，且非负 |
| `test_legacy_keys_still_present_for_panel` | 面板读的旧键不丢、新旧 oracle 同值 |
| `test_single_candidate_gaps_to_zero` | 只有一个候选时差距=0 且两数相等（不是 None/缺键） |

**变异验证**（把代码临时改回旧形状，跑完从 `/tmp` 副本还原，不用 `git checkout`）：

```
mutated(old shape): 3 failed, 3 passed      ← 缺陷会被这套测试抓到
restored(fixed):    6 passed
```

## 4. 影响面

- **只影响研究报告/面板展示**，不碰撮合与账本：钱路径零改动；
- 面板/CLI 读旧键处不破坏（兼容性测试覆盖）；
- 这条修复是 T14 实施步骤 S3 的前件：研究线迁到 rqalpha 时，
  注册表要存 `in_best_oos_excess_pct` 与 `overfit_gap_pct` 两个字段
  （`report-t14.md` §4 已按修好的字段名设计）。

## 5. 复现

```bash
python -m pytest tests/test_walk_forward_overfit_gap.py -q   # 6 passed
python -m pytest tests -q                                      # 403 passed, 4 skipped
# 变异验证（可选）：把 walk_forward 的 in_best/oracle 都改成排序后的 oos_rows[0]
#   → test_fixture_really_has_divergence / test_old_bug_shape_would_be_caught /
#     test_in_best_and_oracle_are_different_quantities 三条必须红
```

## 6. 顺带看到但没动的（P2，等你定）

1. `grid_search` 的并列同分（例：两参数组 `in_excess` 完全相同）目前靠 `sort` 稳定性决定
   谁是"第 1 名"，注册表里同分候选的取舍会影响可复现叙述——
   若要严谨，可加第二排序键（如 `params` 字符串）；属研究口径调整，我没动。
2. `walk_forward` 只看"样本内 top-K"再做样本外检验（K=`top`，面板传 5），
   过拟合的另一半（K 之外就再没机会）没有量化；要更严可改成全候选检验，代价是跑量。
