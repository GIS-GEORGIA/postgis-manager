@echo off
chcp 65001 >nul
title PostGIS Manager

:: Check if installed
if not exist .venv\Scripts\python.exe (
    echo  PostGIS Manager is not installed yet.
    echo  Please run install.bat first.
    echo.
    pause
    exit /b 1
)

:: Launch
.venv\Scripts\python.exe run.py
