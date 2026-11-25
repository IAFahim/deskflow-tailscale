#!/usr/bin/env bash
set -euo pipefail

HUB_URL="http://127.0.0.1:24808"
IS_HUB=0
NO_SYSTEMD=0
COMPUTER_NAME=""
PACKAGE_SPEC=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --hub-url) HUB_URL="${2:?}"; shift 2 ;;
    --hub) IS_HUB=1; shift ;;
    --no-systemd) NO_SYSTEMD=1; shift ;;
    --computer-name) COMPUTER_NAME="${2:?}"; shift 2 ;;
    --package-spec) PACKAGE_SPEC="${2:?}"; shift 2 ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

SRC_FILE="${BASH_SOURCE[0]:-}"
if [[ -z "$PACKAGE_SPEC" ]]; then
  if [[ -n "$SRC_FILE" ]]; then
    SCRIPT_DIR="$(cd "$(dirname "$SRC_FILE")" && pwd)"
    PARENT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
    if [[ -f "$PARENT_DIR/pyproject.toml" ]]; then
      PACKAGE_SPEC="$PARENT_DIR"
    fi
  fi
  if [[ -z "$PACKAGE_SPEC" ]]; then
    PACKAGE_SPEC="https://github.com/IAFahim/deskflow-tailscale/archive/refs/heads/main.tar.gz"
  fi
fi

CFG_DIR="${HOME}/.config/deskflow-tailscale"
DATA_DIR="${HOME}/.local/share/deskflow-tailscale"
VENV_DIR="$DATA_DIR/venv"
mkdir -p "$CFG_DIR" "$DATA_DIR/run"

if ! command -v tailscale >/dev/null 2>&1; then
  echo "Warning: tailscale not found. Install & log in to Tailscale first."
fi

# 1. Install Deskflow + Python 3 + GTK3 PyGObject if missing
need_pkg=0
command -v deskflow-core >/dev/null 2>&1 || need_pkg=1
command -v python3 >/dev/null 2>&1 || need_pkg=1
python3 -c 'import gi; gi.require_version("Gtk", "3.0")' >/dev/null 2>&1 || need_pkg=1

if [[ "$need_pkg" -eq 1 ]]; then
  if [[ -f /etc/NIXOS ]]; then
    echo "NixOS detected: import nix/deskflow-tailscale.nix in your configuration.nix."
  elif sudo -n true 2>/dev/null || [[ -t 0 ]]; then
    if command -v pacman >/dev/null 2>&1; then
      echo "Installing deskflow + python-gobject + gtk3 via pacman..."
      sudo pacman -S --needed --noconfirm deskflow python python-gobject gtk3 || sudo pacman -S --needed deskflow python python-gobject gtk3
    elif command -v apt-get >/dev/null 2>&1; then
      sudo apt-get update && sudo apt-get install -y deskflow python3 python3-gi gir1.2-gtk-3.0 python3-venv
    elif command -v dnf >/dev/null 2>&1; then
      sudo dnf install -y deskflow python3 python3-gobject gtk3
    else
      echo "Please install deskflow, python3, and GTK3 PyGObject manually." >&2
    fi
  else
    echo "Notice: non-interactive shell without passwordless sudo; skipping system package install." >&2
    echo "Run once in a terminal: sudo pacman -S --needed deskflow" >&2
  fi
fi

# 2. Create isolated venv (inheriting system GTK3/PyGObject) and install deskflow-tailscale + 4 tiny libs
if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  echo "Creating Python venv at $VENV_DIR..."
  python3 -m venv --system-site-packages "$VENV_DIR"
fi
echo "Installing deskflow-tailscale fleet package..."
"$VENV_DIR/bin/python" -m pip install --upgrade --no-cache-dir --quiet "$PACKAGE_SPEC"
PY_BIN="$VENV_DIR/bin/python"

# 3. Write config.json
cat > "$CFG_DIR/config.json" <<EOF
{"hub_url": "$HUB_URL", "computer_name": "$COMPUTER_NAME"}
EOF

start_nohup() {
  local name="$1"; shift
  local pid_file="$DATA_DIR/${name}.pid"
  if [[ -f "$pid_file" ]]; then
    local old_pid
    old_pid="$(cat "$pid_file" 2>/dev/null || true)"
    if [[ -n "$old_pid" ]] && kill -0 "$old_pid" 2>/dev/null; then
      kill "$old_pid" 2>/dev/null || true
    fi
    rm -f "$pid_file"
  fi
  nohup "$@" >"$DATA_DIR/${name}.log" 2>&1 &
  echo $! > "$pid_file"
}

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
if [[ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ]] && [[ -S "${XDG_RUNTIME_DIR}/bus" ]]; then
  export DBUS_SESSION_BUS_ADDRESS="unix:path=${XDG_RUNTIME_DIR}/bus"
fi
if [[ "$NO_SYSTEMD" -eq 0 ]] && command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; then
  UNIT_DIR="${HOME}/.config/systemd/user"
  mkdir -p "$UNIT_DIR"
  systemctl --user import-environment DISPLAY WAYLAND_DISPLAY XDG_RUNTIME_DIR HYPRLAND_INSTANCE_SIGNATURE DBUS_SESSION_BUS_ADDRESS 2>/dev/null || true

  if [[ "$IS_HUB" -eq 1 ]]; then
    cat > "$UNIT_DIR/deskflow-fleet-hub.service" <<EOF
[Unit]
Description=Deskflow-Tailscale Fleet Hub
After=network-online.target

[Service]
ExecStart=$PY_BIN -m deskflow_fleet.hub --host 0.0.0.0 --port 24808
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload
    systemctl --user enable --now deskflow-fleet-hub.service
  fi

  cat > "$UNIT_DIR/deskflow-fleet-agent.service" <<EOF
[Unit]
Description=Deskflow-Tailscale Fleet Agent
After=graphical-session.target network-online.target

[Service]
ExecStart=$PY_BIN -m deskflow_fleet.agent --hub-url $HUB_URL
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
EOF
  systemctl --user daemon-reload
  systemctl --user enable --now deskflow-fleet-agent.service
else
  if [[ "$IS_HUB" -eq 1 ]]; then
    start_nohup hub "$PY_BIN" -m deskflow_fleet.hub --host 0.0.0.0 --port 24808
  fi
  start_nohup agent "$PY_BIN" -m deskflow_fleet.agent --hub-url "$HUB_URL"
fi

echo "Deskflow Fleet installed and running! Hub URL: $HUB_URL"
