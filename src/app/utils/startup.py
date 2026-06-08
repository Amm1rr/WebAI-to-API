# src/app/utils/startup.py
import sys
from typing import Tuple

# Import tomli to read pyproject.toml
try:
    import tomli
except ImportError:
    # For Python 3.11+, tomllib is in the standard library
    try:
        import tomllib as tomli
    except ImportError:
        tomli = None


# Helper class for terminal colors
class Colors:
    """A class to hold ANSI color codes for terminal output, dynamically checking TTY support."""

    _use_color = sys.stdout.isatty() if hasattr(sys.stdout, "isatty") else False

    YELLOW = "\033[93m" if _use_color else ""
    GREEN = "\033[92m" if _use_color else ""
    CYAN = "\033[96m" if _use_color else ""
    MAGENTA = "\033[95m" if _use_color else ""
    RED = "\033[91m" if _use_color else ""
    RESET = "\033[0m" if _use_color else ""
    BOLD = "\033[1m" if _use_color else ""


def _read_project_metadata() -> Tuple[str, str]:
    """Read the project name and version from pyproject metadata."""
    if not tomli:
        return "WebAI to API", "N/A (tomli not installed)"

    try:
        with open("pyproject.toml", "rb") as f:
            toml_data = tomli.load(f)

        project_data = toml_data.get("project", {})
        poetry_data = toml_data.get("tool", {}).get("poetry", {})

        name = project_data.get("name") or poetry_data.get("name", "WebAI-to-API")
        version = project_data.get("version") or poetry_data.get("version", "N/A")
        return name.replace("-", " ").title(), version
    except (FileNotFoundError, KeyError, TypeError):
        return "WebAI-to-API", "N/A"


def get_app_info() -> Tuple[str, str]:
    """Reads application name and version from pyproject.toml."""
    return _read_project_metadata()


def print_server_info(host: str, port: int, mode: str, default_model: str | None = None):
    """Displays complete, formatted information about the running server."""
    protocol = "http"
    base_url = f"{protocol}://{host}:{port}"
    app_name, app_version = get_app_info()
    app_info_line = f"{app_name} v{app_version}".center(80)
    print("\n" + "=" * 80)
    print(f"{Colors.BOLD}{Colors.YELLOW}{app_info_line}{Colors.RESET}")
    if mode == "webai":
        print("WebAI-to-API Server is RUNNING".center(80))
        print("=" * 80)
        print("\n✨ Available Services:")
        print(f"  - Dashboard:      {base_url}/ui")
        print(f"  - Docs (Swagger): {base_url}/docs")
        print("\n⚙️ Config.conf:")
        if default_model:
            print(f"  - Default Model: {default_model}")
        else:
            print("  - Could not load config details.")
        print("\n🔗 Primary APIs:")
        print(f"  - POST {base_url}/v1/chat/completions")
        print(f"  - POST {base_url}/translate")
        print(f"  - POST {base_url}/v1beta/models/{{model_path:path}}")
        print("\n🔗 Useful Endpoints:")
        print(f"  - GET  {base_url}/v1/models")
        print(f"  - GET  {base_url}/v1/auth/status")
        print(f"  - POST {base_url}/v1/auth/login")
    print("\n" + "=" * 80)
    instruction_text = "Press Ctrl+C to Quit"
    colored_instructions = (
        f"{Colors.BOLD}{Colors.YELLOW}{instruction_text.center(80)}{Colors.RESET}"
    )
    print(colored_instructions)
    print("=" * 80)


def print_gemini_preflight_status(enabled: bool) -> None:
    """Prints the availability check message during startup preflight."""
    print("INFO:     Checking Gemini service availability...")
    if enabled:
        print(
            f"INFO:     {Colors.GREEN}✅ Gemini service is enabled; starting WebAI-to-API server.{Colors.RESET}"
        )
    else:
        print(
            f"WARN:     {Colors.YELLOW}⚠️ WebAI-to-API mode is not available (Gemini is disabled in config).{Colors.RESET}"
        )
        print(f"\n{Colors.RED}ERROR:    Gemini service is disabled. Exiting.{Colors.RESET}")
