@echo off
title Notion ^→ Anki
cd /d "%~dp0"

where python >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo  Python nao encontrado.
    echo  Instale Python 3.10+ em: https://python.org
    echo.
    pause
    exit /b 1
)

python launcher.py
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo  Ocorreu um erro. Verifique as mensagens acima.
    pause
)
