#!/bin/sh
# Install God's Eye View as a user systemd service wired to the Hermes HUD bridge.
#   GODSEYE_HOME (checkout, default ~/gods-eye-view)  GODSEYE_NODE_DIR (default ~/.local/node24)
set -e
GEV="${GODSEYE_HOME:-$HOME/gods-eye-view}"
NODE_DIR="${GODSEYE_NODE_DIR:-$HOME/.local/node24}"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
ENVF="$HERMES_HOME/plugin-data/godseye/hud-bridge.env"
UNIT="$HOME/.config/systemd/user/gods-eye-view.service"
mkdir -p "$(dirname "$UNIT")"
cat > "$UNIT" <<UNIT
[Unit]
Description=God's Eye View (Hermes godseye plugin)
After=network-online.target

[Service]
WorkingDirectory=$GEV
Environment=PATH=$NODE_DIR/bin:/usr/bin:/bin
# Written by the godseye plugin's HUD bridge (0600); sets GEV_HUD_SUMMARY_* so the in-app HUD uses Hermes.
EnvironmentFile=-$ENVF
ExecStart=$NODE_DIR/bin/npm run dev -- --host 127.0.0.1 --port 4173 --strictPort
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
UNIT
systemctl --user daemon-reload
systemctl --user enable --now gods-eye-view.service
loginctl enable-linger "$(id -un)" 2>/dev/null || echo "run: sudo loginctl enable-linger $(id -un)  (survive reboot without login)"
echo "installed $UNIT"
