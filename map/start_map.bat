@echo off
cd /d "%~dp0"
python serve.py --port 8768 --open
if errorlevel 1 pause
