@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0.venv\Scripts\python.exe" goto venv
where py >nul 2>nul
if not errorlevel 1 goto launcher
where python >nul 2>nul
if not errorlevel 1 goto system
 echo Python 3.11 or later was not found. Please install Python and try again.
pause
exit /b 2
:venv
"%~dp0.venv\Scripts\python.exe" -B birthday_query.py --help
goto finish
:launcher
py -3 -B birthday_query.py --help
goto finish
:system
python -B birthday_query.py --help
:finish
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" echo Could not show CLI help. Check the Python installation.
pause
exit /b %EXIT_CODE%
