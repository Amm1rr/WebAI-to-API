@echo off
setlocal
cd /d "%~dp0"

rem Probe with execution-time ERRORLEVEL checks only: percent-expansion of
rem %errorlevel% inside parenthesized blocks is parsed once and goes stale,
rem so this wrapper uses `if (not) errorlevel N goto` between statements.

where py >nul 2>nul
if errorlevel 1 goto fallback

py -3.12 -c "import sys" >nul 2>nul
if not errorlevel 1 goto run312

py -3.11 -c "import sys" >nul 2>nul
if not errorlevel 1 goto run311

:fallback
python scripts\update.py %*
exit /b %errorlevel%

:run312
py -3.12 scripts\update.py %*
exit /b %errorlevel%

:run311
py -3.11 scripts\update.py %*
exit /b %errorlevel%
