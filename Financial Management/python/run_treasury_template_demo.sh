#!/bin/zsh
set -euo pipefail

DEMO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RUNTIME_PY="${PYTHON_BIN:-python3}"
DEFAULT_INPUT="$DEMO_DIR/../测试表/司库交易明细.xlsx"
DEFAULT_TEMPLATE="$DEMO_DIR/../测试表/规则配置.xlsx"
DEFAULT_OUTPUT="$DEMO_DIR/output/资金日报_$(date +%Y%m%d).xlsx"

echo "资金日报本地生成工具"
echo "当天司库文件需要包含：司库交易明细。"
read "INPUT_FILE?请输入当天司库文件路径（直接回车使用默认文件）： "
INPUT_FILE="${INPUT_FILE:-$DEFAULT_INPUT}"
read "TEMPLATE_FILE?请输入模板与规则文件路径（直接回车使用默认文件）： "
TEMPLATE_FILE="${TEMPLATE_FILE:-$DEFAULT_TEMPLATE}"
read "OUTPUT_FILE?请输入输出日报保存路径（直接回车使用默认位置）： "
OUTPUT_FILE="${OUTPUT_FILE:-$DEFAULT_OUTPUT}"
read "OPENING_BALANCE?请输入日报首笔交易前的真实期初余额： "

if [[ -z "$OPENING_BALANCE" ]]; then
  echo "未输入期初余额，已取消生成。"
  exit 1
fi

"$RUNTIME_PY" "$DEMO_DIR/python/generate_treasury_daily_template.py" \
  --input "$INPUT_FILE" \
  --template "$TEMPLATE_FILE" \
  --output "$OUTPUT_FILE" \
  --opening-balance "$OPENING_BALANCE"
