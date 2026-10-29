@echo off
cd /d "%~dp0"
title DeepSeek Terminal Assistant
python -u main.py %*
if errorlevel 1 pause
