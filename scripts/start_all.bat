@echo off
REM ============================================================
REM  start_all.bat - Ein-Klick-Start: OPC UA Server + Jupyter Lab
REM  Startet den Server in einem eigenen Fenster und oeffnet danach
REM  Jupyter Lab direkt mit dem Simulations-Notebook. Anschliessend
REM  nur noch im Browser "Run All" ausfuehren.
REM  Liegt in scripts\ - wechselt automatisch ins Projektstammverzeichnis.
REM ============================================================
setlocal
cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
    echo [FEHLER] .venv nicht gefunden. Bitte zuerst scripts\setup.bat ausfuehren.
    pause
    exit /b 1
)

echo [START] Starte OPC UA Server in eigenem Fenster ...
start "OPC UA Server" ".venv\Scripts\python.exe" "src\factory_server_de.py"

echo [START] Warte, bis der Server bereit ist ...
timeout /t 4 /nobreak >nul

echo [START] Oeffne Jupyter Lab mit dem Simulations-Notebook ...
echo [START] Im Browser: Kernel "Python (IoP Agenten)" waehlen und "Run All" ausfuehren.
echo.
call ".venv\Scripts\python.exe" -m jupyter lab "notebooks\Agenten_mit_OPC_Hot_Monitor_de.ipynb"

endlocal
