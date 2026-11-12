#!/bin/bash
# ===================================================================
#  AutoClip - one-click launcher for macOS
#  Double-click this file in Finder. First run installs dependencies.
#  (If macOS blocks it: right-click > Open, or run
#   chmod +x AutoClip-Mac.command in Terminal.)
# ===================================================================
cd "$(dirname "$0")" || exit 1
clear

echo ""
echo "  ============================================"
echo "   AutoClip - short clips from long videos"
echo "  ============================================"
echo ""

PYTHON_BIN=""
if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
else
  echo "  Python 3 was not found."
  echo "  Install it from https://www.python.org/downloads/ and run this again."
  echo ""
  read -n 1 -s -r -p "  Press any key to close..."
  exit 1
fi

if [ ! -x ".venv/bin/python" ]; then
  echo "  First run: creating a private Python environment..."
  "$PYTHON_BIN" -m venv .venv || { echo "  Could not create the environment."; read -n 1 -s -r -p "  Press any key..."; exit 1; }
fi

VENV_PY=".venv/bin/python"
STAMP=".venv/.autoclip_deps"

if [ ! -f "$STAMP" ]; then
  echo "  Installing dependencies (one time, needs internet)..."
  "$VENV_PY" -m pip install --upgrade pip --quiet
  if ! "$VENV_PY" -m pip install -r requirements.txt; then
    echo ""
    echo "  Dependency installation failed. Check your internet connection."
    read -n 1 -s -r -p "  Press any key to close..."
    exit 1
  fi
  touch "$STAMP"
fi

if [ ! -f ".venv/.autoclip_asr" ]; then
  echo ""
  echo "  Install automatic transcription (recommended, ~120 MB)?"
  read -r -p "  Install now? [Y/n] " reply
  case "$reply" in
    [nN]*) echo "  Skipped - AutoClip will use audio analysis or a .srt file." ;;
    *)
      echo "  Installing faster-whisper..."
      if "$VENV_PY" -m pip install faster-whisper; then
        touch ".venv/.autoclip_asr"
      else
        echo "  faster-whisper failed to install - continuing without it."
      fi
      ;;
  esac
fi

echo ""
echo "  Starting AutoClip - your browser will open automatically."
echo "  Keep this window open while you work (Ctrl+C stops the app)."
echo ""

"$VENV_PY" start.py "$@"

echo ""
echo "  AutoClip has stopped."
read -n 1 -s -r -p "  Press any key to close..."
