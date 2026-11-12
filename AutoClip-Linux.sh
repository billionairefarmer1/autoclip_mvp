#!/bin/bash
# ===================================================================
#  AutoClip - one-click launcher for Linux
#  Run: ./AutoClip-Linux.sh
# ===================================================================
cd "$(dirname "$0")" || exit 1

echo ""
echo "  ============================================"
echo "   AutoClip - short clips from long videos"
echo "  ============================================"
echo ""

if ! command -v python3 >/dev/null 2>&1; then
  echo "  Python 3 was not found. Install it with your package manager, e.g.:"
  echo "    sudo apt install python3 python3-venv"
  exit 1
fi

if [ ! -x ".venv/bin/python" ]; then
  echo "  First run: creating a private Python environment..."
  if ! python3 -m venv .venv; then
    echo "  Could not create the environment."
    echo "  On Debian/Ubuntu you may need: sudo apt install python3-venv"
    exit 1
  fi
fi

VENV_PY=".venv/bin/python"
STAMP=".venv/.autoclip_deps"

if [ ! -f "$STAMP" ]; then
  echo "  Installing dependencies (one time, needs internet)..."
  "$VENV_PY" -m pip install --upgrade pip --quiet
  "$VENV_PY" -m pip install -r requirements.txt || exit 1
  touch "$STAMP"
fi

if [ ! -f ".venv/.autoclip_asr" ]; then
  echo ""
  read -r -p "  Install automatic transcription (recommended, ~120 MB)? [Y/n] " reply
  case "$reply" in
    [nN]*) echo "  Skipped - AutoClip will use audio analysis or a .srt file." ;;
    *)
      "$VENV_PY" -m pip install faster-whisper && touch ".venv/.autoclip_asr"
      ;;
  esac
fi

echo ""
echo "  Starting AutoClip - open the printed address if the browser does not."
echo "  Press Ctrl+C to stop."
echo ""

exec "$VENV_PY" start.py "$@"
