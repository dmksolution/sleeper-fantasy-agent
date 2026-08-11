@echo off
REM Double-click to open the fantasy dashboard.
REM Uses the project virtualenv if it exists, otherwise whatever python is on PATH.

cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
  set "PY=.venv\Scripts\python.exe"
) else (
  set "PY=python"
)

echo Starting the Sleeper Fantasy Agent dashboard...
echo Close this window to stop the server.
echo.

"%PY%" -m webapp.server

REM Keep the window open if it exited because of an error.
if errorlevel 1 (
  echo.
  echo The server exited with an error. Common causes:
  echo   * dependencies missing    ^-^-^>  pip install -r requirements.txt
  echo   * SLEEPER_LEAGUE_ID unset ^-^-^>  python cli.py setup --username YOURNAME
  echo   * port 8770 already in use ^-^-^> python cli.py web --port 8781
  echo.
  pause
)
