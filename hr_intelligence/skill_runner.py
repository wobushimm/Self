"""人力智算AI助手 — 调度引擎。

负责：
1. 意图解析：从自然语言请求中识别 info / cost / hire 意图
2. 流水线编排：参数校验 → 本地解析 → 本地业务计算 → 输出底表
3. 会话管理：保留 conversation_id 兼容既有调用方
4. 输出管理：处理后的底表文件输出

数据流：用户 Excel 底表 → 本地 Python 处理 → 输出处理后的底表。
不从任何平台 API 拉取数据，所有数据均由用户底表承载。

可作为 skill_tools 的后端调用，也可独立运行。
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

# 确保 Skill 根目录在 sys.path
_SKILL_DIR = Path(__file__).resolve().parent
if str(_SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_DIR))


# ──────────────────────────────────────────────
# 1. 意图解析
# ──────────────────────────────────────────────

# 意图类型常量
INTENT_INFO = "info"
INTENT_COST = "cost"
INTENT_HIRE = "hire"

# 关键词模式
_HIRE_KEYWORDS = re.compile(
    r"(新增人员|增员|招聘优化|招聘计划|待招人数|待招聘|预算结余|招聘人数|人员扩编|"
    r"扩编|招人|招几个人|招多少人|新招|缺人|补人|编制)",
    re.UNICODE,
)

_COST_KEYWORDS = re.compile(
    r"(人力成本|人工成本|成本预算|薪酬总额|年度总薪酬|费用合计|预算汇总|"
    r"成本|薪酬|预算|总费用|花费|支出|开支)",
    re.UNICODE,
)


def parse_intent(query: str) -> dict[str, Any]:
    """从自然语言中解析用户意图。

    支持的意图类型：
    - `hire`：招聘优化（关键词优先）
    - `cost`：人力成本预算
    - `info`：人员信息检索（默认）

    Args:
        query: 用户自然语言请求

    Returns:
        {
            "intent": "info" | "cost" | "hire",
            "query": str,  # 原始查询
            "needs_file": bool,  # 是否需要 Excel 底表
        }
    """
    query = query.strip()

    # 招聘关键词优先于成本关键词
    if _HIRE_KEYWORDS.search(query):
        intent = INTENT_HIRE
    elif _COST_KEYWORDS.search(query):
        intent = INTENT_COST
    else:
        intent = INTENT_INFO

    return {
        "intent": intent,
        "query": query,
        "needs_file": True,  # 所有意图都需要 Excel 底表
    }


# ──────────────────────────────────────────────
# 2. 流水线结果
# ──────────────────────────────────────────────

@dataclass
class PipelineResult:
    """流水线执行结果。"""

    success: bool = False
    intent: str = ""
    query: str = ""
    file_path: str = ""
    answer: str = ""
    conversation_id: str = ""
    files: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "intent": self.intent,
            "query": self.query,
            "file_path": self.file_path,
            "answer": self.answer,
            "conversation_id": self.conversation_id,
            "files": self.files,
            "errors": self.errors,
        }


# ──────────────────────────────────────────────
# 3. 流水线编排
# ──────────────────────────────────────────────

def run_pipeline(
    excel_file: str,
    query: str,
    conversation_id: str = "",
    output_dir: str = "output",
) -> PipelineResult:
    """执行完整流水线：意图解析 → 底表计算 → 三路分流 → 输出文件。

    数据流：用户上传 Excel 底表 → 本地 Python 处理 → 输出处理后的底表。

    Args:
        excel_file: 用户上传的 Excel 底表文件路径
        query: 用户自然语言请求
        conversation_id: 会话 ID（可选，用于追问）
        output_dir: 输出目录

    Returns:
        PipelineResult 对象
    """
    from skill_core.engine import run_chatflow

    result = PipelineResult()
    result.query = query
    result.file_path = excel_file

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # ── Step 1: 意图解析 ──
    print(f"[Pipeline] Step 1/3: 解析意图")
    intent = parse_intent(query)
    result.intent = intent["intent"]
    print(f"  → 意图: {result.intent}")

    # ── Step 2: 参数校验 ──
    print(f"[Pipeline] Step 2/3: 校验参数")
    file_path = Path(excel_file)
    if not file_path.is_file():
        result.errors.append(f"文件不存在: {excel_file}")
        return result

    # ── Step 3: 本地处理 ──
    print(f"[Pipeline] Step 3/3: 本地解析底表并执行业务逻辑")
    try:
        chat_result = run_chatflow(
            file_path=excel_file,
            query=query,
            conversation_id=conversation_id,
            output_dir=output_dir,
        )
        result.answer = chat_result["answer"]
        result.conversation_id = chat_result["conversation_id"]
        result.files = chat_result.get("files", [])
        result.raw = chat_result.get("raw", {})
        result.success = True
    except Exception as e:
        result.errors.append(f"本地处理失败: {e}")
        return result

    # 保存结果到输出目录
    output_file = out / "last_result.json"
    output_file.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"  → 结果已保存: {output_file}")

    return result


# ──────────────────────────────────────────────
# 4. 凭证检查
# ──────────────────────────────────────────────

def check_credentials() -> dict[str, str]:
    """保留旧函数名，返回本地运行状态。

    Returns:
        {"api_key": "configured" | "missing", "base_url": str}
    """
    return {
        "api_key": "not_required",
        "base_url": "local-python",
    }


# ──────────────────────────────────────────────
# 5. CLI 入口
# ──────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(description="人力智算AI助手 — 调度引擎")
    sub = parser.add_subparsers(dest="command")

    # run
    p_run = sub.add_parser("run", help="执行完整流水线")
    p_run.add_argument("--file", required=True, help="Excel 底表路径")
    p_run.add_argument("--query", required=True, help="用户请求")
    p_run.add_argument("--conversation-id", default="", help="会话 ID")
    p_run.add_argument("--output-dir", default="output", help="输出目录")

    # parse（意图解析测试）
    p_parse = sub.add_parser("parse", help="测试意图解析")
    p_parse.add_argument("query", nargs="+", help="自然语言查询")

    # check
    sub.add_parser("check", help="检查凭证配置")

    args = parser.parse_args()

    if args.command == "run":
        result = run_pipeline(
            excel_file=args.file,
            query=args.query,
            conversation_id=args.conversation_id,
            output_dir=args.output_dir,
        )
        print(f"\n{'='*60}")
        print(f"  状态: {'✓ 成功' if result.success else '✗ 失败'}")
        print(f"  意图: {result.intent}")
        print(f"  回答: {result.answer[:200]}..." if len(result.answer) > 200 else f"  回答: {result.answer}")
        if result.conversation_id:
            print(f"  会话: {result.conversation_id}")
        if result.files:
            print(f"  文件: {', '.join(result.files)}")
        if result.errors:
            print(f"  错误:")
            for err in result.errors:
                print(f"    - {err}")

    elif args.command == "parse":
        query = " ".join(args.query)
        intent = parse_intent(query)
        print(f"查询: {query}")
        print(f"意图: {json.dumps(intent, ensure_ascii=False, indent=2)}")

    elif args.command == "check":
        status = check_credentials()
        print(f"API Key: {status['api_key']}")
        print(f"Base URL: {status['base_url']}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
