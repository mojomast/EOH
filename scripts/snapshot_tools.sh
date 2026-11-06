#!/bin/sh
# Regenerate the three catalogs from a God's Eye View checkout.
#
#   GODSEYE_HOME=/path/to/gods-eye-view scripts/snapshot_tools.sh
#
# tools_snapshot.json is the MCP tools/list catalog. live_tools_snapshot.json is
# the app's realtime action catalog (src/voice/actionSchemas.js, described by
# server/providers/openai/toolDescriptions.js). feature_snapshot.json records
# which shipped feature families the checkout actually carries.
set -e
HOME_DIR="${GODSEYE_HOME:-$HOME/gods-eye-view}"; NODE="${GODSEYE_NODE:-node}"
OUT_DIR="$(cd "$(dirname "$0")/.." && pwd)/gev"
TOOLS_OUT="$OUT_DIR/tools_snapshot.json"
LIVE_OUT="$OUT_DIR/live_tools_snapshot.json"
FEATURE_OUT="$OUT_DIR/feature_snapshot.json"
LIVE_EXCLUDE="${GEV_LIVE_EXCLUDE:-set_voice_inactivity_timeout}"

printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"snap","version":"1"}}}' '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
 | "$NODE" "$HOME_DIR/server/mcp/stdio.js" 2>/dev/null \
 | python3 -c "import sys,json
for l in sys.stdin:
  m=json.loads(l)
  if m.get('id')==2: json.dump(m['result']['tools'],open('$TOOLS_OUT','w'),indent=1)"

# The realtime catalog also carries session preferences. They are not globe
# actions, and registering one would add a twelfth model tool.
(cd "$HOME_DIR" && GEV_LIVE_EXCLUDE="$LIVE_EXCLUDE" "$NODE" --input-type=module -e '
import { GEV_REALTIME_TOOLS } from "./server/providers/openai/tools.js";
const exclude = new Set((process.env.GEV_LIVE_EXCLUDE || "").split(",").filter(Boolean));
const tools = GEV_REALTIME_TOOLS.filter((tool) => !exclude.has(tool.name));
process.stdout.write(JSON.stringify(tools));
') | python3 -c "import sys,json; json.dump(json.load(sys.stdin),open('$LIVE_OUT','w'),indent=1)"

python3 - "$HOME_DIR" "$FEATURE_OUT" <<'PY'
import datetime as dt
import json
import pathlib
import subprocess
import sys

home = pathlib.Path(sys.argv[1]).resolve()
out = pathlib.Path(sys.argv[2])
text_suffixes = {".js", ".mjs", ".ts", ".json", ".md"}

files = []
for root_name in ("src", "server"):
    root = home / root_name
    if not root.is_dir():
        continue
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix not in text_suffixes:
            continue
        if ".test." in path.name or ".spec." in path.name:
            continue
        try:
            files.append((path.relative_to(home).as_posix(), path.read_text(errors="ignore")))
        except OSError:
            continue

def first_path(token):
    for relative, text in files:
        if token in text:
            return relative
    return None

specs = {
    "recent_imagery": {
        "paths": ["src/voice/imageryActions.js", "src/layers/recentImagery"],
        "tokens": ["find_imagery", "recent-imagery"],
    },
    "weather_stack": {
        "paths": ["src/layers/wind", "src/layers/weather", "src/layers/cyclones"],
        "tokens": ["weather-radar", "weather-satellite", "weather-cyclones", "esri-imagery"],
    },
    "transit": {
        "paths": ["src/data/gtfsRealtime.js", "src/data/transitFeeds.js"],
        "tokens": ["GTFS-Realtime", "smtr-rio-onibus", "dados.mobilidade.rio"],
    },
    "fire_perimeters": {
        "paths": ["server/providers/firePerimeters.js", "src/layers/perimeters"],
        "tokens": ["WFIGS", "get_fire_perimeters"],
    },
    "local_adsb": {
        "paths": ["src/layers/localAdsb"],
        "tokens": ["local-adsb"],
    },
    "director": {
        "paths": ["src/director/playback.js", "src/director/packs/manifest.js"],
        "tokens": ["Director"],
    },
    "cyber_hud": {
        "paths": ["src/cyberSonarScene.js", "src/hud.js"],
        "tokens": ["cyber-sonar"],
    },
}

features = {}
for name, spec in specs.items():
    evidence = []
    present = True
    for relative in spec["paths"]:
        if (home / relative).exists():
            evidence.append(relative)
        else:
            present = False
    for token in spec["tokens"]:
        relative = first_path(token)
        if relative is None:
            present = False
            continue
        evidence.append(token)
        if relative not in evidence:
            evidence.append(relative)
    features[name] = {"present": present, "evidence": evidence}

try:
    version = json.loads((home / "package.json").read_text()).get("version", "")
except (OSError, ValueError):
    version = ""
rev = subprocess.run(["git", "-C", str(home), "rev-parse", "HEAD"], capture_output=True,
                     text=True, check=False).stdout.strip()
payload = {
    "generated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    "gev_rev": rev,
    "gev_version": version,
    "features": features,
}
json.dump(payload, open(out, "w"), indent=1)
PY
