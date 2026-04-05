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
      python = pkgs.python313;
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
        version = "1.21.0";
        pyproject = true;
        src = python.pkgs.fetchPypi {
          inherit pname version;
          sha256 = "0v5p8rm03yaxs8mlfami37ldli2jdzvqsp5kcgv0kcc0afdw4x8b";
        };
        build-system = with python.pkgs; [ setuptools setuptools-scm ];
        nativeBuildInputs = with python.pkgs; [ pythonRelaxDepsHook ];
        doCheck = false;
        # Core deps from PyPI metadata (browser-cookie3 is an optional extra).
        # Use pythonRelaxDepsHook so the strict ~= version pins in the wheel
        # metadata don't reject the compatible nixpkgs versions of orjson/pydantic.
        pythonRelaxDeps = [ "orjson" "pydantic" ];
        dependencies = with python.pkgs; [ httpx loguru orjson pydantic ];
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
        # Don't add Python packages here directly
        packages =
          [
            python
            pkgs.git
          ]
          ++ (with python.pkgs; [
            # Add runtime dependencies needed for development here
            uvicorn
            fastapi # This might pull in problematic dependencies like matplotlib
            requests
            python-dotenv
            pip
            click
            # Add other dependencies as needed
            # python.pkgs.gemini-webapi
          ]); # Just the interpreter
        buildInputs = [
          pkgs.tcl
          pkgs.tk
          pkgs.libtommath # The missing header file
          pkgs.libxcrypt # Often needed for Python builds
          pkgs.tcl # Sometimes the .dev package is needed explicitly
          pkgs.tk.dev
        ];
        # Instead, set PYTHONPATH to include the built dependencies
        PYTHONPATH =
          pkgs.lib.makeLibraryPath (with python.pkgs; [
            fastapi
            uvicorn
            requests
            python-dotenv
            pip
            # Add ALL your project's Python dependencies here
            # python.pkgs.gemini-webapi # This might not exist in nixpkgs
          ])
          + ":${./src}"; # Include your source code
        shellHook = ''
          echo "Entering WebAI-to-API dev shell (Python $(${pkgs.python313}/bin/python --version))"

          # Create a Python virtual environment inside the Nix shell
          VENV_DIR="$TMPDIR/webai_venv_dev"
          echo "Setting up development venv in $VENV_DIR"
          ${python}/bin/python -m venv "$VENV_DIR"

          # Activate the virtual environment
          source "$VENV_DIR/bin/activate"

          # Upgrade pip first, often good practice
          pip install --upgrade pip

          # Install the project and its dependencies (listed in pyproject.toml) into the venv
          # Using --verbose can help diagnose issues if it fails again
          echo "Installing project (editable) and dependencies into venv using pyproject.toml..."
          pip install --verbose -e .

          # Optional: Verify key dependencies are installed after the install
          echo "Checking if key dependencies are installed..."
          python -c "import click; print(f'click {click.__version__} OK')"
          python -c "import uvicorn; print(f'uvicorn {uvicorn.__version__} OK')"
          python -c "import fastapi; print(f'fastapi {fastapi.__version__} OK')"
          python -c "import gemini_webapi; print(f'gemini_webapi OK')" # Check the core dependency

          echo "Development environment ready (VENV: $VENV_DIR)."
          echo "Run 'PYTHONPATH=src python src/run.py' or './bin/webai-server' (if updated)."
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
