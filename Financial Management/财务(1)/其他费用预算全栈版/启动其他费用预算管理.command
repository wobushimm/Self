#!/bin/zsh
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
RUNTIME_PYTHON="${PYTHON_BIN:-python3}"

"$RUNTIME_PYTHON" "$APP_DIR/python/其他费用预算管理界面.py"
