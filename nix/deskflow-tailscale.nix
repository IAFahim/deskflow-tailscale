{ config, pkgs, lib, ... }:
let
  cfg = config.services.deskflowFleet;
in
{
  options.services.deskflowFleet = {
    enable = lib.mkEnableOption "Deskflow-Tailscale Fleet Agent";
    hubUrl = lib.mkOption {
      type = lib.types.str;
      default = "http://127.0.0.1:24808";
      description = "URL of the Deskflow Fleet Hub over Tailscale.";
    };
    repoDir = lib.mkOption {
      type = lib.types.str;
      default = ".deskflow-tailscale";
      description = "Relative path inside $HOME where deskflow-tailscale is cloned.";
    };
    deskflowPort = lib.mkOption {
      type = lib.types.port;
      default = 24800;
    };
    useLanMouse = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = "Set true if running a minimal wlroots compositor without XDG InputCapture portals.";
    };
  };

  config = lib.mkIf cfg.enable {
    services.tailscale.enable = true;

    environment.systemPackages = with pkgs; [
      (if cfg.useLanMouse then lan-mouse else deskflow)
      python3
      gtk3
      python3Packages.pygobject3
    ];

    xdg.portal = {
      enable = true;
      extraPortals = with pkgs; [
        xdg-desktop-portal-gtk
        xdg-desktop-portal-hyprland
      ];
    };

    # Trust Deskflow only on the encrypted Tailscale WireGuard interface.
    networking.firewall.interfaces.tailscale0 = {
      allowedTCPPorts = [ cfg.deskflowPort 24808 ];
      allowedUDPPorts = [ cfg.deskflowPort 4242 ];
    };

    systemd.user.services.deskflow-fleet-agent = {
      description = "Deskflow-Tailscale Fleet Agent";
      wantedBy = [ "graphical-session.target" ];
      after = [ "graphical-session.target" "network-online.target" ];
      serviceConfig = {
        ExecStart = "${pkgs.python3}/bin/python3 %h/${cfg.repoDir}/src/agent.py --hub-url ${cfg.hubUrl}";
        Restart = "always";
        RestartSec = 3;
      };
    };
  };
}
