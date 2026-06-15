#!/usr/bin/env bash
set -e

if [ ! -f ".venv/bin/python" ]; then
    echo " PostGIS Manager is not installed yet."
    echo " Please run ./install.sh first."
    exit 1
fi

exec .venv/bin/python run.py
