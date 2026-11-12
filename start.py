#!/usr/bin/env python3
"""
AutoClip - one-click desktop launcher.

Double-click this file (or run ``python start.py``) and AutoClip opens in your
browser as a local app:

  1. checks Python version and finds FFmpeg (pip-bundled build is used if the
     system has none)
  2. builds the frontend once, if Node.js is available and the build is missing
  3. starts the local server on a free port (starting at 8000)
  4. opens the browser at http://localhost:<port>

No internet connection is required after installation, and nothing is uploaded
anywhere: the server only listens on localhost.

Command line options
--------------------
  --port 8000       preferred port (a free one is chosen if it is busy)
  --no-browser      do not open a browser window
  --dev             run the Vite dev server instead of the built UI
  --host            bind address (default 127.0.0.1)
  --reload          auto-reload the server on code changes (development)
"""
from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

MIN_PYTHON = (3, 9)

BANNER = r"""
   ___         __  ______ __ _       _
  / _ | __ __ / /_/ ___// /(_)___  (_)___
 / __ |/ // // __/ /__ / // / __ \/ / __ \
/_/ |_|\_,_/ \__/\___//_//_/ /_/ /_/ .__/
                                 /_/
  AutoClip - turn long videos into short clips, on your own PC
"""


def _check_python() -> None:
    if sys.version_info < MIN_PYTHON:
        sys.exit(
            f"AutoClip needs Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer "
            f"(you have {sys.version.split()[0]}). Get it from https://python.org/downloads"
        )


def _check_dependencies() -> None:
    """Fail early with an actionable message instead of a stack trace."""
    missing = []
    for module, package in (
        ("fastapi", "fastapi"),
        ("uvicorn", "uvicorn"),
        ("numpy", "numpy"),
    ):
        try:
            __import__(module)
        except ImportError:
            missing.append(package)
    if missing:
        print("\n  Missing Python packages: " + ", ".join(missing))
        print("  Install everything with:\n")
        print(f"    {sys.executable} -m pip install -r requirements.txt\n")
        sys.exit(1)


def _ensure_ffmpeg() -> str:
    """Locate FFmpeg, installing the pip-bundled build if needed."""
    try:
        from src.media.ffmpeg import get_ffmpeg

        path = get_ffmpeg()
        return path
    except Exception as exc:  # noqa: BLE001
        print(f"\n  FFmpeg not found ({exc}).")
        print("  Trying to install the bundled build with pip...")
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "--quiet", "imageio-ffmpeg"],
                check=True,
            )
            from src.media.ffmpeg import get_ffmpeg as get_ffmpeg_retry

            return get_ffmpeg_retry()
        except Exception as retry_exc:  # noqa: BLE001
            sys.exit(
                "\n  Could not set up FFmpeg automatically.\n"
                f"  Reason: {retry_exc}\n\n"
                "  Fix: install FFmpeg from https://ffmpeg.org/download.html and make sure\n"
                "  `ffmpeg` runs in your terminal, or run:\n"
                f"    {sys.executable} -m pip install imageio-ffmpeg\n"
            )


def _frontend_built() -> bool:
    return (ROOT / "frontend" / "dist" / "index.html").is_file()


def _maybe_build_frontend(interactive: bool = True) -> None:
    """Build the UI once so the app can be served by FastAPI."""
    if _frontend_built():
        return
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if not npm:
        print(
            "\n  The web interface has not been built yet and Node.js was not found.\n"
            "  Install Node.js 18+ from https://nodejs.org, then run start.py again.\n"
            "  (The API still works without it at /docs, but the app will not load.)\n"
        )
        return

    frontend_dir = ROOT / "frontend"
    if not (frontend_dir / "node_modules").is_dir():
        print("\n  Installing interface dependencies (one time, needs internet)...")
        subprocess.run([npm, "install", "--no-audit", "--no-fund"], cwd=frontend_dir, check=False)

    print("\n  Building the web interface (one time)...")
    result = subprocess.run([npm, "run", "build"], cwd=frontend_dir, check=False)
    if result.returncode != 0:
        print("  Build failed. You can still use the API at http://localhost:8000/docs")
    else:
        print("  Interface ready.")


def _free_port(preferred: int) -> int:
    for candidate in range(preferred, preferred + 40):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", candidate))
                return candidate
            except OSError:
                continue
    # Let the OS decide.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _open_browser_later(url: str, delay: float = 2.0) -> None:
    def _open() -> None:
        time.sleep(delay)
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            print(f"  Open this address in your browser: {url}")

    threading.Thread(target=_open, daemon=True).start()


def _run_dev_frontend(port: int) -> None:
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if not npm:
        sys.exit("Node.js is required for --dev mode")
    subprocess.run([npm, "run", "dev", "--", "--port", "3000", "--host"], cwd=ROOT / "frontend")


def main() -> int:
    parser = argparse.ArgumentParser(description="Start the AutoClip desktop app")
    parser.add_argument("--port", type=int, default=int(os.getenv("AUTOCLIP_PORT", "8000")))
    parser.add_argument("--host", default=os.getenv("AUTOCLIP_HOST", "127.0.0.1"))
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--dev", action="store_true", help="run the Vite dev server too")
    parser.add_argument("--reload", action="store_true", help="auto-reload on code changes")
    parser.add_argument("--rebuild-frontend", action="store_true", help="force a UI rebuild")
    args = parser.parse_args()

    print(BANNER)
    _check_python()
    _check_dependencies()

    ffmpeg_path = _ensure_ffmpeg()
    # Make the discovered binary visible to every subprocess (yt-dlp, etc).
    os.environ.setdefault("AUTOCLIP_FFMPEG", ffmpeg_path)
    print(f"  FFmpeg : {ffmpeg_path}")

    if args.rebuild_frontend:
        shutil.rmtree(ROOT / "frontend" / "dist", ignore_errors=True)
    _maybe_build_frontend()

    # Report what else the machine can do.
    try:
        from src.transcribe.engine import available_backends

        backends = available_backends()
        label = ", ".join(backends) if backends else "none (audio-only analysis)"
        print(f"  Speech : {label}")
        if not backends:
            print("           install faster-whisper for transcript-based clips:")
            print(f"           {sys.executable} -m pip install faster-whisper")
    except Exception:  # noqa: BLE001
        pass

    port = _free_port(args.port)
    url = f"http://localhost:{port}"
    print(f"  App    : {url}")
    print(f"  Data   : {ROOT / 'uploads' / 'studio'}")
    print("\n  Press Ctrl+C to stop AutoClip.\n")

    if not args.no_browser:
        _open_browser_later(url)

    if args.dev:
        _run_dev_frontend(port)

    import uvicorn

    os.chdir(ROOT)
    uvicorn.run(
        "backend_server:app",
        host=args.host,
        port=port,
        reload=args.reload,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n  AutoClip stopped.")
