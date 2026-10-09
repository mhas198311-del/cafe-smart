@echo off
cd /d "%~dp0"
echo Starting 26 Cafe Smart Web App (Daphne ASGI Server)...
python\Scripts\daphne.exe -b 0.0.0.0 -p 8000 cafe_smart.asgi:application
pause
