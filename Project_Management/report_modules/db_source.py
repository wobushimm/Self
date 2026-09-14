"""
db_source.py - 数据库直连取数层（双源校验的 DB 通道）

职责：
  1. ProjectDB：pymysql 只读连接池（线程安全）
  2. 为 fetch_data.py 提供 DB 查询通道（如 requirements 总需求数）
  3. 双源决策：API + DB 同时取数，按模式合并/降级

配置（环境变量）：
  PMO_DB_HOST / PMO_DB_PORT / PMO_DB_USER / PMO_DB_PASSWORD / PMO_DB_DATABASE
  PMO_DATA_MODE — api_first(默认) / db_first / api_only
"""

import os
import sys
import calendar
import threading
from typing import Any, Dict, List, Optional


def _log(msg: str):
    """简易日志（项目无统一 logger，输出到 stderr）。"""
    print(f"[DB] {msg}", file=sys.stderr)

# ===========================================================================
# 配置
# ===========================================================================
_DB_HOST = os.getenv("PMO_DB_HOST", "")
_DB_PORT = int(os.getenv("PMO_DB_PORT", "4000"))
_DB_USER = os.getenv("PMO_DB_USER", "root")
_DB_PASSWORD = os.getenv("PMO_DB_PASSWORD", "")
_DB_DATABASE = os.getenv("PMO_DB_DATABASE", "project_management")
_POOL_SIZE = int(os.getenv("PMO_DB_POOL_SIZE", "3"))
_QUERY_TIMEOUT = int(os.getenv("PMO_DB_QUERY_TIMEOUT", "30"))

# 数据模式：api_first(默认) / db_first / api_only
_DATA_MODE = os.getenv("PMO_DATA_MODE", "api_first")


# ===========================================================================
# ProjectDB - 连接池客户端
# ===========================================================================
class ProjectDB:
    """
    项目管理 MySQL 只读连接池。

    用法：
        db = get_project_db()
        rows = db.query("SELECT ...", params)
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._pool: List[Any] = []
        self._initialized = False
        self._available = None  # None=未检测, True/False=结果缓存 60s
        self._available_ts = 0.0

    def _ensure_pool(self):
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            if not _DB_HOST:
                _log("PMO_DB_HOST 未配置，DB 通道不可用")
                self._initialized = True
                return
            try:
                import pymysql
                for _ in range(_POOL_SIZE):
                    conn = pymysql.connect(
                        host=_DB_HOST,
                        port=_DB_PORT,
                        user=_DB_USER,
                        password=_DB_PASSWORD,
                        database=_DB_DATABASE,
                        charset="utf8mb4",
                        cursorclass=pymysql.cursors.DictCursor,
                        connect_timeout=5,
                        read_timeout=_QUERY_TIMEOUT,
                        autocommit=True,
                    )
                    self._pool.append(conn)
                _log(f"连接池初始化成功: {_DB_HOST}:{_DB_PORT}/{_DB_DATABASE} (pool={_POOL_SIZE})")
            except Exception as e:
                _log(f"连接池初始化失败: {e}")
            self._initialized = True

    def _get_conn(self):
        self._ensure_pool()
        with self._lock:
            if not self._pool:
                return None
            return self._pool.pop()

    def _return_conn(self, conn):
        with self._lock:
            self._pool.append(conn)

    def query(self, sql: str, params: tuple = ()) -> Optional[List[Dict]]:
        """执行只读查询。失败返回 None。"""
        conn = self._get_conn()
        if conn is None:
            return None
        try:
            import pymysql
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
            self._return_conn(conn)
            return rows
        except Exception as e:
            _log(f"查询失败: {e} | SQL: {sql[:120]}")
            # 尝试重连一次
            try:
                conn = pymysql.connect(
                    host=_DB_HOST, port=_DB_PORT,
                    user=_DB_USER, password=_DB_PASSWORD,
                    database=_DB_DATABASE, charset="utf8mb4",
                    cursorclass=pymysql.cursors.DictCursor,
                    connect_timeout=5, read_timeout=_QUERY_TIMEOUT,
                    autocommit=True,
                )
                self._return_conn(conn)
            except Exception:
                pass
            return None

    def is_available(self) -> bool:
        """快速检查 DB 是否可用（结果缓存 60 秒）。"""
        import time
        now = time.time()
        if self._available is not None and (now - self._available_ts) < 60:
            return self._available
        try:
            rows = self.query("SELECT 1 AS ok")
            self._available = rows is not None and len(rows) > 0
        except Exception:
            self._available = False
        self._available_ts = now
        return self._available


# 全局单例
_hera_db_instance: Optional[ProjectDB] = None
_hera_db_lock = threading.Lock()


def get_project_db() -> ProjectDB:
    """获取 ProjectDB 全局单例。"""
    global _hera_db_instance
    if _hera_db_instance is None:
        with _hera_db_lock:
            if _hera_db_instance is None:
                _hera_db_instance = ProjectDB()
    return _hera_db_instance


# ===========================================================================
# 双源决策（参照经营快报 _dual_source_resolve 模式）
# ===========================================================================

def _is_effective(value):
    """判断取值是否为有效数据（非 None 且非 0）。"""
    if value is None:
        return False
    if isinstance(value, (int, float)) and value == 0:
        return False
    return True


def dual_source_resolve(source_name: str, api_result, db_result):
    """
    API + DB 双源合并决策：
      - api_first（默认）：API 优先，API 无效（None/0）降级 DB
      - db_first：DB 优先，DB 无效降级 API
      - api_only：完全不走 DB
    返回最终使用的值（可能是 None，表示双源都无数据）。

    注：对于计数类指标，API 返回 0 通常意味着"未取到数据"而非"确实为 0"，
    因此 0 与 None 同样视为无效，允许对端补充。
    """
    api_ok = _is_effective(api_result)
    db_ok = _is_effective(db_result)

    # 双源都有有效数据 → 记录比对
    if api_ok and db_ok and api_result != db_result:
        _log(f"双源比对({source_name}): API={api_result}, DB={db_result} — 存在差异")

    if _DATA_MODE == "api_only":
        return api_result

    if _DATA_MODE == "db_first":
        if db_ok:
            return db_result
        if api_ok:
            _log(f"({source_name}) DB 无有效数据，降级使用 API 结果")
            return api_result
        return api_result if api_result is not None else db_result

    # api_first（默认）
    if api_ok:
        return api_result
    if db_ok:
        _log(f"({source_name}) API 无有效数据(api={api_result})，降级使用 DB 结果({db_result})")
        return db_result
    # 双源都无效 → 返回非 None 的那个
    return api_result if api_result is not None else db_result


def get_data_mode() -> str:
    """返回当前数据模式。"""
    return _DATA_MODE


# ===========================================================================
# DB 查询函数
# ===========================================================================

def db_fetch_total_requirement_count(settlement_month: str) -> Optional[int]:
    """
    从示例 requirements 表查询目标月份的总需求数。

    参数:
        settlement_month: 结算月份，格式 YYYY-MM（如 "2026-07"）

    返回:
        该月 expected_data 范围内的记录数；DB 不可用或查询失败返回 None。
    """
    db = get_project_db()
    if not db.is_available():
        return None

    # 计算月份日期范围
    parts = settlement_month.split("-")
    if len(parts) != 2:
        return None
    year, month = int(parts[0]), int(parts[1])
    day_start = f"{year}-{month:02d}-01"
    day_end = f"{year}-{month:02d}-{calendar.monthrange(year, month)[1]}"

    sql = "SELECT COUNT(*) AS cnt FROM requirements WHERE expected_date BETWEEN %s AND %s"
    rows = db.query(sql, (day_start, day_end))
    if rows is None or not rows:
        return None

    cnt = rows[0].get("cnt")
    if cnt is None:
        return None
    return int(cnt)
