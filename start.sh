#!/usr/bin/env bash
# One-click launcher: sets up Python the first time, then opens the bot's web page.
# Works on Linux/macOS and on Windows in Git Bash (for a plain Windows window, use start.bat).
set -e
cd "$(dirname "$0")"
export PYTHONUTF8=1

# Supported: 64-bit Python 3.11-3.14. Polymarket's SDK needs 3.11+, and one of its dependencies (ckzg) ships
# prebuilt packages only up to 3.14; on newer or 32-bit Python pip tries to compile it and fails.
OKPY='import sys, struct; sys.exit(0 if (3, 11) <= sys.version_info[:2] <= (3, 14) and struct.calcsize("P") == 8 else 1)'

# Find a supported Python 3 (on Windows "python3" is often missing or a Microsoft Store stub;
# "py -3.X" picks a specific version when several are installed)
PY=""
for cand in "py -3.13" "py -3.12" "py -3.14" "py -3.11" "py -3.10" python3 python "py -3"; do
  if $cand -c "$OKPY" >/dev/null 2>&1; then
    PY="$cand"; break
  fi
done
if [ -z "$PY" ]; then
  echo "No supported Python found (need 64-bit Python 3.11 to 3.14)."
  echo "Install Python 3.13 from https://www.python.org/downloads/ (on Windows tick 'Add python.exe to PATH'),"
  echo "then open a NEW terminal and run ./start.sh again."
  exit 1
fi

# venv layout differs: Linux/macOS .venv/bin/python, Windows .venv/Scripts/python.exe
venv_python() {
  if [ -x .venv/bin/python ]; then echo .venv/bin/python
  elif [ -x .venv/Scripts/python.exe ]; then echo .venv/Scripts/python.exe
  fi
}

VPY="$(venv_python)"
# rebuild an environment made with an unsupported Python
if [ -n "$VPY" ] && ! "$VPY" -c "$OKPY" >/dev/null 2>&1; then
  echo "The Python environment was made with an unsupported Python; rebuilding it with $PY ..."
  VPY=""
fi
if [ -z "$VPY" ]; then
  echo "First run: setting up Python environment with $PY ..."
  rm -rf .venv
  $PY -m venv .venv 2>/dev/null || {
    # Debian/Ubuntu without python3-venv: create without pip, then bootstrap pip
    $PY -m venv --without-pip .venv
    curl -sS https://bootstrap.pypa.io/get-pip.py | "$(venv_python)"
  }
  VPY="$(venv_python)"
fi

# (re)install requirements when missing or when requirements.txt changed
if [ ! -f .venv/.installed ] || [ requirements.txt -nt .venv/.installed ]; then
  "$VPY" -m pip install -q --disable-pip-version-check -r requirements.txt
  touch .venv/.installed
fi

exec "$VPY" -m poly_bot web "$@"
