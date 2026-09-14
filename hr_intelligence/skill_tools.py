"""人力智算AI助手 — 智能体工具入口。

定义 Skill 对外暴露的工具函数，供 LangChain ReAct Agent 或自定义调度器调用。
每个工具函数内调用本地 Python 引擎完成实际工作。

数据流：用户上传 Excel 底表 → 本地解析与计算 → 输出处理后的底表。
不从任何平台 API 拉取数据，所有数据均由用户底表承载。

当接入公司智能体时，SKILL_TOOLS 列表注册到 Agent；
独立运行时，可直接调用函数或通过 CLI 入口 main() 使用。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Optional

# 确保 Skill 根目录在 sys.path
_SKILL_DIR = Path(__file__).resolve().parent
if str(_SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_DIR))

# 用户上传附件目录
_CHAT_FILES_ROOT = _SKILL_DIR.parent.parent / "data" / "tmp" / "chat_files"
# 支持的表格扩展名
_TABLE_EXTS = {".xlsx", ".xlsm"}


# ──────────────────────────────────────────────
# 入口函数辅助逻辑
# ──────────────────────────────────────────────

def _extract_user_question(raw_input: str) -> str:
    """从 Skill 引擎注入的输入中提取真实用户问题。

    引擎可能传入三种格式：
    1. 纯用户问题：``"查询学历为硕士的人员"``
    2. 带 ``"用户问题："`` 标记：``"{文件全文}\\n\\n---\\n\\n用户问题：{问题}"``
    3. 带 ``"【以下是用户上传的文件...】"`` 前缀：
       ``"【以下是用户上传的文件《x》...】\\n\\n{文件全文}\\n\\n---\\n\\n{问题}"``

    提取后统一清除 ``[file_ref: ...]`` 附件标记。
    """
    text = raw_input or ""
    # 格式 2：从最后一个 "用户问题：" 处截取
    idx = text.rfind("用户问题：")
    if idx >= 0:
        question = text[idx + len("用户问题："):].strip()
    # 格式 3：【以下是用户上传的文件...】前缀 → 从 "---" 后截取
    elif text.lstrip().startswith("【") and "---" in text:
        sep_idx = text.rfind("---")
        question = text[sep_idx + 3:].strip()
    else:
        question = text.strip()
    # 清除 [file_ref: ...] 附件标记（前端注入的文件引用标识）
    question = re.sub(r"\[file_ref:\s*[^\]]+\]", "", question).strip()
    return question


def _own_chat_dir() -> Optional[Path]:
    """当前请求用户的对话附件目录。

    优先按身份定位；身份丢失时回退到扫描所有用户子目录中最新 Excel。
    """
    if not _CHAT_FILES_ROOT.exists():
        return None
    uid = ""
    try:
        from storage.skill_output import get_identity, sanitize_user_id
        uid, _ = get_identity()
        names = [n for n in (uid, sanitize_user_id(uid or "")) if n]
    except Exception:
        names = [uid] if uid else []
    for name in names:
        p = _CHAT_FILES_ROOT / name
        if p.is_dir():
            return p
    # 回退：身份丢失时扫描所有用户目录，返回含最新 Excel 的那个
    if not uid:
        best_dir = None
        best_mtime = -1.0
        for sub in _CHAT_FILES_ROOT.iterdir():
            if not sub.is_dir():
                continue
            for ext in _TABLE_EXTS:
                for f in sub.glob(f"*{ext}"):
                    try:
                        mt = f.stat().st_mtime
                        if mt > best_mtime:
                            best_mtime = mt
                            best_dir = sub
                    except Exception:
                        continue
        return best_dir
    return None


def _find_excel_file(query: str) -> Optional[str]:
    """从查询文本或用户附件目录中查找 Excel 底表。

    解析优先级：
    1. 查询中的绝对路径
    2. 查询中的 file_ref 标记
    3. 用户附件目录中最新的 .xlsx/.xlsm 文件
    """
    text = query or ""
    # 1. 绝对路径
    abs_match = re.search(r"(?:/[^\s]+\.xlsx?)", text)
    if abs_match:
        p = Path(abs_match.group())
        if p.is_file():
            return str(p)
    # 2. file_ref 标记
    ref_match = re.search(r"\[file_ref:\s*([^\]]+)\]", text)
    if ref_match:
        ref = ref_match.group(1).strip()
        user_dir = _own_chat_dir()
        if user_dir:
            for ext in _TABLE_EXTS:
                cand = user_dir / (ref + ext)
                if cand.is_file():
                    return str(cand)
            # 按原始文件名匹配 meta
            for meta_f in user_dir.glob("*.meta.json"):
                try:
                    meta = json.loads(meta_f.read_text(encoding="utf-8"))
                    if ref in str(meta.get("filename", "")):
                        orig = meta_f.stem.replace(".meta", "")
                        for ext in _TABLE_EXTS:
                            cand = user_dir / (orig + ext)
                            if cand.is_file():
                                return str(cand)
                except Exception:
                    continue
    # 3. 用户附件目录中最新的 Excel
    user_dir = _own_chat_dir()
    if user_dir:
        candidates = [f for f in user_dir.iterdir()
                      if f.is_file() and f.suffix.lower() in _TABLE_EXTS]
        if candidates:
            candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            return str(candidates[0])
    return None


def _output_dir() -> str:
    """确定输出目录：优先使用 storage 配置的 report root，回退到本地 output/。"""
    try:
        from storage.skill_output import root_dir
        report_root = root_dir("report")
        if report_root:
            uid = ""
            try:
                from storage.skill_output import get_identity
                uid, _ = get_identity()
            except Exception:
                pass
            dest = Path(report_root) / (uid or "default")
            dest.mkdir(parents=True, exist_ok=True)
            return str(dest)
    except Exception:
        pass
    dest = _SKILL_DIR / "output"
    dest.mkdir(parents=True, exist_ok=True)
    return str(dest)


def run_pipeline(query: str) -> str:
    """Skill 引擎统一入口。由 _meta.json 的 entry_function 配置调用。

    流程：提取真实问题 → 定位 Excel 底表 → 执行 run_chatflow → 返回结果文本。
    """
    import logging as _logging
    _log = _logging.getLogger("agent.hr_intelligence")
    from skill_core.engine import run_chatflow

    real_query = _extract_user_question(query)
    # 诊断身份与文件查找
    _uid = ""
    try:
        from storage.skill_output import get_identity as _gi
        _uid, _ = _gi()
    except Exception:
        pass
    _log.warning(f"[hr_intelligence] identity_uid=[{_uid}], raw_len={len(query)}, real_query=[{real_query[:120]}]")
    if not real_query:
        return "⚠️ 未识别到有效查询内容，请重新描述您的需求。"

    excel_file = _find_excel_file(query)
    _log.warning(f"[hr_intelligence] excel_file={excel_file}")
    if not excel_file:
        return (
            "⚠️ 未找到人员信息 Excel 底表。\n\n"
            "请先在对话中上传完整的人员信息底表（.xlsx 或 .xlsm 格式），"
            "然后再发起查询。"
        )

    out_dir = _output_dir()
    try:
        result = run_chatflow(
            file_path=excel_file,
            query=real_query,
            output_dir=out_dir,
        )
    except FileNotFoundError:
        return f"⚠️ 底表文件不存在：{excel_file}"
    except ValueError as e:
        return f"⚠️ 查询参数错误：{e}"
    except Exception as e:
        _log.error(f"[hr_intelligence] run_chatflow error: {e}", exc_info=True)
        return f"⚠️ 执行失败：{e}"

    answer = result.get("answer", "")
    files = result.get("files", [])

    # 把生成的文件复制到输出目录（供下载链接正则匹配）
    output_files = []
    for fpath in files:
        src = Path(fpath)
        if src.is_file():
            dest = Path(out_dir) / src.name
            if src != dest:
                try:
                    shutil.copy2(str(src), str(dest))
                except Exception:
                    pass
            output_files.append(str(dest))

    # 组装返回文本
    parts = []
    if answer:
        parts.append(answer)
    # 包含 raw markdown 数据（cost/hire 路由）
    raw = result.get("raw", {})
    markdown = raw.get("markdown", "")
    if markdown and len(markdown) > len(answer):
        parts.append(markdown[:8000])
    if output_files:
        parts.append("\n\n📎 已生成文件：")
        for f in output_files:
            parts.append(f"  {f}")

    return "\n".join(parts) if parts else "⚠️ 未返回任何结果，请检查查询条件。"


# ──────────────────────────────────────────────
# 工具函数定义
# ──────────────────────────────────────────────

def invoke_hr_assistant(
    excel_file: str,
    query: str,
    conversation_id: str = "",
    output_dir: str = "output",
) -> dict:
    """调用人力智算AI助手，在本地读取 Excel 并执行查询。

    Args:
        excel_file: 用户上传的完整人员信息 Excel 底表绝对路径
        query: 用户自然语言请求（人员查询、预算分析或招聘优化）
        conversation_id: 会话 ID，首次调用留空，追问时传入上次的值

    Returns:
        {
            "answer": str,           # 工作流返回的结果说明
            "conversation_id": str,  # 用于后续追问
            "files": list,           # 本地输出文件绝对路径
        }
    """
    from skill_core.engine import run_chatflow
    result = run_chatflow(
        file_path=excel_file,
        query=query,
        conversation_id=conversation_id,
        output_dir=output_dir,
    )
    return {
        "answer": result["answer"],
        "conversation_id": result["conversation_id"],
        "files": result["files"],
    }


def query_personnel_info(
    excel_file: str,
    query: str,
    output_dir: str = "output",
) -> str:
    """人员信息检索：在底表上筛选、统计后输出结果底表。

    Args:
        excel_file: 用户上传的完整人员信息 Excel 底表绝对路径
        query: 查询请求，如“研发部有多少人”、“列出项目经理名单”

    Returns:
        结果说明文本及输出底表下载链接
    """
    result = invoke_hr_assistant(excel_file, query, output_dir=output_dir)
    return result["answer"]


def generate_cost_budget(
    excel_file: str,
    query: str = "导出人力成本预算",
    output_dir: str = "output",
) -> str:
    """人力成本预算导出：在底表尾部追加成本预算列，输出含预算数据的底表。

    Args:
        excel_file: 用户上传的完整人员信息 Excel 底表绝对路径
        query: 预算相关请求，如“导出年度人力成本预算”

    Returns:
        结果说明文本及输出底表下载链接
    """
    result = invoke_hr_assistant(excel_file, query, output_dir=output_dir)
    if result["files"]:
        return f"预算文件已生成: {', '.join(result['files'])}\n\n{result['answer']}"
    return result["answer"]


def generate_recruitment_plan(
    excel_file: str,
    query: str,
    output_dir: str = "output",
) -> str:
    """招聘优化：在底表基础上生成招聘计划 schedule，输出含招聘方案的底表。

    Args:
        excel_file: 用户上传的完整人员信息 Excel 底表绝对路径
        query: 招聘相关请求，如“研发部需要新增3人，预算结余多少”

    Returns:
        结果说明文本及输出底表下载链接
    """
    result = invoke_hr_assistant(excel_file, query, output_dir=output_dir)
    if result["files"]:
        return f"招聘方案文件已生成: {', '.join(result['files'])}\n\n{result['answer']}"
    return result["answer"]


# ──────────────────────────────────────────────
# 工具注册表（兼容 LangChain @tool 格式）
# ──────────────────────────────────────────────

SKILL_TOOLS = [
    {
        "name": "invoke_hr_assistant",
        "description": "本地运行人力智算AI助手，读取 Excel 底表并执行人员查询、预算或招聘分析",
        "function": invoke_hr_assistant,
    },
    {
        "name": "query_personnel_info",
        "description": "人员信息检索：在底表上筛选、统计后输出结果底表（不得查询敏感字段）",
        "function": query_personnel_info,
    },
    {
        "name": "generate_cost_budget",
        "description": "人力成本预算导出：在底表尾部追加成本预算列，输出含预算数据的底表",
        "function": generate_cost_budget,
    },
    {
        "name": "generate_recruitment_plan",
        "description": "招聘优化：在底表基础上生成招聘计划 schedule，输出含招聘方案的底表",
        "function": generate_recruitment_plan,
    },
]


def get_tool(name: str):
    """按名称查找工具函数。"""
    for tool in SKILL_TOOLS:
        if tool["name"] == name:
            return tool["function"]
    raise KeyError(f"未知工具: {name}")


# ──────────────────────────────────────────────
# LangChain Agent 创建（可选，需安装 langchain）
# ──────────────────────────────────────────────

SYSTEM_PROMPT = """你是一个人力智算AI助手。你可以帮助用户：
1. 人员信息检索：在底表上筛选、统计，输出结果底表
2. 人力成本预算：在底表尾部追加成本预算列，输出含预算数据的底表
3. 招聘优化：在底表基础上生成招聘计划，输出含招聘方案的底表

工作流程：
- 用户必须提供完整人员信息 Excel 底表
- 确认文件路径后，调用 invoke_hr_assistant 在本地执行查询
- 将工作流返回的输出底表文件提供给用户
- 如果返回文件下载链接，一并提供给用户

注意事项：
- 所有数据均来自用户上传的底表，不从任何外部平台拉取数据
- 不得查询身份证件、电话、家庭地址、银行账户、工资等敏感字段
- 招聘建议不得基于性别、年龄、民族或政治面貌
- 不连接任何外部平台，不需要 API Key
- 如果执行失败，报告本地校验或计算错误
"""


def create_skill_agent(llm=None):
    """创建 LangChain ReAct Agent（需要安装 langchain）。

    Args:
        llm: LangChain LLM 实例。如果为 None，尝试从 agents/openai.yaml 创建。

    Returns:
        LangChain AgentExecutor
    """
    try:
        from langchain.agents import create_react_agent, AgentExecutor
        from langchain_core.tools import StructuredTool
    except ImportError:
        raise ImportError(
            "需要安装 langchain: pip install langchain langchain-openai"
        )

    # 将 SKILL_TOOLS 转换为 LangChain StructuredTool
    lc_tools = []
    for tool_def in SKILL_TOOLS:
        func = tool_def["function"]
        lc_tools.append(
            StructuredTool.from_function(
                func=func,
                name=tool_def["name"],
                description=tool_def["description"],
            )
        )

    agent = create_react_agent(llm, lc_tools, prompt=SYSTEM_PROMPT)
    return AgentExecutor(agent=agent, tools=lc_tools, verbose=True)


# ──────────────────────────────────────────────
# CLI 入口
# ──────────────────────────────────────────────

def main():
    """命令行入口：直接调用工具函数。"""
    import argparse

    parser = argparse.ArgumentParser(description="人力智算AI助手 Skill 工具入口")
    sub = parser.add_subparsers(dest="command")

    # invoke
    p_invoke = sub.add_parser("invoke", help="调用人力智算AI助手")
    p_invoke.add_argument("--file", required=True, help="Excel 底表路径")
    p_invoke.add_argument("--query", required=True, help="查询请求")
    p_invoke.add_argument("--conversation-id", default="", help="会话 ID")
    p_invoke.add_argument("--json", action="store_true", help="输出完整 JSON")

    # query
    p_query = sub.add_parser("query", help="人员信息检索")
    p_query.add_argument("--file", required=True, help="Excel 底表路径")
    p_query.add_argument("--query", required=True, help="查询请求")

    # cost
    p_cost = sub.add_parser("cost", help="人力成本预算导出")
    p_cost.add_argument("--file", required=True, help="Excel 底表路径")
    p_cost.add_argument("--query", default="导出人力成本预算", help="预算请求")

    # recruit
    p_recruit = sub.add_parser("recruit", help="招聘优化")
    p_recruit.add_argument("--file", required=True, help="Excel 底表路径")
    p_recruit.add_argument("--query", required=True, help="招聘请求")

    args = parser.parse_args()

    if args.command == "invoke":
        result = invoke_hr_assistant(args.file, args.query, args.conversation_id)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(result["answer"])
            if result.get("conversation_id"):
                print(f"\nconversation_id: {result['conversation_id']}", file=sys.stderr)

    elif args.command == "query":
        print(query_personnel_info(args.file, args.query))

    elif args.command == "cost":
        print(generate_cost_budget(args.file, args.query))

    elif args.command == "recruit":
        print(generate_recruitment_plan(args.file, args.query))

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
