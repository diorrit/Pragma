@echo off
cd /d "%~dp0"
start "" pythonw.exe main.py
timeout /t 2 >nul
