@echo off
REM ============================================================
REM  setup.bat - Einmalige Einrichtung der Projektumgebung
REM  Erstellt eine virtuelle Umgebung (.venv), installiert alle
REM  Pakete aus requirements.txt und registriert den Jupyter-Kernel.
REM  Kann gefahrlos mehrfach ausgefuehrt werden.
REM  Liegt in scripts\ - wechselt automatisch ins Projektstammverzeichnis.
REM ============================================================
setlocal
cd /d "%~dp0.."

echo [SETUP] Pruefe Python (py-Launcher)...
py --version >nul 2>&1
if errorlevel 1 (
    echo [FEHLER] Python wurde nicht gefunden. Bitte Python 3.11+ installieren
    echo          und sicherstellen, dass der "py"-Launcher verfuegbar ist.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo [SETUP] Erstelle virtuelle Umgebung .venv ...
    py -m venv .venv
) else (
    echo [SETUP] .venv existiert bereits - wird wiederverwendet.
)

echo [SETUP] Aktualisiere pip ...
call ".venv\Scripts\python.exe" -m pip install --upgrade pip

echo [SETUP] Installiere Pakete aus requirements.txt ...
call ".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo [FEHLER] Installation der Pakete fehlgeschlagen.
    pause
    exit /b 1
)

echo [SETUP] Registriere Jupyter-Kernel "iop-agents" ...
call ".venv\Scripts\python.exe" -m ipykernel install --user --name iop-agents --display-name "Python (IoP Agenten)"

echo.
echo [SETUP] Fertig. Naechste Schritte:
echo         1) scripts\start_server.bat  (OPC UA Server starten)
echo         2) scripts\start_sim.bat     (Simulation / Notebook starten)
echo.
pause
endlocal
