@echo off
REM ============================================================
REM  start_sim.bat - Startet Jupyter Lab fuer die Simulation
REM  Oeffnet den Browser. Dort das Notebook
REM  "Agenten_mit_OPC_Hot_Monitor_de.ipynb" oeffnen und die
REM  Zellen von oben nach unten ausfuehren.
REM  WICHTIG: Vorher start_server.bat ausfuehren!
REM  Liegt in scripts\ - wechselt automatisch ins Projektstammverzeichnis.
REM ============================================================
setlocal
cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
    echo [FEHLER] .venv nicht gefunden. Bitte zuerst scripts\setup.bat ausfuehren.
    pause
    exit /b 1
)

echo [SIM] Stelle sicher, dass der OPC UA Server bereits laeuft (scripts\start_server.bat).
echo [SIM] Starte Jupyter Lab (Startordner: notebooks) ...
echo [SIM] Kernel im Notebook auf "Python (IoP Agenten)" stellen.
echo.
call ".venv\Scripts\python.exe" -m jupyter lab notebooks

pause
endlocal
