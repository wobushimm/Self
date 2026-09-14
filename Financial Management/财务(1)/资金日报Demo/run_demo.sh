#!/bin/zsh
set -euo pipefail

DEMO_DIR="$(cd "$(dirname "$0")" && pwd)"
RUNTIME_NODE="${NODE_BIN:-node}"

"$RUNTIME_NODE" "$DEMO_DIR/src/run_demo.mjs"
