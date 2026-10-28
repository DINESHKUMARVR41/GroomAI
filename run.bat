@echo off
cd /d "%~dp0"
if not exist .venv (
  echo Creating Python virtual environment...
  py -3 -m venv .venv
  if errorlevel 1 python -m venv .venv
)
call .venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
if not exist .env copy .env.example .env
 echo.
echo GroomAI is starting at http://127.0.0.1:8000
 echo If you have not configured the API key, edit .env and restart this window.
python -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
pause
