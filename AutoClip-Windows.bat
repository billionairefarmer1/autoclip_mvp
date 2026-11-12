@echo off
REM ===================================================================
REM  AutoClip - one-click launcher for Windows
REM  Double-click this file. It sets up Python dependencies on first run,
REM  then opens AutoClip in your browser.
REM ===================================================================
setlocal enabledelayedexpansion
title AutoClip
cd /d "%~dp0"

echo.
echo   ============================================
echo    AutoClip - short clips from long videos
echo   ============================================
echo.

REM ---- find Python -------------------------------------------------
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo   Python was not found.
  echo.
  echo   Install Python 3.10 or newer from:
  echo     https://www.python.org/downloads/
  echo   IMPORTANT: tick "Add Python to PATH" in the installer.
  echo.
  pause
  exit /b 1
)

REM ---- create/refresh the virtual environment ---------------------
if not exist ".venv\Scripts\python.exe" (
  echo   First run: creating a private Python environment...
  %PY% -m venv .venv
  if errorlevel 1 (
    echo   Could not create the environment. Is Python installed correctly?
    pause
    exit /b 1
  )
)

set "VENV_PY=.venv\Scripts\python.exe"
set "STAMP=.venv\.autoclip_deps"

if not exist "%STAMP%" (
  echo   Installing dependencies ^(one time, needs internet^)...
  "%VENV_PY%" -m pip install --upgrade pip --quiet
  "%VENV_PY%" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo.
    echo   Dependency installation failed. Check your internet connection
    echo   and run this file again.
    pause
    exit /b 1
  )
  echo ok> "%STAMP%"
)

REM ---- optional speech recognition --------------------------------
if not exist ".venv\.autoclip_asr" (
  echo.
  echo   Install automatic transcription ^(recommended, ~120 MB^)?
  echo   Press Y to install, N to skip ^(you can add subtitles later^).
  choice /c YN /n /m "  Install now? [Y/N] "
  if !errorlevel!==1 (
    echo   Installing faster-whisper...
    "%VENV_PY%" -m pip install faster-whisper
    if errorlevel 1 (
      echo   faster-whisper failed to install - AutoClip will use audio-only analysis.
    ) else (
      echo ok> ".venv\.autoclip_asr"
    )
  ) else (
    echo   Skipped. AutoClip still works with audio analysis or a .srt file.
  )
)

echo.
echo   Starting AutoClip...
echo   Your browser will open automatically. Keep this window open while
echo   you work - closing it stops the app.
echo.

"%VENV_PY%" start.py %*

echo.
echo   AutoClip has stopped.
pause
