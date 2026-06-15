@echo off
chcp 65001 >nul
title PostGIS Manager — Installation

echo.
echo  ╔══════════════════════════════════════╗
echo  ║       PostGIS Manager v1.0.0         ║
echo  ║       GIS GEORGIA - pg.qgis.ge       ║
echo  ╚══════════════════════════════════════╝
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python not found.
    echo  Please install Python 3.10+ from https://python.org
    echo  or use the QGIS Plugin version instead.
    pause
    exit /b 1
)

for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PY_VER=%%v
echo  Python %PY_VER% found.
echo.

:: Create virtual environment
if exist .venv (
    echo  Virtual environment already exists. Skipping creation.
) else (
    echo  Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo  [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
    echo  Done.
)

echo.
echo  Installing dependencies (this may take a few minutes)...
echo.

.venv\Scripts\pip.exe install --upgrade pip -q
.venv\Scripts\pip.exe install PyQt6 psycopg2-binary geopandas shapely pyproj fiona

if errorlevel 1 (
    echo.
    echo  [ERROR] Failed to install dependencies.
    pause
    exit /b 1
)

echo.
echo  ════════════════════════════════════════
echo  Installation complete!
echo  Run PostGIS Manager with:  run.bat
echo  ════════════════════════════════════════
echo.
pause
