#!/usr/bin/env python3
"""旧脚本名兼容入口；实际执行完全在本地。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from skill_core.engine import run_chatflow


def main() -> int:
    parser = argparse.ArgumentParser(description="本地运行人力智算AI助手")
    parser.add_argument("--file", required=True)
    parser.add_argument("--query", required=True)
    parser.add_argument("--conversation-id", default="")
    parser.add_argument("--output-dir", default="output")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        result = run_chatflow(
            args.file,
            args.query,
            conversation_id=args.conversation_id,
            output_dir=args.output_dir,
        )
    except Exception as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else result["answer"])
    for file_path in result.get("files", []):
        print(file_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
