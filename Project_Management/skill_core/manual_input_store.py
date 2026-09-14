"""
手工填报数据持久化存储
- 文件存储：JSON 格式，按报告期间保存
- 支持读取历史填报数据作为基准
- 用于 API 数据缺失时的补充
"""

import json
import os
import logging
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


def _dir_writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _resolve_storage_dir() -> Path:
    """解析可写存储目录。"""
    env = (os.environ.get("PMO_DATA_DIR") or "").strip()
    candidates = []
    if env:
        candidates.append(Path(env) / "manual_input" if not env.endswith("manual_input") else Path(env))
    skill_root = Path(__file__).resolve().parent.parent
    candidates.extend([
        skill_root / "output" / "manual_input",
        skill_root / "data" / "manual_input",
    ])
    seen = set()
    for cand in candidates:
        key = str(cand)
        if key in seen:
            continue
        seen.add(key)
        if _dir_writable(cand):
            return cand
    fallback = candidates[-1]
    logger.warning(f"手工填报目录均不可写，仍使用 {fallback}")
    return fallback


STORAGE_DIR = _resolve_storage_dir()


class ManualInputStore:
    """手工填报数据持久化"""

    def __init__(self, storage_dir: str = None):
        self.storage_dir = Path(storage_dir) if storage_dir else STORAGE_DIR
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def save(self, report_period: str, data: Dict[str, Any]) -> str:
        """保存手工填报数据"""
        safe_name = report_period.replace("年", "_").replace("月", "").replace("-", "_").replace(" ", "")
        filename = f"manual_{safe_name}_{datetime.now().strftime('%Y%m%d%H%M%S')}.json"
        filepath = self.storage_dir / filename

        record = {
            "report_period": report_period,
            "saved_at": datetime.now().isoformat(),
            "data": data,
        }

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)

        logger.info(f"手工填报数据已保存: {filepath}")
        return str(filepath)

    def load_latest(self, report_period: str = None) -> Optional[Dict[str, Any]]:
        """加载最新的手工填报数据"""
        files = sorted(self.storage_dir.glob("manual_*.json"), reverse=True)
        if not files:
            return None

        for filepath in files:
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    record = json.load(f)
                if report_period is None or record.get("report_period") == report_period:
                    return record.get("data", {})
            except Exception as e:
                logger.warning(f"读取手工填报数据失败 {filepath}: {e}")
        return None

    def list_all(self) -> list:
        """列出所有手工填报记录"""
        records = []
        for filepath in sorted(self.storage_dir.glob("manual_*.json"), reverse=True):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    record = json.load(f)
                records.append({
                    "file": filepath.name,
                    "report_period": record.get("report_period", ""),
                    "saved_at": record.get("saved_at", ""),
                })
            except Exception:
                pass
        return records
