#!/bin/bash
set -euo pipefail

uv run python -m mcp_controller --port 8088 --host 0.0.0.0 "$@"
