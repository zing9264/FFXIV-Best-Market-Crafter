#!/usr/bin/env bash
cd "$(dirname "$0")"
source .venv-wsl/bin/activate
FF14_APP_HOST=0.0.0.0 python web_ui.py
