@echo off
cd /d "%~dp0"
set DJANGO_DEBUG=1
set PYTHONPATH=.
echo Starting 26 Cafe Smart Web App (Daphne ASGI Server)...
python\Scripts\daphne.exe -b 0.0.0.0 -p 8000 asgi:application
pause
