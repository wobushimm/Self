#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
项目月度快报数据拉取脚本

职责：按 project_id + project_name + settlement_month 拉取数据，
      完成字段映射、单位转换、校验，输出统一 ProjectMonthlySnapshot JSON。
      不负责 HTML / PDF / 指标文案 / 预警判断。

用法:
  python fetch_data.py --project-id <ID> --project-name "<名称>" --month <YYYY-MM>
  python fetch_data.py --project-id <ID> --project-name "<名称>" --month <YYYY-MM> --as-of <YYYY-MM-DD> --output output/report_data.json
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from typing import Any

# Fix Windows console encoding for CJK output
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _normalize_month_for_api(month_str: str) -> str:
    """将月份字符串转换为 API 期望的 YYYY-MM 格式。
    
    支持输入格式:
    - YYYY-MM (如 2026-07) -> 原样返回
    - YYYYQn (如 2026Q2) -> 转换为该季度末月 (2026-06)
    """
    import re
    # 已经是 YYYY-MM 格式
    if re.match(r'^\d{4}-\d{2}$', month_str):
        return month_str
    # 季度格式 YYYYQn
    m = re.match(r'^(\d{4})Q([1-4])$', month_str)
    if m:
        year = int(m.group(1))
        quarter = int(m.group(2))
        # 季度末月: Q1=03, Q2=06, Q3=09, Q4=12
        last_month = quarter * 3
        return f"{year}-{last_month:02d}"
    # 无法识别，原样返回
    return month_str

import requests
from requests.adapters import HTTPAdapter
from report_modules.db_source import db_fetch_total_requirement_count, dual_source_resolve, get_data_mode

# --- 本地数据模块（已有 API 响应数据） ---
try:
    from ...textdata.看板_data import RECORDS as BOARD_RECORDS
except ImportError:
    BOARD_RECORDS = None

try:
    from ...textdata.质量_data import RECORDS as QUALITY_RECORDS
except ImportError:
    QUALITY_RECORDS = None

try:
    from ...textdata.工时_data import RECORDS as WORKHOUR_RECORDS
    from ...textdata.工时_data import PAGE_TOTAL as WORKHOUR_TOTAL
except ImportError:
    WORKHOUR_RECORDS = None
    WORKHOUR_TOTAL = 0

try:
    from ...textdata.质量_page_data import FIRST_RECORD as QM_PAGE_RECORD
except ImportError:
    QM_PAGE_RECORD = None

# ============================================================
# 1. 平台配置（质量度量已接入真实 API，其余待接入）
# ============================================================

PLATFORM_CONFIG = {
    # --- 项目主数据平台（通过环境变量接入） ---
    "project": {
        "base_url": os.getenv("PMO_API_BASE_URL") or os.getenv("QUALITY_API_BASE", "https://api.example.com"),
        "token": os.getenv("QUALITY_API_TOKEN", ""),
        "cookies": {
            "SESSION": os.getenv("QUALITY_SESSION_COOKIE", ""),
            "X-Admin-Token": os.getenv("QUALITY_ADMIN_TOKEN", ""),
        },
        "endpoints": {
            "project_info": "/api/project-management/v1/projects",
            "project_board": "/api/project-management/v1/reports/project",
            "monthly_board": "/api/project-management/v1/reports/monthly",
            "staff_workhours": "/api/project-management/v1/workhours/staff",
            "common_page": "/api/project-management/v1/workhours/entries",
            "attendance": "/api/project-management/v1/attendance/entries",
        },
    },
    # --- 质量度量平台 ---
    "quality": {
        "base_url": os.getenv("PMO_API_BASE_URL") or os.getenv("QUALITY_API_BASE", "https://api.example.com"),
        "token": os.getenv("QUALITY_API_TOKEN", ""),
        "cookies": {
            "SESSION": os.getenv("QUALITY_SESSION_COOKIE", ""),
            "X-Admin-Token": os.getenv("QUALITY_ADMIN_TOKEN", ""),
        },
        "endpoints": {
            "trend": "/api/project-management/v1/quality/trends",
            "current": "/api/project-management/v1/quality/trends",
            "history": "/api/project-management/v1/quality/trends",
            "page": "/api/project-management/v1/quality/metrics",
        },
    },
    # --- 需求平台 ---
    "requirement": {
        "base_url": os.getenv("REQUIREMENT_API_BASE", "https://your-requirement-platform/api/v1"),
        "token": os.getenv("REQUIREMENT_API_TOKEN", ""),
        "endpoints": {
            "summary": "/requirements/summary",  # GET ?projectId=&month=
        },
    },
    # --- Bug / 缺陷平台 ---
    "bug": {
        "base_url": os.getenv("BUG_API_BASE", "https://your-bug-platform/api/v1"),
        "token": os.getenv("BUG_API_TOKEN", ""),
        "endpoints": {
            "summary": "/bugs/summary",          # GET ?projectId=&month=
        },
    },
    # --- 工时系统 ---
    "workhour": {
        "base_url": os.getenv("WORKHOUR_API_BASE", "https://your-workhour-system/api/v1"),
        "token": os.getenv("WORKHOUR_API_TOKEN", ""),
        "endpoints": {
            "project_summary": "/workhour/project-summary",  # GET ?projectId=&from=&to=
            "staff_daily": "/workhour/staff-daily",          # GET ?projectId=&from=&to=
        },
    },
}

# ============================================================
# 2. 常量
# ============================================================

HOURS_PER_DAY = 9
TIMEZONE = "Asia/Shanghai"
HTTP_TIMEOUT = 30  # seconds
HTTP_RETRIES = 3

MISSING = None  # 缺失值统一标记


# ============================================================
# 3. HTTP 工具
# ============================================================

def _make_session() -> requests.Session:
    s = requests.Session()
    adapter = HTTPAdapter(max_retries=HTTP_RETRIES)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    return s


_SESSION = _make_session()

# ============================================================
# 2b. 认证（账号密码自动登录，与经营快报 base_module.py 同模式）
# ============================================================

_BASE_URL = os.getenv("PMO_API_BASE_URL") or os.getenv("QUALITY_API_BASE", "https://api.example.com")
_LOGIN_URL = f"{_BASE_URL}/api/auth/login"
_USERNAME = os.getenv("PMO_API_USERNAME", "")
_PASSWORD = os.getenv("PMO_API_PASSWORD", "")

_auth_state = {"token": None, "session_cookie": None, "logged_in": False}


def _login() -> bool:
    """用账号密码登录 项目管理平台，获取 access token。
    返回 True 表示登录成功，False 表示失败。
    """
    import socket
    from urllib.parse import urlparse
    # 连通性检查（3s）
    try:
        parsed = urlparse(_BASE_URL)
        host = parsed.hostname or "localhost"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        sock = socket.create_connection((host, port), timeout=3)
        sock.close()
    except Exception as e:
        print(f"[WARN] 项目管理 API 不可达: {e}", file=sys.stderr)
        return False
    for attempt in range(2):
        try:
            resp = _SESSION.post(_LOGIN_URL, json={
                "userName": _USERNAME, "password": _PASSWORD, "captchaCode": ""
            }, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("code") in (200, "200"):
                    token = (data.get("data") or {}).get("accessToken") or \
                            (data.get("data") or {}).get("token")
                    if token:
                        _auth_state["token"] = token
                        _auth_state["logged_in"] = True
                        # 提取 SESSION cookie（如果平台返回）
                        for cookie in _SESSION.cookies:
                            if cookie.name == "SESSION":
                                _auth_state["session_cookie"] = cookie.value
                                break
                        print(f"[INFO] Hera 登录成功 (user={_USERNAME})", file=sys.stderr)
                        return True
            print(f"[WARN] Hera 登录失败: {resp.status_code} {resp.text[:200]}",
                  file=sys.stderr)
        except Exception as e:
            if attempt < 1:
                import time
                time.sleep(1)
            else:
                print(f"[WARN] Hera 登录异常: {e}", file=sys.stderr)
    return False


def _clear_auth():
    """清除认证状态（401 时调用）。"""
    _auth_state["token"] = None
    _auth_state["session_cookie"] = None
    _auth_state["logged_in"] = False


def _auth_headers() -> dict:
    """构建认证请求头。未登录时自动登录。"""
    if not _auth_state["token"]:
        _login()
    h = {"Accept": "application/json, text/plain, */*",
         "Origin": _BASE_URL, "Referer": _BASE_URL + "/"}
    if _auth_state["token"]:
        h["Authorization"] = f"Bearer {_auth_state['token']}"
    if _auth_state["session_cookie"]:
        h["Cookie"] = f"SESSION={_auth_state['session_cookie']}"
    return h


def _get(platform: str, endpoint_key: str, params: dict | None = None,
         path_params: dict | None = None) -> Any:
    """统一 GET 请求封装。返回 JSON dict；失败时抛异常。
    支持自动登录 + 401 重试。"""
    cfg = PLATFORM_CONFIG[platform]
    url = cfg["base_url"] + cfg["endpoints"][endpoint_key]
    if path_params:
        url = url.format(**path_params)
    headers = _auth_headers()
    if "json" not in headers:
        headers["Accept"] = "application/json, text/plain, */*"
    resp = _SESSION.get(url, params=params, headers=headers, timeout=HTTP_TIMEOUT)
    # 401: token 过期，重新登录后重试
    if resp.status_code == 401:
        _clear_auth()
        headers = _auth_headers()
        resp = _SESSION.get(url, params=params, headers=headers, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _post(platform: str, endpoint_key: str, json_body: dict | None = None,
          params: dict | None = None) -> Any:
    """统一 POST 请求封装。返回 JSON dict；失败时抛异常。
    支持自动登录 + 401 重试。"""
    cfg = PLATFORM_CONFIG[platform]
    url = cfg["base_url"] + cfg["endpoints"][endpoint_key]
    headers = _auth_headers()
    headers["Content-Type"] = "application/json;charset=UTF-8"
    resp = _SESSION.post(url, json=json_body, params=params,
                         headers=headers, timeout=HTTP_TIMEOUT)
    # 401: token 过期，重新登录后重试
    if resp.status_code == 401:
        _clear_auth()
        headers = _auth_headers()
        headers["Content-Type"] = "application/json;charset=UTF-8"
        resp = _SESSION.post(url, json=json_body, params=params,
                             headers=headers, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _fetch_qm_page(project_id: str, settlement_month: str = None) -> dict:
    """获取质量度量 page 接口数据（POST）。优先真实 API，回退本地数据。

    请求参数（来自浏览器抓包）:
      - projectId: 字符串，如 "12345"
      - statDate: 统计日期，如 "2026-08-25"
      - page / size: 分页
    """
    # 优先调用真实 API
    if project_id:
        try:
            body: dict = {"projectId": str(project_id), "page": 1, "size": 999999999}
            if settlement_month:
                # statDate 需要完整日期，使用月末日期
                _, date_to, _ = _month_range(settlement_month)
                body["statDate"] = date_to
            api_data = _post("quality", "page", json_body=body)
            records = api_data.get("data", {}).get("records", []) if isinstance(api_data, dict) else []
            if records:
                print(f"[INFO] 质量 page API 调用成功 (projectId={project_id})", file=sys.stderr)
                return records[0]
        except Exception as e:
            print(f"[WARN] 质量 page API 调用失败: {e}，回退到本地数据", file=sys.stderr)

    # 回退: 本地数据
    if QM_PAGE_RECORD:
        return QM_PAGE_RECORD
    return {}


def _safe_float(val) -> float | None:
    if val is None or val == "":
        return MISSING
    try:
        return float(val)
    except (ValueError, TypeError):
        return MISSING


def _safe_int(val) -> int | None:
    if val is None or val == "":
        return MISSING
    try:
        return int(val)
    except (ValueError, TypeError):
        return MISSING


def _convert_hours(val, unit: str) -> tuple[float | None, float | None, str]:
    """返回 (converted_hour, original_value, source_unit)。"""
    if val is None:
        return MISSING, MISSING, unit
    fval = _safe_float(val)
    if fval is None:
        return MISSING, MISSING, unit
    if unit == "person_day":
        return fval * HOURS_PER_DAY, fval, "person_day"
    return fval, fval, "person_hour"


def _month_range(month: str) -> tuple[str, str, str]:
    """返回 (date_from, date_to, next_month) 基于 YYYY-MM。"""
    y, m = int(month[:4]), int(month[5:7])
    date_from = f"{y}-{m:02d}-01"
    if m == 12:
        date_to = f"{y}-12-31"
        next_month = f"{y + 1}-01"
    else:
        import calendar
        last_day = calendar.monthrange(y, m)[1]
        date_to = f"{y}-{m:02d}-{last_day:02d}"
        next_month = f"{y}-{m + 1:02d}"
    return date_from, date_to, next_month


def _prev_months(month: str, count: int = 3) -> list[str]:
    """返回 month 之前 count 个月的列表（含 month 本身则 +1）。"""
    y, m = int(month[:4]), int(month[5:7])
    result = []
    for i in range(count):
        total = m - i
        py, pm = divmod(total - 1, 12)
        ry, rm = y + py, (pm % 12) + 1
        result.append(f"{ry}-{rm:02d}")
    return list(reversed(result))


# ============================================================
# 4. 平台适配函数（7个）
# ============================================================

def _fetch_project_info(project_id: str) -> dict:
    """获取项目主数据（GET /v1/project?id=）。优先真实 API，回退本地数据。"""
    # 优先调用真实 API
    if project_id:
        try:
            api_data = _get("project", "project_info", params={"id": project_id})
            records = api_data.get("data", {}).get("records", []) if isinstance(api_data, dict) else []
            if records:
                print(f"[INFO] 项目主数据 API 调用成功 (id={project_id})", file=sys.stderr)
                return records[0]
        except Exception as e:
            print(f"[WARN] 项目主数据 API 调用失败: {e}，回退到本地数据", file=sys.stderr)
    # 回退: 本地数据
    if BOARD_RECORDS:
        return BOARD_RECORDS[0]
    return {}


def _camel_to_snake(name: str) -> str:
    """camelCase 转 snake_case"""
    import re
    s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
    return re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()


def _fetch_monthly_board(project_id: str, settlement_month: str) -> dict:
    """获取月度项目看板数据。优先 project report（分摊后口径），回退 monthly report（原始口径）。
    请求参数（来自浏览器抓包）:
      - projectIds: 数字数组，如 [12345]
      - startMonth / endMonth: 月份范围，如 "2026-07"
      - manHourType: 1（project report 需要）
      - projectStatus: "1"（project report 需要）
      - page / size: 分页（query string）
    """
    if project_id and settlement_month:
        body_base = {
            "projectIds": [int(project_id)],
            "startMonth": settlement_month,
            "endMonth": settlement_month,
        }
        # 1) 优先 project report（分摊后口径，与交接文档基线一致）
        try:
            api_data = _post("project", "project_board",
                             json_body={**body_base, "manHourType": 1, "projectStatus": "1"},
                             params={"page": 1, "size": 10})
            # 兼容多种响应格式：list / {"data": [...]} / {"data": {"records": [...]}}
            records = []
            if isinstance(api_data, list):
                records = api_data
            elif isinstance(api_data, dict):
                data = api_data.get("data")
                if isinstance(data, list):
                    records = data
                elif isinstance(data, dict):
                    records = data.get("records", [])
            if records and any(v != 0 for k, v in records[0].items() if isinstance(v, (int, float)) and k not in ("project_id", "projectName", "project_id")):
                print(f"[INFO] 产品线看板 API 调用成功 (id={project_id}, month={settlement_month}, 口径=分摊后)",
                      file=sys.stderr)
                # 将 camelCase 字段转为 snake_case
                raw = records[0]
                return {_camel_to_snake(k): v for k, v in raw.items()}
        except Exception as e:
            print(f"[WARN] 产品线看板 API 调用失败: {e}", file=sys.stderr)
        # 2) 回退 monthly report（原始口径，同样用 product-line-board/project，不带 manHourType）
        try:
            api_data = _post("project", "monthly_board",
                             json_body={**body_base, "manHourType": 0, "projectStatus": "1"},
                             params={"page": 1, "size": 10})
            records = []
            if isinstance(api_data, list):
                records = api_data
            elif isinstance(api_data, dict):
                data = api_data.get("data")
                if isinstance(data, list):
                    records = data
                elif isinstance(data, dict):
                    records = data.get("records", [])
            if records:
                print(f"[INFO] 月度看板 API 调用成功 (id={project_id}, month={settlement_month}, 口径=原始)",
                      file=sys.stderr)
                # 将 camelCase 字段转为 snake_case
                raw = records[0]
                return {_camel_to_snake(k): v for k, v in raw.items()}
        except Exception as e:
            print(f"[WARN] 月度看板 API 调用失败: {e}，回退到本地数据", file=sys.stderr)
    # 回退: 本地数据
    if BOARD_RECORDS:
        return BOARD_RECORDS[0]
    return {}


# 可通过业务系统动态发现项目；公开版不内置任何真实项目 ID。
_ALL_PROJECT_IDS: list[int] = []


def fetch_quarterly_board(project_id: str, start_month: str, end_month: str) -> dict:
    """获取季度（多月份）聚合项目看板数据。

    调用 project report API，传入 startMonth/endMonth 跨月范围，
    返回该项目在该季度内的聚合指标（可加指标为各月之和，比率指标为重新计算）。
    """
    if project_id and start_month and end_month:
        body = {
            "startMonth": start_month,
            "endMonth": end_month,
            "manHourType": 1,
            "projectIds": [int(project_id)],
            "projectStatus": "1",
        }
        try:
            api_data = _post("project", "project_board",
                             json_body=body,
                             params={"page": 1, "size": 10})
            records = api_data.get("data", {}).get("records", []) if isinstance(api_data, dict) else []
            if records:
                print(f"[INFO] 季度聚合看板 API 调用成功 (id={project_id}, range={start_month}~{end_month})",
                      file=sys.stderr)
                return records[0]
        except Exception as e:
            print(f"[WARN] 季度聚合看板 API 调用失败: {e}", file=sys.stderr)
    return {}


def get_project(project_id: str, project_name: str, settlement_month: str = None) -> dict:
    """
    拉取项目主数据。
    数据源:
      - GET /v1/project → 项目基本信息（编码、经理、部门、预算）
      - POST monthly report → 月度指标（工时、任务、Bug）
    返回: project 块
    """
    # 1. 项目主数据（编码、经理、部门、预算）
    proj = _fetch_project_info(project_id)
    # 2. 月度看板（工时、任务、Bug 等月度指标）
    board = _fetch_monthly_board(project_id, settlement_month) if settlement_month else {}

    # 合并: proj 提供基本信息, board 提供月度指标
    dept_name = MISSING
    if isinstance(proj.get("sysDept"), dict):
        dept_name = proj["sysDept"].get("deptName", MISSING)

    return {
        "project_id": str(proj.get("id", project_id)),
        "project_name": proj.get("name", project_name),
        "project_code": proj.get("code", MISSING),
        "stat_month": board.get("stat_month", settlement_month or MISSING),
        # 工时与成本（来自月度看板）
        "actual_total_man_hour": _safe_float(board.get("actual_total_man_hour")),
        "actual_rd_man_hour": _safe_float(board.get("actual_rd_man_hour")),
        "actual_test_man_hour": _safe_float(board.get("actual_test_man_hour")),
        "plan_total_man_hour": _safe_float(board.get("plan_total_man_hour")),
        "attendance_hour": _safe_float(board.get("attendance_hour")),
        "actual_staff_cost": _safe_float(board.get("actual_staff_cost")),
        "plan_staff_cost": _safe_float(board.get("plan_staff_cost")),
        "actual_dev_cost": _safe_float(board.get("actual_dev_cost")),
        "plan_dev_cost": _safe_float(board.get("plan_dev_cost")),
        # 任务（来自月度看板）
        "task_count": _safe_int(board.get("task_count")),
        "task_finish_count": _safe_int(board.get("task_finish_count")),
        "task_delay_count": _safe_int(board.get("task_delay_count")),
        "high_difficulty_task_count": _safe_int(board.get("high_difficulty_task_count")),
        # 质量（来自月度看板）
        "bug_leak_rate": _safe_float(board.get("bug_leak_rate")),
        "bug_close_rate": _safe_float(board.get("bug_close_rate")),
        "bug_resolve_duration": _safe_float(board.get("bug_resolve_duration")),
        "deviation_ratio": _safe_float(board.get("deviation_ratio")),
        # 基本信息（来自项目主数据）
        "manager_name": proj.get("managerUserName", MISSING),
        "department_name": dept_name,
        "product_line": dept_name if dept_name is not MISSING else MISSING,
        "as_of_date": datetime.now().strftime("%Y-%m-%d"),
        "_source": "project_api+monthly_board",
        "_retrieved_at": datetime.now().isoformat(),
        "_data_status": "ok" if (proj or board) else "missing",
    }


def get_quality_current(project_code: str, project_id: str, settlement_month: str) -> dict:
    """
    拉取质量度量实时数据（当月快照）。
    返回: quality_current 块，字段名与契约 §4 对齐。
    """
    # 优先调用真实 API
    raw = {}
    if project_code:
        try:
            api_data = _get("quality", "current", params={
                "projectCode": project_code, "endMonth": f"{_normalize_month_for_api(settlement_month)}-25"
            })
            records = api_data.get("data", []) if isinstance(api_data, dict) else []
            raw = next((r for r in records if r.get("statMonth") == settlement_month), {})
            print(f"[INFO] 质量度量 API 调用成功: {settlement_month}", file=sys.stderr)
        except Exception as e:
            print(f"[WARN] 质量度量 API 调用失败: {e}，回退到本地数据", file=sys.stderr)

    # 回退: 本地数据
    if not raw and QUALITY_RECORDS:
        raw = next((r for r in QUALITY_RECORDS if r.get("statMonth") == settlement_month), {})

    return {
        # 累计指标
        "progress_exec_rate": _safe_float(raw.get("progressExecRate")),
        "rd_cost_exec_rate": _safe_float(raw.get("rdCostExecRate")),
        "staff_cost_exec_rate": _safe_float(raw.get("staffCostExecRate")),
        "actual_workhour": _safe_float(raw.get("actualWorkhour")),
        "estimated_workhour": _safe_float(raw.get("estimatedWorkhour")),
        "workhour_exec_rate": _safe_float(raw.get("workhourExecRate")),
        "total_change_count": _safe_int(raw.get("totalChangeCount")),
        "engineering_change_count": _safe_int(raw.get("engineeringChangeCount")),
        "req_verify_rate": _safe_float(raw.get("reqVerifyRate")),
        "bug_close_rate": _safe_float(raw.get("bugCloseRate")),
        "bug_resolve_duration_seconds": _safe_int(raw.get("bugResolveDuration")),
        "bug_leak_rate": _safe_float(raw.get("bugLeakRate")),
        # 月度指标
        "rd_actual_cost_month": _safe_float(raw.get("rdActualCostMonth")),
        "rd_budget_cost_month": _safe_float(raw.get("rdBudgetCostMonth")),
        "rd_cost_exec_rate_month": _safe_float(raw.get("rdCostExecRateMonth")),
        "staff_actual_cost_month": _safe_float(raw.get("staffActualCostMonth")),
        "staff_budget_cost_month": _safe_float(raw.get("staffBudgetCostMonth")),
        "staff_cost_exec_rate_month": _safe_float(raw.get("staffCostExecRateMonth")),
        "actual_workhour_month": _safe_float(raw.get("actualWorkhourMonth")),
        "estimated_workhour_month": _safe_float(raw.get("estimatedWorkhourMonth")),
        "workhour_exec_rate_month": _safe_float(raw.get("workhourExecRateMonth")),
        # 元数据
        "_source": "quality_platform",
        "_retrieved_at": datetime.now().isoformat(),
        "_data_status": "ok" if raw else "missing",
    }


def get_quality_history(project_code: str, project_id: str,
                        settlement_month: str, months: int = 4) -> list[dict]:
    """
    拉取质量度量历史趋势（settlement_month 及此前 months-1 个月）。
    返回: quality_history 数组，每条结构与 quality_current 相同，额外含 settlement_month。
    """
    history_months = _prev_months(settlement_month, months - 1) + [settlement_month]
    history_months.sort()

    def _build_quality_record(raw, month):
        """从原始记录构建统一结构的 quality 数据块"""
        return {
            "progress_exec_rate": _safe_float(raw.get("progressExecRate")),
            "rd_cost_exec_rate": _safe_float(raw.get("rdCostExecRate")),
            "staff_cost_exec_rate": _safe_float(raw.get("staffCostExecRate")),
            "actual_workhour": _safe_float(raw.get("actualWorkhour")),
            "estimated_workhour": _safe_float(raw.get("estimatedWorkhour")),
            "workhour_exec_rate": _safe_float(raw.get("workhourExecRate")),
            "total_change_count": _safe_int(raw.get("totalChangeCount")),
            "engineering_change_count": _safe_int(raw.get("engineeringChangeCount")),
            "req_verify_rate": _safe_float(raw.get("reqVerifyRate")),
            "bug_close_rate": _safe_float(raw.get("bugCloseRate")),
            "bug_resolve_duration_seconds": _safe_int(raw.get("bugResolveDuration")),
            "bug_leak_rate": _safe_float(raw.get("bugLeakRate")),
            "rd_actual_cost_month": _safe_float(raw.get("rdActualCostMonth")),
            "rd_budget_cost_month": _safe_float(raw.get("rdBudgetCostMonth")),
            "rd_cost_exec_rate_month": _safe_float(raw.get("rdCostExecRateMonth")),
            "staff_actual_cost_month": _safe_float(raw.get("staffActualCostMonth")),
            "staff_budget_cost_month": _safe_float(raw.get("staffBudgetCostMonth")),
            "staff_cost_exec_rate_month": _safe_float(raw.get("staffCostExecRateMonth")),
            "actual_workhour_month": _safe_float(raw.get("actualWorkhourMonth")),
            "estimated_workhour_month": _safe_float(raw.get("estimatedWorkhourMonth")),
            "workhour_exec_rate_month": _safe_float(raw.get("workhourExecRateMonth")),
            "settlement_month": month,
            "_source": "quality_platform",
            "_retrieved_at": datetime.now().isoformat(),
            "_data_status": "ok" if raw else "missing",
        }

    # 优先调用真实 API（单次调用获取所有月份）
    if project_code:
        try:
            api_data = _get("quality", "history", params={
                "projectCode": project_code, "endMonth": f"{_normalize_month_for_api(settlement_month)}-25"
            })
            all_records = api_data.get("data", []) if isinstance(api_data, dict) else []
            if all_records:
                print(f"[INFO] 质量历史 API 调用成功: {len(all_records)} 条记录", file=sys.stderr)
                results = []
                for m in history_months:
                    raw = next((r for r in all_records if r.get("statMonth") == m), {})
                    results.append(_build_quality_record(raw, m))
                return results
        except Exception as e:
            print(f"[WARN] 质量历史 API 调用失败: {e}，回退到本地数据", file=sys.stderr)

    # 回退: 本地数据
    if QUALITY_RECORDS:
        results = []
        for m in history_months:
            raw = next((r for r in QUALITY_RECORDS if r.get("statMonth") == m), {})
            results.append(_build_quality_record(raw, m))
        return results

    # 最终回退: 逐月调用
    results = []
    for m in history_months:
        q = get_quality_current(project_code, project_id, m)
        q["settlement_month"] = m
        results.append(q)
    return results


def get_requirements(project_id: str, settlement_month: str, project_code: str = None) -> dict:
    """
    拉取需求明细。
    数据源: /quality/metrics API → totalReqCount / passedReqCount / reqVerifyRate
    返回: requirement_detail 块
    """
    raw = _fetch_qm_page(project_id, settlement_month)

    total = _safe_int(raw.get("totalReqCount"))
    verified = _safe_int(raw.get("passedReqCount"))
    rate = _safe_float(raw.get("reqVerifyRate"))

    # 校验：rate 必须满足 verified / total（±0.0001 容差）
    if total and verified and rate:
        expected = verified / total
        if abs(expected - rate) > 0.0001:
            print(f"[WARN] reqVerifyRate mismatch: {rate} vs expected {expected:.4f}",
                  file=sys.stderr)

    return {
        "total_requirement_count": total,
        "verified_requirement_count": verified,
        "req_verify_rate": rate,
        "req_verify_detail": raw.get("reqVerifyDetail", MISSING),
        "req_verify_rate_warn_level": raw.get("reqVerifyRateWarnLevel", MISSING),
        "_source": "quality_page",
        "_retrieved_at": datetime.now().isoformat(),
        "_data_status": "ok" if raw else "missing",
    }


def get_bug_summary(project_id: str, settlement_month: str, project_code: str = None) -> dict:
    """
    拉取 Bug 明细与质量汇总。
    数据源: /quality/metrics API → totalBugCount / closedBugCount / fieldBugCount 等
    返回: bug_detail 块
    """
    raw = _fetch_qm_page(project_id, settlement_month)

    total = _safe_int(raw.get("totalBugCount"))
    closed = _safe_int(raw.get("closedBugCount"))
    close_rate = _safe_float(raw.get("bugCloseRate"))

    # 校验：close_rate 必须满足 closed / total
    if total and closed and close_rate:
        expected = closed / total
        if abs(expected - close_rate) > 0.0001:
            print(f"[WARN] bugCloseRate mismatch: {close_rate} vs expected {expected:.4f}",
                  file=sys.stderr)

    return {
        "bug_total_count": total,
        "bug_closed_count": closed,
        "field_bug_count": _safe_int(raw.get("fieldBugCount")),
        "bug_resolve_duration_seconds": _safe_int(raw.get("bugResolveDuration")),
        "bug_resolve_duration_detail": raw.get("bugResolveDurationDetail", MISSING),
        "bug_resolve_duration_exclude_reject_third": _safe_int(raw.get("bugResolveDurationExcludeRejectThrid")),
        "bug_close_rate": close_rate,
        "bug_close_detail": raw.get("bugCloseDetail", MISSING),
        "bug_close_rate_warn_level": raw.get("bugCloseRateWarnLevel", MISSING),
        "bug_leak_rate": _safe_float(raw.get("bugLeakRate")),
        "_source": "quality_page",
        "_retrieved_at": datetime.now().isoformat(),
        "_data_status": "ok" if raw else "missing",
    }


def get_project_workhour_summary(project_id: str, date_from: str, date_to: str,
                                 settlement_month: str = None) -> dict:
    """
    拉取工时汇总（项目维度）。
    数据源: POST monthly report → actual_total_man_hour / plan_total_man_hour 等
    返回: workhour_summary 块
    """
    # 数据源: 月度看板 API
    raw = _fetch_monthly_board(project_id, settlement_month) if settlement_month else {}
    if not raw and BOARD_RECORDS:
        raw = BOARD_RECORDS[0]

    # 月度看板字段: actual_total_man_hour, plan_total_man_hour, attendance_hour
    raw_allocated = raw.get("actual_total_man_hour")
    raw_estimated = raw.get("plan_total_man_hour")
    raw_reported = raw.get("attendance_hour")
    raw_unit = "person_hour"  # 看板数据默认人时

    alloc_hour, alloc_orig, alloc_src = _convert_hours(raw_allocated, raw_unit)
    est_hour, est_orig, est_src = _convert_hours(raw_estimated, raw_unit)
    rep_hour, rep_orig, rep_src = _convert_hours(raw_reported, raw_unit)

    warnings = []
    if raw_allocated is None:
        warnings.append("allocated_hour_missing")

    return {
        "actual_allocated_hour": alloc_hour,
        "estimated_hour": est_hour,
        "reported_hour": rep_hour,
        "unit_source": raw_unit,
        "conversion_rule": f"1 person_day = {HOURS_PER_DAY} hours",
        "member_count": MISSING,  # 看板数据无此字段
        "_original_allocated": alloc_orig,
        "_original_estimated": est_orig,
        "_source": "monthly_board_api",
        "_retrieved_at": datetime.now().isoformat(),
        "_data_status": "ok" if raw else "missing",
        "_warnings": warnings,
    }


# 默认查询的部门 ID 列表（覆盖主要研发中心部门）
DEFAULT_DEPT_IDS = os.getenv("PMO_DEPT_IDS", "")


def _fetch_staff_workhours(project_name: str, date_from: str, date_to: str) -> list[dict]:
    """获取部门人员工时数据（GET staff workhours endpoint）。
    返回该时间段内所有部门人员的月度工时，按 project_name 过滤出目标项目。
    优先真实 API，回退本地数据。
    """
    # 优先调用真实 API
    if project_name:
        try:
            api_data = _get("project", "staff_workhours", params={
                "deptIds": DEFAULT_DEPT_IDS,
                "startDate": date_from,
                "endDate": date_to,
            })
            records = api_data.get("data", []) if isinstance(api_data, dict) else []
            if records:
                # 按 projectName 过滤目标项目（优先精确匹配，回退子串包含）
                filtered = [r for r in records if r.get("projectName") == project_name]
                if not filtered:
                    filtered = [r for r in records
                                if project_name in (r.get("projectName") or "")
                                or (r.get("projectName") or "") in project_name]
                print(f"[INFO] 人员工时 API 调用成功: {len(records)} 条记录, "
                      f"目标项目 {project_name} 匹配 {len(filtered)} 条", file=sys.stderr)
                return filtered
        except Exception as e:
            print(f"[WARN] 人员工时 API 调用失败: {e}，回退到本地数据", file=sys.stderr)
    # 回退: 本地数据
    if WORKHOUR_RECORDS:
        return WORKHOUR_RECORDS
    return []


def get_staff_daily_workhours(project_id: str, project_name: str,
                              date_from: str, date_to: str) -> list[dict]:
    """
    拉取人员工时明细（员工—项目粒度）。
    数据源: GET staff workhours endpoint → 月度聚合的员工-项目工时
    注意: 该 API 返回月度汇总，非日粒度；work_date / is_workday / leave_hour 不可得。
    返回: staff_daily_workhours 数组
    """
    raw_list = _fetch_staff_workhours(project_name, date_from, date_to)

    results = []
    for item in raw_list:
        # 真实 API 字段: staffName, projectName, deptName, manHours, orderNum
        man_hours = _safe_float(item.get("manHours"))
        if item.get("staffName"):  # 真实 API 格式
            results.append({
                "work_date": MISSING,  # 月度聚合，无日粒度
                "employee_id": MISSING,  # API 不提供员工编号
                "staff_name": item.get("staffName", MISSING),
                "project_id": project_id,
                "project_name": item.get("projectName", project_name),
                "department_name": item.get("deptName", MISSING),
                "estimated_hour": MISSING,
                "reported_hour": MISSING,
                "allocated_hour": man_hours,
                "source_unit": "person_hour",
                "is_workday": MISSING,
                "leave_hour": MISSING,
            })
        else:
            # 回退: 本地工时数据格式
            raw_unit = "person_hour"
            raw_alloc = item.get("manHourReportAllocation")
            alloc_hour, alloc_orig, _ = _convert_hours(raw_alloc, raw_unit)
            results.append({
                "task_id": item.get("taskId"),
                "task_name": item.get("taskName"),
                "project_id": project_id,
                "project_name": project_name,
                "charge_user": item.get("chargeUserName"),
                "plan_start_date": item.get("planStartDate"),
                "plan_end_date": item.get("planEndDate"),
                "actual_start_time": item.get("actualStartTime"),
                "actual_end_time": item.get("actualEndTime"),
                "estimated_hour": _safe_float(item.get("manHourEstimate")),
                "reported_hour": _safe_float(item.get("manHourReport")),
                "allocated_hour": alloc_hour,
                "source_unit": raw_unit,
                "original_value": alloc_orig if alloc_orig is not None else _safe_float(item.get("manHourReportAllocation")),
            })

    return results


def get_manhour_common_page(date_from: str, date_to: str,
                             staff_no: str = "", page: int = 1, size: int = 9999) -> list[dict]:
    """
    拉取工时明细（员工-项目-日粒度）。
    数据源: GET /man-hour/workhour entries
    返回: 原始记录列表，包含 staffName, projectName, reportDate, reportManHour 等。
    """
    params = {"page": page, "size": size, "startDate": date_from, "endDate": date_to}
    if staff_no:
        params["staffNo"] = staff_no
    try:
        api_data = _get("project", "common_page", params=params)
        if isinstance(api_data, dict):
            return api_data.get("data", {}).get("records", [])
    except Exception as e:
        print(f"[WARN] workhour entries API 调用失败: {e}", file=sys.stderr)
    return []


def get_attendance(date_from: str, date_to: str,
                   staff_no: str = "", page: int = 1, size: int = 9999) -> list[dict]:
    """
    拉取考勤数据（员工-日粒度）。
    数据源: GET /attendance
    返回: 原始记录列表，包含 workDate, checkInTime, checkOutTime, attendanceHour 等。
    """
    params = {"page": page, "size": size, "startDate": date_from, "endDate": date_to}
    if staff_no:
        params["staffNo"] = staff_no
    try:
        api_data = _get("project", "attendance", params=params)
        if isinstance(api_data, dict):
            return api_data.get("data", {}).get("records", [])
    except Exception as e:
        print(f"[WARN] attendance API 调用失败: {e}", file=sys.stderr)
    return []


# ============================================================
# 5. 校验逻辑（7项强制对账）
# ============================================================

def run_validations(snapshot: dict) -> tuple[list[str], list[str]]:
    """
    执行契约 §11 的 7 项校验。
    返回: (missing_fields, warnings)
    """
    missing = []
    warnings = []

    proj = snapshot["project"]
    qc = snapshot["quality_current"]
    wh = snapshot["workhour_summary"]
    req = snapshot["requirement_detail"]
    bug = snapshot["bug_detail"]
    staff = snapshot["staff_daily_workhours"]

    now = datetime.now().isoformat()

    # --- 校验1: project_id + project_name 在所有来源一致 ---
    pid = proj["project_id"]
    pname = proj["project_name"]
    if proj.get("_data_status") == "missing":
        missing.append("project.project_id/name")
    # 检查 quality_current 中的 project 交叉一致性（如果API返回了projectName）
    # 此处留扩展点：如果 quality 返回了 projectName 字段，比对 pname

    # --- 校验2: 所有工时统一为小时 ---
    # 已在 _convert_hours 中完成，检查 unit_source 标记
    if wh.get("unit_source") not in ("person_hour", "person_day", MISSING):
        warnings.append(f"unexpected unit_source: {wh.get('unit_source')}")

    # --- 校验3: verified_requirement_count <= total_requirement_count ---
    total_req = req.get("total_requirement_count")
    verified_req = req.get("verified_requirement_count")
    if total_req is not None and verified_req is not None:
        if verified_req > total_req:
            warnings.append(
                f"verified_requirement_count({verified_req}) > total_requirement_count({total_req})"
            )
    # 与 req_verify_rate 对账
    if total_req and verified_req and req.get("req_verify_rate") is not None:
        expected = verified_req / total_req
        if abs(expected - req["req_verify_rate"]) > 0.0001:
            warnings.append(
                f"req_verify_rate({req['req_verify_rate']}) != verified/total({expected:.4f})"
            )

    # --- 校验4: bug_closed_count <= bug_total_count ---
    bug_total = bug.get("bug_total_count")
    bug_closed = bug.get("bug_closed_count")
    if bug_total is not None and bug_closed is not None:
        if bug_closed > bug_total:
            warnings.append(
                f"bug_closed_count({bug_closed}) > bug_total_count({bug_total})"
            )
    # 与 bug_close_rate 对账
    if bug_total and bug_closed and bug.get("bug_close_rate") is not None:
        expected = bug_closed / bug_total
        if abs(expected - bug["bug_close_rate"]) > 0.0001:
            warnings.append(
                f"bug_close_rate({bug['bug_close_rate']}) != closed/total({expected:.4f})"
            )

    # --- 校验5: actual_allocated_hour 与 SUM(staff_daily_workhours.allocated_hour) 对账 ---
    alloc_sum = wh.get("actual_allocated_hour")
    if alloc_sum is not None and staff:
        staff_sum = sum(s["allocated_hour"] for s in staff if s.get("allocated_hour") is not None)
        if abs(alloc_sum - staff_sum) > 0.01:
            warnings.append(
                f"workhour_summary.actual_allocated_hour({alloc_sum}) != "
                f"SUM(staff_daily.allocated_hour)({staff_sum:.2f}), diff={abs(alloc_sum - staff_sum):.4f}"
            )

    # --- 校验6: 成本执行率与"实际/预算"对账 ---
    # 研发成本
    rd_actual = qc.get("rd_actual_cost_month")
    rd_budget = qc.get("rd_budget_cost_month")
    rd_rate = qc.get("rd_cost_exec_rate_month")
    if rd_actual is not None and rd_budget is not None and rd_budget != 0 and rd_rate is not None:
        expected = rd_actual / rd_budget
        if abs(expected - rd_rate) > 0.01:
            warnings.append(
                f"rd_cost_exec_rate_month({rd_rate}) != actual/budget({expected:.4f})"
            )
    elif rd_budget == 0 and rd_actual is not None:
        missing.append("quality_current.rd_budget_cost_month (budget is 0)")

    # 人工成本
    sf_actual = qc.get("staff_actual_cost_month")
    sf_budget = qc.get("staff_budget_cost_month")
    sf_rate = qc.get("staff_cost_exec_rate_month")
    if sf_actual is not None and sf_budget is not None and sf_budget != 0 and sf_rate is not None:
        expected = sf_actual / sf_budget
        if abs(expected - sf_rate) > 0.01:
            warnings.append(
                f"staff_cost_exec_rate_month({sf_rate}) != actual/budget({expected:.4f})"
            )
    elif sf_budget == 0 and sf_actual is not None:
        missing.append("quality_current.staff_budget_cost_month (budget is 0)")

    # --- 校验7: 必填字段 data_status 检查 ---
    required_fields = {
        "project.project_id": proj.get("project_id"),
        "project.project_name": proj.get("project_name"),
        "project.project_code": proj.get("project_code"),
        "project.manager_name": proj.get("manager_name"),
        "quality_current.rd_cost_exec_rate": qc.get("rd_cost_exec_rate"),
        "quality_current.staff_cost_exec_rate": qc.get("staff_cost_exec_rate"),
        "quality_current.actual_workhour": qc.get("actual_workhour"),
        "quality_current.estimated_workhour": qc.get("estimated_workhour"),
        "quality_current.workhour_exec_rate": qc.get("workhour_exec_rate"),
        "quality_current.req_verify_rate": qc.get("req_verify_rate"),
        "quality_current.bug_close_rate": qc.get("bug_close_rate"),
        "quality_current.bug_resolve_duration_seconds": qc.get("bug_resolve_duration_seconds"),
        "quality_current.bug_leak_rate": qc.get("bug_leak_rate"),
        "quality_current.rd_actual_cost_month": qc.get("rd_actual_cost_month"),
        "quality_current.rd_budget_cost_month": qc.get("rd_budget_cost_month"),
        "quality_current.rd_cost_exec_rate_month": qc.get("rd_cost_exec_rate_month"),
        "quality_current.staff_actual_cost_month": qc.get("staff_actual_cost_month"),
        "quality_current.staff_budget_cost_month": qc.get("staff_budget_cost_month"),
        "quality_current.staff_cost_exec_rate_month": qc.get("staff_cost_exec_rate_month"),
        "quality_current.actual_workhour_month": qc.get("actual_workhour_month"),
        "quality_current.estimated_workhour_month": qc.get("estimated_workhour_month"),
        "quality_current.workhour_exec_rate_month": qc.get("workhour_exec_rate_month"),
        "requirement_detail.total_requirement_count": req.get("total_requirement_count"),
        "requirement_detail.verified_requirement_count": req.get("verified_requirement_count"),
        "requirement_detail.req_verify_rate": req.get("req_verify_rate"),
        "db_total_requirement_count": snapshot.get("db_total_requirement_count"),
        "resolved_total_requirement_count": snapshot.get("resolved_total_requirement_count"),
        "bug_detail.bug_total_count": bug.get("bug_total_count"),
        "bug_detail.bug_closed_count": bug.get("bug_closed_count"),
        "bug_detail.bug_resolve_duration_seconds": bug.get("bug_resolve_duration_seconds"),
        "bug_detail.bug_close_rate": bug.get("bug_close_rate"),
        "bug_detail.bug_leak_rate": bug.get("bug_leak_rate"),
        "workhour_summary.actual_allocated_hour": wh.get("actual_allocated_hour"),
        # estimated_hour (计划工时) 看板 API 经常不返回，降级为可选字段
        # "workhour_summary.estimated_hour": wh.get("estimated_hour"),
        "workhour_summary.unit_source": wh.get("unit_source"),
        "workhour_summary.conversion_rule": wh.get("conversion_rule"),
        "workhour_summary.member_count": wh.get("member_count"),
    }
    for field, val in required_fields.items():
        if val is MISSING or val is None:
            missing.append(field)

    # 合并 workhour_summary 自身 warnings
    warnings.extend(wh.get("_warnings", []))

    return missing, warnings


# ============================================================
# 5.5 项目 ID / 名称自动补全
# ============================================================

def _resolve_project_pair(project_id: str, project_name: str) -> tuple:
    """当 project_id 或 project_name 缺失时，自动从 API 项目列表补全。

    Returns:
        (resolved_id, resolved_name) — 尽力补全，仍缺失则返回原值。
    """
    if project_id and project_name:
        return project_id, project_name

    try:
        data = _get("project", "project_info", params={"id": "", "page": 1, "size": 200})
        projects = data.get("data", {}).get("records", []) if isinstance(data, dict) else []
    except Exception as e:
        print(f"[WARN] 项目列表查询失败，无法自动补全: {e}", file=sys.stderr)
        return project_id, project_name

    if not projects:
        return project_id, project_name

    # ── 有 ID 缺名称：按 ID 查找 ──
    if project_id and not project_name:
        for p in projects:
            if str(p.get("id", "")) == str(project_id):
                project_name = p.get("name", "")
                print(f"[INFO] 自动补全项目名: id={project_id} → {project_name}", file=sys.stderr)
                break

    # ── 有名称缺 ID：按名称模糊匹配 ──
    elif project_name and not project_id:
        # 精确匹配优先
        for p in projects:
            if p.get("name", "") == project_name:
                project_id = str(p.get("id", ""))
                print(f"[INFO] 自动补全项目ID: {project_name} → id={project_id}", file=sys.stderr)
                break
        # 模糊匹配（多策略：正向子串 → 反向子串 → 前缀匹配）
        if not project_id and len(project_name) >= 2:
            # 正向：关键词是项目名的子串
            matches = [p for p in projects if project_name in p.get("name", "")]
            # 反向：项目名是关键词的子串（用户输入可能多了几个字）
            if not matches:
                matches = [p for p in projects if p.get("name", "") in project_name]
            # 前缀匹配
            if not matches:
                matches = [p for p in projects
                           if p.get("name", "").startswith(project_name)
                           or project_name.startswith(p.get("name", ""))]
            if len(matches) == 1:
                project_id = str(matches[0].get("id", ""))
                project_name = matches[0].get("name", "")
                print(f"[INFO] 模糊匹配项目: '{project_name}' → id={project_id}", file=sys.stderr)
            elif len(matches) > 1:
                # 多个匹配时优先选名称最短的（最精确）
                matches.sort(key=lambda p: len(p.get("name", "")))
                project_id = str(matches[0].get("id", ""))
                project_name = matches[0].get("name", "")
                names = [m.get("name", "") for m in matches[:5]]
                print(f"[INFO] 模糊匹配到多个项目({len(matches)}个): {names}，自动选取最精确的: '{project_name}' → id={project_id}", file=sys.stderr)

    return project_id, project_name


# ============================================================
# 6. 主函数：组装 ProjectMonthly Snapshot JSON
# ============================================================

def fetch(project_id: str, project_name: str, settlement_month: str,
          as_of_date: str | None = None) -> dict:
    """
    主入口：拉取全部数据并组装 ProjectMonthlySnapshot。
    project_id 和 project_name 至少提供一个，另一个会自动补全。
    """
    as_of = as_of_date or datetime.now().strftime("%Y-%m-%d")
    date_from, date_to, _ = _month_range(settlement_month)

    # --- 自动补全：至少需要一个标识 ---
    if not project_id and not project_name:
        raise ValueError("请提供项目名称或项目ID（至少一个）")
    project_id, project_name = _resolve_project_pair(project_id, project_name)
    if not project_id or not project_name:
        raise ValueError(f"无法定位项目：id={project_id!r}, name={project_name!r}，请检查输入是否正确")

    print(f"[INFO] 开始拉取数据: project_id={project_id}, name={project_name}, month={settlement_month}",
          file=sys.stderr)

    # 1. 项目主数据（API + 月度看板）
    project = get_project(project_id, project_name, settlement_month)

    # ── 项目存在性校验：API 未返回任何有效数据 ──
    if project.get("_data_status") == "missing":
        raise ValueError(
            f"项目不存在（ID={project_id}，名称={project_name}）。"
            f"请检查项目 ID 或名称是否正确。"
        )

    #    从项目 API 获取 project_code
    project_code = project.get("project_code") or MISSING
    #    使用 API 返回的真实项目名（而非 CLI 参数），避免名称不一致导致过滤失败
    actual_project_name = project.get("project_name") or project_name

    # 2. 质量实时数据（优先真实 API，回退本地数据）
    quality_current = get_quality_current(project_code, project_id, settlement_month)

    # 从质量数据回填 projectCode 和 managerName（如果项目 API 未提供）
    if project_code is MISSING and quality_current.get("_data_status") == "ok":
        qc_rec = None
        if QUALITY_RECORDS:
            qc_rec = next((r for r in QUALITY_RECORDS if r.get("statMonth") == settlement_month), None)
        if qc_rec:
            project_code = qc_rec.get("projectCode", MISSING)
            if project_code:
                project["project_code"] = project_code
            if project.get("manager_name") is MISSING:
                project["manager_name"] = qc_rec.get("managerName", MISSING)
            # 用真实 projectCode 重新调用 API
            if project_code and project_code is not MISSING:
                quality_current = get_quality_current(project_code, project_id, settlement_month)

    # 3. 质量历史趋势（含当月，共4个月）
    if project_code:
        quality_history = get_quality_history(project_code, project_id, settlement_month, months=4)
    else:
        quality_history = []
        print("[WARN] project_code 缺失，跳过 quality_history", file=sys.stderr)

    # 4. 需求明细
    requirement_detail = get_requirements(project_id, settlement_month, project_code)

    # 4b. 总需求数：API + DB 双源决策
    api_total_req = requirement_detail.get("total_requirement_count")  # API 通道
    db_total_req = None
    if get_data_mode() != "api_only":
        try:
            db_total_req = db_fetch_total_requirement_count(settlement_month)
        except Exception as e:
            print(f"[WARN] DB 总需求数异常: {e}", file=sys.stderr)
    resolved_total_req = dual_source_resolve("total_requirement_count", api_total_req, db_total_req)
    if resolved_total_req is not None:
        print(f"[INFO] 总需求数({settlement_month}): {resolved_total_req} (api={api_total_req}, db={db_total_req})", file=sys.stderr)
    else:
        print(f"[WARN] 总需求数双源均无数据，将降级到任务完成数", file=sys.stderr)

    # 5. Bug 明细
    bug_detail = get_bug_summary(project_id, settlement_month, project_code)

    # 6. 工时汇总
    workhour_summary = get_project_workhour_summary(project_id, date_from, date_to, settlement_month)

    # 7. 人员日工时明细（使用 API 返回的真实项目名进行过滤）
    staff_daily_workhours = get_staff_daily_workhours(project_id, actual_project_name, date_from, date_to)

    # 8. 工时明细（全员，用于计算加班/跨项目）
    manhour_common = get_manhour_common_page(date_from, date_to)
    print(f"[INFO] 工时明细 API 调用成功: {len(manhour_common)} 条记录", file=sys.stderr)

    # 9. 考勤明细（全员，用于计算总可用工时）
    attendance_records = get_attendance(date_from, date_to)
    print(f"[INFO] 考勤 API 调用成功: {len(attendance_records)} 条记录", file=sys.stderr)

    # 从员工数据回填 member_count
    if staff_daily_workhours:
        unique_staff = set()
        for s in staff_daily_workhours:
            name = s.get("staff_name") or s.get("charge_user")
            if name:
                unique_staff.add(name)
        if unique_staff:
            workhour_summary["member_count"] = len(unique_staff)

    # --- 组装 ---
    snapshot = {
        "request": {
            "project_id": project_id,
            "project_name": project_name,
            "settlement_month": settlement_month,
            "as_of_date": as_of,
            "timezone": TIMEZONE,
        },
        "project": project,
        "quality_current": quality_current,
        "quality_history": quality_history,
        "requirement_detail": requirement_detail,
        "resolved_total_requirement_count": resolved_total_req,
        "db_total_requirement_count": db_total_req,
        "bug_detail": bug_detail,
        "workhour_summary": workhour_summary,
        "staff_daily_workhours": staff_daily_workhours,
        "manhour_common": manhour_common,
        "attendance_records": attendance_records,
        "data_quality": {
            "missing_fields": [],
            "warnings": [],
            "source_versions": {},
        },
    }

    # --- 回填: 看板无工时数据时，从人员日工时汇总计算 ---
    if staff_daily_workhours:
        staff_total = sum(
            float(s.get("allocated_hour", 0) or 0) for s in staff_daily_workhours
        )
        if staff_total > 0:
            # 回填 project.actual_total_man_hour
            if project.get("actual_total_man_hour") is None:
                snapshot["project"]["actual_total_man_hour"] = staff_total
                print(f"[INFO] 看板无工时数据，从人员日工时汇总回填 project.actual_total_man_hour={staff_total:.2f}h", file=sys.stderr)
            # 回填 workhour_summary.actual_allocated_hour
            if snapshot["workhour_summary"].get("actual_allocated_hour") is None:
                snapshot["workhour_summary"]["actual_allocated_hour"] = staff_total
                snapshot["workhour_summary"]["_original_allocated"] = staff_total
                snapshot["workhour_summary"]["_source"] = "staff_daily_workhours_fallback"
                # 移除 allocated_hour_missing 告警
                if "allocated_hour_missing" in snapshot["workhour_summary"].get("_warnings", []):
                    snapshot["workhour_summary"]["_warnings"].remove("allocated_hour_missing")
                print(f"[INFO] 看板无工时数据，从人员日工时汇总回填 workhour_summary.actual_allocated_hour={staff_total:.2f}h", file=sys.stderr)

    # --- 校验 ---
    missing, warnings = run_validations(snapshot)

    # 记录 source_versions
    now = datetime.now().isoformat()
    for block in ["project", "quality_current", "requirement_detail",
                   "bug_detail", "workhour_summary"]:
        blk = snapshot[block]
        if blk.get("_source"):
            snapshot["data_quality"]["source_versions"][blk["_source"]] = {
                "retrieved_at": blk.get("_retrieved_at", now),
                "data_status": blk.get("_data_status", "unknown"),
            }

    snapshot["data_quality"]["missing_fields"] = missing
    snapshot["data_quality"]["warnings"] = warnings

    # 汇总
    print(f"[INFO] 拉取完成: missing={len(missing)}, warnings={len(warnings)}", file=sys.stderr)
    if missing:
        print(f"[WARN] 缺失字段: {', '.join(missing[:10])}{'...' if len(missing) > 10 else ''}",
              file=sys.stderr)
    if warnings:
        print(f"[WARN] 校验告警: {', '.join(warnings[:5])}{'...' if len(warnings) > 5 else ''}",
              file=sys.stderr)

    return snapshot


# ============================================================
# 7. CLI 入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="项目月度快报数据拉取 — 输出 ProjectMonthlySnapshot JSON"
    )
    parser.add_argument("--project-id", required=True, help="项目 ID")
    parser.add_argument("--project-name", required=True, help="项目名称")
    parser.add_argument("--month", required=True, help="结算月份 YYYY-MM，如 2026-07")
    parser.add_argument("--as-of", default=None, help="数据截至日期 YYYY-MM-DD，默认今天")
    parser.add_argument("--output", "-o", default=None,
                        help="输出 JSON 文件路径，默认输出到 stdout")
    args = parser.parse_args()

    snapshot = fetch(
        project_id=args.project_id,
        project_name=args.project_name,
        settlement_month=args.month,
        as_of_date=args.as_of,
    )

    output = json.dumps(snapshot, ensure_ascii=False, indent=2)

    if args.output:
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"[INFO] JSON 已写入: {args.output}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
