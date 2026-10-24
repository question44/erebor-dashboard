{ config, lib, pkgs, ... }:
let
  cfg = config.services.erebor-dashboard;
  settingsFormat = pkgs.formats.json { };
  actionType = lib.types.enum [ "display-on" "display-off" "ambilight-on" "ambilight-off"
    "sleep" "power-off" "reboot-windows" ];
in
{
  options.services.erebor-dashboard = {
    enable = lib.mkEnableOption "the Erebor dashboard package";
    package = lib.mkOption {
      type = lib.types.package;
      default = pkgs.callPackage ./package.nix { settings = cfg.settings; };
      description = "Built static dashboard; serve this directory as the website root.";
    };
    settings = lib.mkOption {
      type = settingsFormat.type;
      default = { };
      description = "Public frontend configuration baked into the package. Do not put secrets here.";
    };
    control = {
      enable = lib.mkEnableOption "the loopback dashboard control API";
      package = lib.mkOption {
        type = lib.types.package;
        default = pkgs.callPackage ./control-package.nix { };
        description = "Dashboard control API package.";
      };
      user = lib.mkOption {
        type = lib.types.str;
        description = "Desktop user whose user manager runs the API.";
      };
      allowedHosts = lib.mkOption {
        type = lib.types.listOf lib.types.str;
        default = [ "localhost" "127.0.0.1" ];
        description = "Hostnames accepted by the same-origin request boundary. Private and tailnet IPs are also accepted.";
      };
      enabledActions = lib.mkOption {
        type = lib.types.listOf actionType;
        default = [ ];
        description = "Actions the API may execute. Windows reboot needs a separately supplied privileged helper.";
      };
      wledUrl = lib.mkOption {
        type = lib.types.nullOr lib.types.str;
        default = null;
        description = "WLED HTTP URL; HyperHDR is expected on loopback port 19444.";
      };
      extraPackages = lib.mkOption {
        type = lib.types.listOf lib.types.package;
        default = [ ];
        description = "Compositor command packages used by display actions.";
      };
    };
  };
  config = lib.mkIf cfg.enable {
    assertions = [{
      assertion = !cfg.control.enable || cfg.control.user != "";
      message = "services.erebor-dashboard.control.user must name a desktop user.";
    }];
    systemd.user.services.dashboard-control = lib.mkIf cfg.control.enable {
      description = "Local dashboard controls";
      wantedBy = [ "default.target" ];
      unitConfig.ConditionUser = cfg.control.user;
      environment = {
        DASHBOARD_HOSTS = lib.concatStringsSep "," cfg.control.allowedHosts;
        DASHBOARD_ACTIONS = lib.concatStringsSep "," cfg.control.enabledActions;
      } // lib.optionalAttrs (cfg.control.wledUrl != null) {
        DASHBOARD_WLED_URL = cfg.control.wledUrl;
      };
      path = [ pkgs.systemd pkgs.wlopm ] ++ cfg.control.extraPackages;
      serviceConfig = {
        ExecStart = lib.getExe cfg.control.package;
        Restart = "on-failure";
        NoNewPrivileges = true;
        PrivateTmp = true;
        ProtectSystem = "strict";
        ProtectHome = "read-only";
        RestrictAddressFamilies = [ "AF_UNIX" "AF_INET" ];
        LockPersonality = true;
        UMask = "0077";
      };
    };
  };
}
