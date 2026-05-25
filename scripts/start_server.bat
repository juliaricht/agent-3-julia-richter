@echo off
REM ============================================================
REM  start_server.bat - Startet den OPC UA Fabrik-Server
REM  Blockiert, solange der Server laeuft. Mit Strg+C beenden.
REM  Liegt in scripts\ - wechselt automatisch ins Projektstammverzeichnis.
REM ============================================================
setlocal
cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
    echo [FEHLER] .venv nicht gefunden. Bitte zuerst scripts\setup.bat ausfuehren.
    pause
    exit /b 1
)

echo [SERVER] Starte OPC UA Fabrik-Server ...
echo [SERVER] Endpunkt: opc.tcp://localhost:4840/freeopcua/server/
echo [SERVER] Zum Beenden: Strg+C
echo.
call ".venv\Scripts\python.exe" "src\factory_server_de.py"

pause
endlocal
