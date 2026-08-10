{
  description = "WebAI-to-API: Expose web LLMs (Gemini, Claude, etc.) as local APIs";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = {
    self,
    nixpkgs,
    flake-utils,
  }: let
    systems = flake-utils.lib.eachDefaultSystem (system: let
      pkgs = nixpkgs.legacyPackages.${system};
      python = pkgs.python312;
      g4f = python.pkgs.buildPythonPackage rec {
        pname = "g4f";
        version = "6.8.2";
        pyproject = true;
        src = python.pkgs.fetchPypi {
          inherit pname version;
          sha256 = "0mrd6dni80icgv75lzi1ykmssa4sbb7czqnwcizlc38wsr4rslrb";
        };
        build-system = [ python.pkgs.setuptools ];
        doCheck = false;
        # Only core (non-extra) deps from PyPI metadata
        dependencies = with python.pkgs; [ requests aiohttp brotli pycryptodome nest-asyncio ];
      };

      gemini-webapi = python.pkgs.buildPythonPackage rec {
        pname = "gemini_webapi";
        version = "2.0.0";
        pyproject = true;
        src = python.pkgs.fetchPypi {
          inherit pname version;
          sha256 = "0dcp0g5s1ngyvzkbm14rgq7w7i0xrz4m5ldz1v2zviylkynxisay";
        };
        build-system = with python.pkgs; [ setuptools setuptools-scm ];
        nativeBuildInputs = with python.pkgs; [ pythonRelaxDepsHook ];
        doCheck = false;
        # Core deps from PyPI metadata (browser-cookie3 is an optional extra).
        # Use pythonRelaxDepsHook so the strict ~= version pins in the wheel
        # metadata don't reject the compatible nixpkgs versions of orjson/pydantic.
        pythonRelaxDeps = [ "orjson" "pydantic" "curl-cffi" ];
        dependencies = with python.pkgs; [ httpx loguru orjson pydantic curl-cffi ];
      };

      webai-python = python.withPackages (ps:
        with ps; [
          fastapi
          browser-cookie3
          httpx
          curl-cffi
          uvicorn
          python-multipart
          aiohttp-socks
          nodriver
          platformdirs
          tomli
          click
          pytest
          pytest-asyncio
          pytest-mock
          pytest-cov
          pytest-timeout
          playwright
          jinja2
        ] ++ [ g4f gemini-webapi ]);

      webai-to-api = pkgs.stdenv.mkDerivation {
        pname = "webai-to-api";
        version = "0.4.0";
        src = ./.;

        buildPhase = "true";

        installPhase = ''
          mkdir -p $out/bin $out/src
          cp -r src/* $out/src/
          
          cat > $out/bin/webai-server <<EOF
          #!/usr/bin/env sh
          export PYTHONPATH="$out/src"
          exec ${webai-python}/bin/python $out/src/run.py "\$@"
          EOF
          chmod +x $out/bin/webai-server
        '';
      };
    in {
      packages.default = webai-to-api;

      apps.default = flake-utils.lib.mkApp {
        drv = webai-to-api;
        exePath = "/bin/webai-server";
      };

      devShells.default = pkgs.mkShell {
        packages = [
          webai-python
          pkgs.git
          pkgs.poetry
        ];
        PYTHONPATH = "./src";
        shellHook = ''
          echo "Entering WebAI-to-API dev shell (Python $(${webai-python}/bin/python --version))"
          echo "Development environment ready."
          echo "Run 'pytest' to run tests, or 'python src/run.py' to run the server."
        '';
      };
    });
  in
    systems
    // {
      overlays.default = final: prev: {
        webai-to-api = systems.packages.${prev.system}.default;
      };

      nixosModules.webai-to-api = import ./modules/nixos-module.nix self;
      nixosModules.default = self.nixosModules.webai-to-api;

      homeManagerModules.webai-to-api = import ./modules/home-manager.nix self;
      homeManagerModules.default = self.homeManagerModules.webai-to-api;
    };
}
