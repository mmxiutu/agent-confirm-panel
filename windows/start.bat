@echo off
cd /d "%~dp0"
"C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe" -u "%~dp0server.py"
if errorlevel 1 pause
