@echo off
cd /d "%~dp0"
start "" pythonw.exe screen_tg.py
timeout /t 2 >nul
