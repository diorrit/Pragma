@echo off
taskkill /F /FI "IMAGENAME eq pythonw.exe" 2>nul
taskkill /F /FI "IMAGENAME eq python.exe" 2>nul
echo Wrata зупинена.
timeout /t 2 >nul
