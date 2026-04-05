flake:
{ config, lib, pkgs, ... }:
with lib;
let
  cfg = config.services.webai-to-api;
  user = "webai";
in {
  options.services.webai-to-api = {
    enable = mkEnableOption "WebAI-to-API local LLM proxy service";
    port = mkOption {
      type = types.port;
      default = 8000;
      description = "Port to bind the API server to.";
    };
    cookieFile = mkOption {
      type = types.path;
      description = ''
        Path to a file containing environment variables like:
        GEMINI_COOKIE=..., CLAUDE_COOKIE=...
        This file must be readable by the 'webai' user.
      '';
    };
    package = mkOption {
      type = types.package;
      default = pkgs.webai-to-api;
      description = "The WebAI-to-API package to use.";
    };
  };

  config = mkIf cfg.enable {
    nixpkgs.overlays = [ flake.overlays.default ];

    systemd.services.webai-to-api = {
      description = "WebAI-to-API — Local proxy for browser-based LLMs";
      after = ["network.target"];
      wantedBy = ["multi-user.target"];
      script = "${cfg.package}/bin/webai-server";
      serviceConfig = {
        Environment = ["PORT=${toString cfg.port}"];
        EnvironmentFile = cfg.cookieFile;
        Restart = "on-failure";
        User = user;
        WorkingDirectory = "/var/lib/webai";
      };
    };

    users.users.${user} = {
      isSystemUser = true;
      group = user;
      home = "/var/lib/webai";
    };
    users.groups.${user} = {};
    systemd.tmpfiles.rules = [
      "d /var/lib/webai 0750 ${user} ${user} -"
    ];
  };
}
