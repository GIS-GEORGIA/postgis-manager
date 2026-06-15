#!/usr/bin/env bash
set -e

echo ""
echo " ╔══════════════════════════════════════╗"
echo " ║       PostGIS Manager v1.0.0         ║"
echo " ║       GIS GEORGIA - pg.qgis.ge       ║"
echo " ╚══════════════════════════════════════╝"
echo ""

# Check Python 3
if ! command -v python3 &>/dev/null; then
    echo " [ERROR] python3 not found."
    echo " Please install Python 3.10+ via your package manager:"
    echo "   Ubuntu/Debian:  sudo apt install python3 python3-venv python3-pip"
    echo "   Fedora:         sudo dnf install python3"
    echo "   Arch:           sudo pacman -S python"
    echo "   macOS:          brew install python"
    exit 1
fi

PY_VER=$(python3 --version 2>&1)
echo " ${PY_VER} found."
echo ""

# Create virtual environment
if [ -d ".venv" ]; then
    echo " Virtual environment already exists. Skipping creation."
else
    echo " Creating virtual environment..."
    python3 -m venv .venv
    echo " Done."
fi

echo ""
echo " Installing dependencies (this may take a few minutes)..."
echo ""

.venv/bin/pip install --upgrade pip -q
.venv/bin/pip install PyQt6 psycopg2-binary geopandas shapely pyproj fiona

echo ""
echo " ════════════════════════════════════════"
echo " Installation complete!"
echo " Run PostGIS Manager with:  ./run.sh"
echo " ════════════════════════════════════════"
echo ""
