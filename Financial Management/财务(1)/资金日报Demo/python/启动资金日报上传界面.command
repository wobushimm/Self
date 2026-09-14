#!/bin/zsh
set -euo pipefail

DEMO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
if ! command -v python3 >/dev/null 2>&1; then
  echo "未找到 Python 3。请先安装 Python 3，再双击本文件。"
  read "?按回车键关闭窗口..."
  exit 1
fi

if ! python3 -c "import openpyxl" >/dev/null 2>&1; then
  echo "缺少 openpyxl。请在终端执行：python3 -m pip install -r requirements.txt"
  read "?按回车键关闭窗口..."
  exit 1
fi

python3 "$DEMO_DIR/python/资金日报上传生成界面.py"
