flake:
{ config, lib, pkgs, ... }:
with lib;
let
  cfg = config.services.webai-to-api;
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
      '';
    };
    package = mkOption {
      type = types.package;
      default = pkgs.webai-to-api;
      description = "The WebAI-to-API package to use.";
    };
    browser = mkOption {
      type = types.str;
      default = "chrome";
      description = "The browser to extract cookies from (e.g. chrome, firefox, brave, edge).";
    };
  };

  config = mkIf cfg.enable {
    nixpkgs.overlays = [ flake.overlays.default ];

    systemd.user.services.webai-to-api = {
      Unit = {
        Description = "WebAI-to-API — Local proxy for browser-based LLMs";
        After = [ "network.target" ];
      };
      Install = {
        WantedBy = [ "default.target" ];
      };
      Service = {
        ExecStart = "${cfg.package}/bin/webai-server";
        Environment = [
          "PORT=${toString cfg.port}"
          "WEBAI_BROWSER=${cfg.browser}"
          "WEBAI_CONFIG_PATH=%h/.local/share/webai/config.conf"
        ];
        EnvironmentFile = cfg.cookieFile;
        Restart = "on-failure";
        WorkingDirectory = "%h/.local/share/webai";
      };
    };
  };
}
