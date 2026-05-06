@echo off
setlocal

cd /d "%~dp0"

where uv >nul 2>nul
if errorlevel 1 (
    echo FlowForge requires uv, but uv was not found on PATH.
    echo Install uv from https://docs.astral.sh/uv/ and try again.
    pause
    exit /b 1
)

echo Starting ComfyUI FlowForge...
uv run flowforge-gui

if errorlevel 1 (
    echo.
    echo FlowForge exited with an error.
    pause
)
