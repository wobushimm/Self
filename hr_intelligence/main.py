

"""人力智算AI助手 Skill 入口
========================
支持多种调用方式：

1. 流水线模式（推荐）:
   python main.py run --file '/path/人员底表.xlsx' --query '研发部有多少人'

2. 工具模式（按场景调用）:
   python main.py query --file '/path/人员底表.xlsx' --query '列出项目经理名单'
   python main.py cost --file '/path/人员底表.xlsx'
   python main.py recruit --file '/path/人员底表.xlsx' --query '研发部新增3人'

3. 意图解析测试:
   python main.py parse '研发部有多少人'
   python main.py parse '导出人力成本预算'
   python main.py parse '招聘5个开发人员'

4. 环境检查:
   python main.py check
"""

import sys
import json
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# 确保 Skill 根目录在 sys.path
_SKILL_DIR = Path(__file__).resolve().parent
if str(_SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_DIR))


def run_pipeline_mode(args):
    """流水线模式：指定文件 + 请求，一键执行。"""
    from skill_runner import run_pipeline

    result = run_pipeline(
        excel_file=args.file,
        query=args.query,
        conversation_id=args.conversation_id,
        output_dir=args.output_dir,
    )

    print(f"\n{'='*60}")
    print(f"  状态: {'✓ 成功' if result.success else '✗ 失败'}")
    print(f"  意图: {result.intent}")
    if result.answer:
        if len(result.answer) > 500:
            print(f"  回答: {result.answer[:500]}...")
        else:
            print(f"  回答: {result.answer}")
    if result.conversation_id:
        print(f"  会话: {result.conversation_id}")
    if result.files:
        print(f"  文件: {', '.join(result.files)}")
    if result.errors:
        print(f"  错误:")
        for err in result.errors:
            print(f"    - {err}")

    return result


def parse_intent_mode(args):
    """意图解析测试模式。"""
    from skill_runner import parse_intent

    query = " ".join(args.query)
    intent = parse_intent(query)
    print(f"查询: {query}")
    print(f"意图: {json.dumps(intent, ensure_ascii=False, indent=2)}")


def check_mode(args):
    """环境检查模式。"""
    from skill_runner import check_credentials

    status = check_credentials()
    print(f"{'='*40}")
    print(f"  人力智算AI助手 — 环境检查")
    print(f"{'='*40}")
    print("  运行模式: ✓ 本地 Python")
    print("  外部平台/API Key: 不需要")
    print(f"  引擎: {status['base_url']}")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="人力智算AI助手 Skill")
    sub = parser.add_subparsers(dest="command")

    # run（流水线模式）
    p_run = sub.add_parser("run", help="执行完整本地流水线")
    p_run.add_argument("--file", required=True, help="Excel 底表路径")
    p_run.add_argument("--query", required=True, help="用户请求")
    p_run.add_argument("--conversation-id", default="", help="会话 ID（追问时使用）")
    p_run.add_argument("--output-dir", default="output", help="输出目录")

    # query（人员信息检索）
    p_query = sub.add_parser("query", help="人员信息检索")
    p_query.add_argument("--file", required=True, help="Excel 底表路径")
    p_query.add_argument("--query", required=True, help="查询请求")
    p_query.add_argument("--output-dir", default="output", help="输出目录")

    # cost（人力成本预算）
    p_cost = sub.add_parser("cost", help="人力成本预算导出")
    p_cost.add_argument("--file", required=True, help="Excel 底表路径")
    p_cost.add_argument("--query", default="导出人力成本预算", help="预算请求")
    p_cost.add_argument("--output-dir", default="output", help="输出目录")

    # recruit（招聘优化）
    p_recruit = sub.add_parser("recruit", help="招聘优化")
    p_recruit.add_argument("--file", required=True, help="Excel 底表路径")
    p_recruit.add_argument("--query", required=True, help="招聘请求")
    p_recruit.add_argument("--output-dir", default="output", help="输出目录")

    # parse（意图解析测试）
    p_parse = sub.add_parser("parse", help="测试意图解析")
    p_parse.add_argument("query", nargs="+", help="自然语言查询")

    # check（环境检查）
    sub.add_parser("check", help="检查环境配置")

    args = parser.parse_args()

    if args.command == "run":
        run_pipeline_mode(args)
    elif args.command == "query":
        from skill_tools import query_personnel_info
        print(query_personnel_info(args.file, args.query, args.output_dir))
    elif args.command == "cost":
        from skill_tools import generate_cost_budget
        print(generate_cost_budget(args.file, args.query, args.output_dir))
    elif args.command == "recruit":
        from skill_tools import generate_recruitment_plan
        print(generate_recruitment_plan(args.file, args.query, args.output_dir))
    elif args.command == "parse":
        parse_intent_mode(args)
    elif args.command == "check":
        check_mode(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
