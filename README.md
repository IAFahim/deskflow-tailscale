# deskflow-tailscale

Thin orchestrator for a **zero-bloat Deskflow + Tailscale KVM Fleet** with single-photo multi-QR spatial desk scanning, live drag-and-drop 2D layout canvas, and 1-click Master switching & presets.

## Architecture

Each concern lives in its own focused, zero-third-party-pip-dependency library, composed here:

- **[`tinyqr`](https://github.com/IAFahim/tinyqr)** — Pure-Python ISO/IEC 18004 QR code encoder (SVG, PNG, ASCII)
- **[`qr-overlay`](https://github.com/IAFahim/qr-overlay)** — Lightweight cross-platform borderless topmost QR overlay window (`Tkinter`, `GTK3`, headless SVG)
- **[`desk-layout`](https://github.com/IAFahim/desk-layout)** — 2D multi-monitor spatial layout solver & Deskflow v1.27+ `Deskflow.conf` / `deskflow-server.conf` compiler
- **[`tailscale-host`](https://github.com/IAFahim/tailscale-host)** — Cross-platform Tailscale IP/hostname, OS, active monitor geometry, and Deskflow binary locator
- **Fleet Hub (`deskflow_fleet.hub`)** — Stdlib Python HTTP server + SQLite (`:24808`) serving the mobile-friendly Web UI and coordinating per-PC Deskflow configs over Tailscale.
- **Fleet Agent (`deskflow_fleet.agent`)** — Cross-platform daemon (`Windows` + `Linux X11/Wayland/Hyprland/NixOS`) reporting monitor/Tailscale state, spawning `qr-overlay` during pairing, and managing `deskflow-core` (`server` or `client`, with `tlsEnabled=false` bound to your Tailscale IP).
- **Pair CLI (`deskflow_fleet.pair_cli`)** — Triggers pairing mode and prints an ASCII QR code for the Web UI right in your terminal.
- **NixOS Module (`nix/deskflow-tailscale.nix`)** — Declarative NixOS configuration for `deskflow`, `xdg.portal`, Tailscale firewall ports, and `systemd.user.services.deskflow-fleet-agent`.

## Quick Setup

### 1. Run Hub + Agent on your primary Windows or Linux PC

Windows (PowerShell — zero git needed):
```powershell
iwr -UseBasicParsing https://raw.githubusercontent.com/IAFahim/deskflow-tailscale/main/scripts/install-windows.ps1 -OutFile $env:TEMP\df-install.ps1
powershell -ExecutionPolicy Bypass -File $env:TEMP\df-install.ps1 -Hub -HubUrl "http://127.0.0.1:24808"
```

Linux:
```bash
curl -fsSL https://raw.githubusercontent.com/IAFahim/deskflow-tailscale/main/scripts/install-linux.sh | bash -s -- --hub --hub-url "http://127.0.0.1:24808"
```

### 2. Join any extra PC (pointing to Hub's Tailscale IP)

Windows (PowerShell):
```powershell
powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1 -HubUrl "http://<HUB-TAILSCALE-IP>:24808"
```

Linux (Arch / Omarchy / Ubuntu / Fedora / NixOS):
```bash
curl -fsSL https://raw.githubusercontent.com/IAFahim/deskflow-tailscale/main/scripts/install-linux.sh | bash -s -- --hub-url "http://<HUB-TAILSCALE-IP>:24808"
```

## Pairing Flow

1. Run `deskflow-pair` (or tap **Pair / Show QRs** in the Web UI at `http://<HUB-TAILSCALE-IP>:24808`).
2. Each online PC shows a crisp `P1`, `P2`, ... QR code on screen.
3. Open the Web UI on your phone, tap **Snap / Upload Desk Photo**, take **one photo** showing your screens, and it automatically computes the spatial layout, assigns Master, connects all PCs, and dismisses the overlays.
4. Drag screens anytime on the 2D canvas, click **Set Master** on any PC to switch which keyboard/mouse controls the desk, or save presets (e.g. `4-PC Battlestation`, `2-PC Travel Desk`).

## Run Tests

```bash
python -m unittest discover -s tests -v
```
