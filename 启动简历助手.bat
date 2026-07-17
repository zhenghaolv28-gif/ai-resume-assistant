@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Python environment not found. Please finish the setup first.
    pause
    exit /b 1
)

if not defined AI_RESUME_NO_BROWSER (
    start "" /b powershell.exe -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 2; Start-Process 'http://127.0.0.1:8501'"
)

".venv\Scripts\python.exe" -m streamlit run app.py --server.headless true --server.address 127.0.0.1 --server.port 8501 --browser.gatherUsageStats false

if errorlevel 1 (
    echo.
    echo The app did not start successfully.
    pause
)
