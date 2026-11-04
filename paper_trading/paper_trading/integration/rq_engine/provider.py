"""状态持久化（断点续跑）——rqalpha 开源发行里缺的那块，按**钱类件**对待。

取证（report-t13 §1/§4.2）：6.4.2 core 只有 `interface.py:643` 的
`AbstractPersistProvider` 抽象类，**没有任何实现**，CLI 也没有 `-p` 开关；
`persist: true` 会在 `main.py:77-79` 直接抛 "Persist provider 未加载"。
需要持久化的对象由 rqalpha 自己指定（`main.py:81-92`）：
`user_context / global_vars / universe / portfolio / executor` +
`Persistable` 的 broker（未成交挂单）与各 mod。

这里补的是**存储**，不是判定：一 key 一文件 + 原子换名 + 校验和。
T33（同事 B）点名的 fsync/校验和已落：`fsync` 文件与目录、`.sha256` sidecar，
恢复时校验和不符 ⇒ 记入 `restore_errors` 并**拒绝给状态**——
rqalpha 的 `PersistHelper._restore_obj` 会吞异常（`persisit_helper.py:76-79`），
所以错误必须留在 provider 里由 runner 检查、停线，而不是指望框架报错。

同样不 import rqalpha（鸭子类型：只用 `store/load`）。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import date, datetime
from pathlib import Path
from typing import Optional

STATE_SUFFIX = ".state"
SUM_SUFFIX = ".sha256"
META_NAME = "session.json"


class PersistStateError(RuntimeError):
    """状态文件损坏/被截断/校验和不符——宁可停线，不拿半截状态继续跑。"""


class SessionGateError(RuntimeError):
    """G-T13-5：同日重跑 / 日期回退 / 配置指纹漂移。"""


def config_fingerprint(payload: dict) -> str:
    """把"会改变账本语义的配置"压成一个短哈希；漂移即拒续（fail-closed）。"""
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _fsync_dir(p: Path) -> None:
    """目录 fsync（尽力而为：Windows 不支持，Linux 上才是真原子性保证）。"""
    try:
        fd = os.open(str(p), os.O_RDONLY)
    except (OSError, AttributeError):
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


class FilePersistProvider:
    """一 key 一文件的态存储（带原子写 + 校验和）。"""

    def __init__(self, state_dir: str | Path) -> None:
        self.dir = Path(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.restore_errors: list[str] = []
        self.written_keys: list[str] = []

    # -- rqalpha PersistHelper 用的两个方法 --
    def store(self, key: str, value: bytes) -> None:
        if isinstance(value, str):
            value = value.encode("utf-8")
        target = self.dir / (key + STATE_SUFFIX)
        tmp = self.dir / (key + STATE_SUFFIX + ".tmp")
        with open(tmp, "wb") as f:
            f.write(value)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
        side = self.dir / (key + SUM_SUFFIX)
        side_tmp = self.dir / (key + SUM_SUFFIX + ".tmp")
        side_tmp.write_text(_sha(value), encoding="utf-8")
        os.replace(side_tmp, side)
        _fsync_dir(self.dir)
        if key not in self.written_keys:
            self.written_keys.append(key)

    def load(self, key: str) -> Optional[bytes]:
        target = self.dir / (key + STATE_SUFFIX)
        if not target.exists():
            return None
        data = target.read_bytes()
        side = self.dir / (key + SUM_SUFFIX)
        want = side.read_text(encoding="utf-8").strip() if side.exists() else None
        if want is None:
            self.restore_errors.append(f"{key}: 无校验和 sidecar，拒绝恢复")
            return None
        got = _sha(data)
        if got != want:
            self.restore_errors.append(f"{key}: 校验和不符（{got[:8]}≠{want[:8]}）疑似截断/篡改")
            return None
        return data

    # -- 辅助 --
    def should_resume(self) -> bool:
        """core 里没人调（死接口，见 report-t13 §4.10），保留以符合抽象签名。"""
        return any(p.suffix == STATE_SUFFIX for p in self.dir.glob("*"))

    def should_run_init(self) -> bool:
        return True

    def keys(self) -> list:
        return sorted(p.stem for p in self.dir.glob("*" + STATE_SUFFIX))

    def snapshot(self) -> dict:
        """当前态目录的可读概览（审计/排障用，不返回状态内容）。"""
        return {
            "dir": str(self.dir),
            "keys": self.keys(),
            "sizes": {p.stem: p.stat().st_size
                      for p in sorted(self.dir.glob("*" + STATE_SUFFIX))},
            "restore_errors": list(self.restore_errors),
        }

    def purge(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)
        self.dir.mkdir(parents=True, exist_ok=True)


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class SessionGate:
    """G-T13-5 幂等 + 配置指纹。

    rqalpha 的 resume 只跳 `before_trading`（`executor.py:66-67`），
    **不防同一天重跑**——实测 `--force` 绕过闸后同日重复成交、持仓翻倍
    （report-t13 §3 S5）。所以日期推进权必须握在我们手里。
    """

    def __init__(self, state_dir: str | Path) -> None:
        self.path = Path(state_dir) / META_NAME

    def read(self) -> Optional[dict]:
        if not self.path.exists():
            return None
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:            # noqa: BLE001 - 元数据坏了也要停线
            raise SessionGateError(f"session.json 解析失败：{exc}") from exc

    def check(self, today: date, fingerprint: str, *, force: bool = False,
              intraday_resume: bool = False) -> dict:
        """`intraday_resume=True`（M3b 日内多跳）：同日 + 有 state + 指纹一致
        ⇒ 放行续跑（T52 实证：resume 跳 before_trading，意图幂等由调用方
        `executed_ids` 提供）；同日**无** state 或指纹漂移仍拒。"""
        meta = self.read()
        if meta is None:
            return {"allowed": True, "prior": None, "force": force}
        if meta.get("fingerprint") != fingerprint:
            raise SessionGateError(
                "配置指纹漂移（存档 "
                f"{meta.get('fingerprint')} ≠ 当前 {fingerprint}）——"
                "换现金基数/费率/数据文件后续跑，会把两个口径的账本拼在一起")
        last = str(meta.get("last_day") or "")
        if last and last >= today.isoformat() and not force:
            if intraday_resume and last == today.isoformat():
                return {"allowed": True, "prior": last, "intraday": True}
            raise SessionGateError(
                f"状态已推进到 {last}，本次请求 {today.isoformat()} 不晚于它；"
                "同日重跑会重复下单（resume 只跳 before_trading，handle_bar 会再执行一次）")
        return {"allowed": True, "prior": last or None, "force": force}

    def mark_done(self, today: date, fingerprint: str, extra: Optional[dict] = None) -> dict:
        payload = {"last_day": today.isoformat(), "fingerprint": fingerprint,
                   "written_at": datetime.now().isoformat(timespec="seconds")}
        if extra:
            payload.update(extra)
        tmp = self.path.parent / (self.path.name + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)
        _fsync_dir(self.path.parent)
        return payload
