#!/bin/sh
# Regenerate gev/tools_snapshot.json from a God's Eye View checkout.
set -e
HOME_DIR="${GODSEYE_HOME:-$HOME/gods-eye-view}"; NODE="${GODSEYE_NODE:-node}"
OUT="$(cd "$(dirname "$0")/.." && pwd)/gev/tools_snapshot.json"
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"snap","version":"1"}}}' '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
 | "$NODE" "$HOME_DIR/server/mcp/stdio.js" 2>/dev/null \
 | python3 -c "import sys,json
for l in sys.stdin:
  m=json.loads(l)
  if m.get('id')==2: json.dump(m['result']['tools'],open('$OUT','w'),indent=1)"
