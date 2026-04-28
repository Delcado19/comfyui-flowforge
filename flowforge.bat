@echo off
cd /d "%~dp0"
uv run python flowforge.py %*
