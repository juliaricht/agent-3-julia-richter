# Predictive-Maintenance-Agenten

Portfolio-Aufgabe Szenario 3 —
Multi-Agenten-Fabriksimulation mit vorausschauender Wartung via OPC UA.

Ein **PredictorAgent** erkennt Temperaturtrends (gleitender Durchschnitt + lineare Regression),
setzt ein `EarlyWarning`-Flag über OPC UA, und **MaintenanceAgents** kühlen die Maschine,
bevor sie überhitzt.

---

## Voraussetzungen

- Windows 10/11
- Python 3.11+ mit `py`-Launcher ([python.org](https://www.python.org/downloads/))

---

## Einrichtung (einmalig)

```
scripts\setup.bat
```

Legt die virtuelle Umgebung an, installiert alle Pakete und registriert den Jupyter-Kernel.

---

## Starten

**Alles auf einmal:**

```
scripts\start_all.bat
```

Oder manuell in zwei Fenstern:

1. `scripts\start_server.bat` — OPC UA Server starten und Fenster **offen lassen**
2. `scripts\start_sim.bat` — Jupyter Lab öffnen

Im Notebook den Kernel **„Python (IoP Agenten)"** auswählen und alle Zellen ausführen (*Run All*).

> Reihenfolge beachten: **erst Server, dann Simulation.**

---

## Tests (kein OPC UA Server nötig)

```
.venv\Scripts\python.exe -m pytest src\tests -v
```
