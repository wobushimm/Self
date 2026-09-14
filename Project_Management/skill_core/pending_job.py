"""项目管理快报：待确认任务管理。

生成前若快照缺失关键字段，把本次生成意图落盘；
用户回复确认后自动续跑。
"""
from __future__ import annotations

import json
import logging
import os
import re
from contextvars import ContextVar
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

PENDING_TTL_HOURS = 24
PMO_SKILL_ID = "SKL_PMO_REPORT_001"

_user_id: ContextVar[str] = ContextVar("pmo_user_id", default="")
_thread_id: ContextVar[str] = ContextVar("pmo_thread_id", default="")

_SKIP = ("跳过", "先不填", "不用填", "直接生成", "空值生成")
_CANCEL = ("取消", "不生成了", "算了")
_CONFIRM = ("确认生成", "继续生成", "开始生成", "现在生成", "确认", "继续")


def set_request_identity(user_id: str = "", thread_id: str = "") -> None:
    _user_id.set(user_id or "")
    _thread_id.set(thread_id or "")


def get_request_identity() -> Tuple[str, str]:
    return _user_id.get() or "", _thread_id.get() or ""


def sync_identity_from_skill_output() -> Tuple[str, str]:
    """恢复 pending 身份（与 business_bulletin 同款）。

    skill_engine 执行前会清 skill_core.*，ContextVar 随之失效。
    优先读 skill_engine 工作线程里保存的认证 user_id/thread_id（通用版，
    与 match_pending_skill_resume 口径一致）；再回退 skill_output。
    """
    uid, tid = get_request_identity()
    if uid and tid and uid != "default" and tid != "default":
        return uid, tid
    try:
        from features.skill_engine import get_pending_request_identity
        su, st = get_pending_request_identity()
        if su or st:
            uid = su or uid
            tid = st or tid
            set_request_identity(uid, tid)
            return uid, tid
    except Exception:
        pass
    try:
        from storage.skill_output import get_identity
        ouid, otid = get_identity()
        if (ouid or otid) and (not uid or uid == "default"):
            uid = ouid or uid
            tid = otid or tid
            set_request_identity(uid, tid)
    except Exception:
        pass
    return uid, tid


def _safe_part(value: str) -> str:
    text = (value or "").strip() or "default"
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text)[:80]


def _pending_dir() -> Path:
    env = (os.environ.get("PMO_DATA_DIR") or "").strip()
    if env:
        root = Path(env)
        return root / "pending_jobs"
    skill_root = Path(__file__).resolve().parent.parent
    for cand in (
        skill_root / "output" / "pending_jobs",
    ):
        try:
            cand.mkdir(parents=True, exist_ok=True)
            probe = cand / ".write_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return cand
        except OSError:
            continue
    fallback = skill_root / "output" / "pending_jobs"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


class PendingJobStore:
    """按用户 + 会话保存待续跑的生成任务。"""

    def __init__(self, storage_dir: str = None):
        self.storage_dir = Path(storage_dir) if storage_dir else _pending_dir()
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, user_id: str, thread_id: str) -> Path:
        return self.storage_dir / f"pending_{_safe_part(user_id)}__{_safe_part(thread_id)}.json"

    def _read_valid(self, path: Path) -> Optional[Dict[str, Any]]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                job = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"读取待续跑任务失败 {path}: {e}")
            return None
        expires = job.get("expires_at") or ""
        try:
            if expires and datetime.fromisoformat(expires) < datetime.now():
                path.unlink(missing_ok=True)
                return None
        except ValueError:
            path.unlink(missing_ok=True)
            return None
        return job

    def save(self, job: Dict[str, Any], user_id: str = None,
             thread_id: str = None) -> str:
        # 身份回填：ContextVar 失效时从 skill_engine/skill_output 找回，
        # 防止 pending 落到 default__default 导致「先不填」续跑找不到任务
        sync_identity_from_skill_output()
        uid, tid = get_request_identity()
        user_id = user_id if user_id is not None else uid
        thread_id = thread_id if thread_id is not None else tid
        now = datetime.now()
        record = dict(job)
        record["user_id"] = user_id or "default"
        record["thread_id"] = thread_id or "default"
        record["saved_at"] = now.isoformat()
        record["expires_at"] = (now + timedelta(hours=PENDING_TTL_HOURS)).isoformat()
        record.setdefault("skill_id", PMO_SKILL_ID)
        path = self._path(record["user_id"], record["thread_id"])
        tmp = path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
        tmp.replace(path)
        logger.info(f"已保存待续跑任务: {path}")
        return str(path)

    def load(self, user_id: str = None, thread_id: str = None) -> Optional[Dict[str, Any]]:
        uid, tid = get_request_identity()
        user_id = user_id if user_id is not None else uid
        thread_id = thread_id if thread_id is not None else tid
        user_id = user_id or "default"
        thread_id = thread_id or "default"

        exact = self._path(user_id, thread_id)
        if exact.exists():
            job = self._read_valid(exact)
            if job:
                return job

        matches = []
        prefix = f"pending_{_safe_part(user_id)}__"
        for path in self.storage_dir.glob(prefix + "*.json"):
            job = self._read_valid(path)
            if job:
                matches.append(job)
        if len(matches) == 1:
            return matches[0]
        return None

    def clear(self, user_id: str = None, thread_id: str = None) -> None:
        uid, tid = get_request_identity()
        user_id = user_id if user_id is not None else uid
        thread_id = thread_id if thread_id is not None else tid
        user_id = user_id or "default"
        thread_id = thread_id or "default"
        path = self._path(user_id, thread_id)
        if path.exists():
            path.unlink(missing_ok=True)
            return
        prefix = f"pending_{_safe_part(user_id)}__"
        for extra in self.storage_dir.glob(prefix + "*.json"):
            extra.unlink(missing_ok=True)


def classify_pending_reply(query: str,
                           job: Optional[Dict[str, Any]] = None) -> str:
    """判断用户对「待确认」的回复类型。

    返回: skip | cancel | confirm | fill | unrelated
    """
    q = (query or "").strip()
    if not q:
        return "unrelated"

    compact = re.sub(r"\s+", "", q)
    if compact in _CANCEL or (len(q) <= 10 and any(q.startswith(p) for p in _CANCEL)):
        return "cancel"

    if any(p in q for p in _SKIP) and len(q) < 40:
        return "skip"

    if compact in _CONFIRM or q in _CONFIRM:
        return "confirm"

    return "unrelated"
