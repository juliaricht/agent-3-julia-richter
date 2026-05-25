#!/usr/bin/env python
# coding: utf-8
"""PredictorAgent – Frühwarn-Agent für Maschinenüberhitzung.

Abonniert Temperaturwerte aller Maschinen via OPC UA-Subscription,
hält pro Maschine eine Temperaturhistorie (gleitendes Fenster),
und setzt das EarlyWarning-Flag sobald eine lineare Regression
eine bevorstehende Überhitzung prognostiziert.

Start:  .venv\\Scripts\\python.exe src\\predictor_agent_de.py
Stop:   Strg+C
"""

import asyncio
import logging
from collections import deque
from datetime import datetime
from typing import Dict, Optional

import numpy as np
from asyncua import Client, ua

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("predictor-agent")

# --- Konfiguration -------------------------------------------------------
SERVER_URL     = "opc.tcp://localhost:4840/freeopcua/server/"
FACTORY_NS_URI = "http://ostfalia.de/ipt/factory"

WINDOW         = 10    # Anzahl der gespeicherten Messpunkte pro Maschine
HORIZON        = 5     # Prognoseschritte voraus (in Simulations-Ticks)
HOT_THRESHOLD  = 60.0  # °C – Temperatur ab der eine Maschine als HOT gilt
PUBLISH_MS     = 500   # OPC UA-Subscription publishing interval (ms)


# --- Datenklasse ---------------------------------------------------------

class MachineNodes:
    """Knotenreferenzen und Zustandspuffer für eine einzelne Maschine."""

    def __init__(self, name: str, temp_node, early_warning_node):
        self.name                    = name
        self.temp_node               = temp_node
        self.early_warning_node      = early_warning_node
        self.history: deque          = deque(maxlen=WINDOW)
        # lokaler Merker verhindert unnötige OPC UA-Schreibzugriffe
        self.ew_aktiv: bool          = False

    def __repr__(self) -> str:
        return (
            f"MachineNodes(name={self.name!r}, "
            f"temp={self.temp_node.nodeid}, "
            f"ew={self.early_warning_node.nodeid})"
        )


# --- Discovery -----------------------------------------------------------

async def discover_machines_with_jobs(client: Client) -> Dict[str, MachineNodes]:
    """Alle Maschinen sowie Temperature- und EarlyWarning-Knoten ermitteln.

    Struktur:
        Objects
         └─ Factory
             ├─ Machines
             │   └─ Mxx / Temperature
             └─ Maintenance
                 └─ Jobs
                     ├─ Mxx_RepairNeeded
                     └─ Mxx_EarlyWarning   ← neu (M2)
    """
    ns = await client.get_namespace_index(FACTORY_NS_URI)
    logger.info("[DISCOVERY] Namespace-Index für %r: %s", FACTORY_NS_URI, ns)

    objects         = client.nodes.objects
    factory         = await objects.get_child([f"{ns}:Factory"])
    machines_folder = await factory.get_child([f"{ns}:Machines"])
    maintenance     = await factory.get_child([f"{ns}:Maintenance"])
    jobs_folder     = await maintenance.get_child([f"{ns}:Jobs"])

    machine_nodes: Dict[str, MachineNodes] = {}

    for mobj in await machines_folder.get_children():
        bname = await mobj.read_browse_name()
        name  = bname.Name

        if not name.startswith("M") or len(name) != 3:
            logger.debug("[DISCOVERY] Nicht-Maschinenknoten übersprungen: %s", bname)
            continue

        try:
            temp_node = await mobj.get_child([f"{ns}:Temperature"])
        except Exception as exc:
            logger.warning("[DISCOVERY] %s: Temperature-Knoten fehlt (%s)", name, exc)
            continue

        ew_name = f"{name}_EarlyWarning"
        try:
            ew_node = await jobs_folder.get_child([f"{ns}:{ew_name}"])
        except Exception as exc:
            logger.warning("[DISCOVERY] %s: EarlyWarning-Knoten '%s' fehlt (%s)", name, ew_name, exc)
            continue

        machine_nodes[name] = MachineNodes(name, temp_node, ew_node)
        logger.info("[DISCOVERY] %s: temp=%s  ew=%s", name, temp_node.nodeid, ew_node.nodeid)

    logger.info("[DISCOVERY] %d Maschinen gefunden.", len(machine_nodes))
    return machine_nodes


# --- Prognose-Logik ------------------------------------------------------

def prognose_berechnen(machine: MachineNodes) -> Optional[float]:
    """Lineare Regression über die Temperaturhistorie.

    Gibt None zurück, solange weniger als 2 Messpunkte vorliegen.
    Rückgabewert: prognostizierte Temperatur in HORIZON Schritten.
    """
    history = list(machine.history)
    if len(history) < 2:
        return None

    x = np.arange(len(history), dtype=float)
    slope, _ = np.polyfit(x, history, 1)
    return float(history[-1] + slope * HORIZON)


async def early_warning_aktualisieren(machine: MachineNodes) -> None:
    """EarlyWarning-Flag basierend auf Temperaturtrend setzen oder zurücksetzen.

    Schreibt nur bei Zustandswechsel auf den OPC UA-Server (Hysterese).
    """
    projected = prognose_berechnen(machine)
    if projected is None:
        return  # noch zu wenige Messpunkte

    soll_aktiv = projected >= HOT_THRESHOLD

    if soll_aktiv == machine.ew_aktiv:
        return  # keine Zustandsänderung

    machine.ew_aktiv = soll_aktiv
    await machine.early_warning_node.write_value(
        ua.Variant(soll_aktiv, ua.VariantType.Boolean)
    )

    if soll_aktiv:
        logger.warning(
            "[PROGNOSE] %s EarlyWarning=True  – progn. %.1f °C in %d Schritten",
            machine.name, projected, HORIZON,
        )
    else:
        logger.info(
            "[PROGNOSE] %s EarlyWarning=False – Trend erholt (progn. %.1f °C)",
            machine.name, projected,
        )


# --- Subscription-Handler ------------------------------------------------

class PredictorSubHandler:
    """OPC UA-Subscription-Handler: puffert Temperaturwerte und löst Prognose aus."""

    def __init__(self, machines_by_nodeid: Dict):
        self.machines_by_nodeid = machines_by_nodeid

    def datachange_notification(self, node, val, data):
        machine = self.machines_by_nodeid.get(node.nodeid)
        if machine is None:
            return
        machine.history.append(float(val))
        # Prognose asynchron in der laufenden Event-Loop berechnen
        loop = asyncio.get_event_loop()
        loop.create_task(early_warning_aktualisieren(machine))

    def event_notification(self, event):
        logger.debug("[SUB] Event: %s", event)


# --- Hauptschleife -------------------------------------------------------

async def run_predictor() -> None:
    """PredictorAgent als dauerhaft laufende asyncio-Aufgabe starten."""
    async with Client(url=SERVER_URL) as client:
        logger.info("[PREDICTOR] Verbunden mit %s", SERVER_URL)

        machines = await discover_machines_with_jobs(client)
        if not machines:
            logger.error("[PREDICTOR] Keine Maschinen gefunden – Abbruch.")
            return

        by_nodeid = {m.temp_node.nodeid: m for m in machines.values()}
        handler   = PredictorSubHandler(by_nodeid)

        subscription = await client.create_subscription(PUBLISH_MS, handler)
        for m in machines.values():
            handle = await subscription.subscribe_data_change(m.temp_node)
            logger.info("[SUB] %s Temperature abonniert (handle=%s)", m.name, handle)

        logger.info(
            "[PREDICTOR] Frühwarn-Agent aktiv. Fenster=%d  Horizont=%d  Schwelle=%.0f °C",
            WINDOW, HORIZON, HOT_THRESHOLD,
        )
        try:
            while True:
                await asyncio.sleep(1)
        finally:
            await subscription.delete()
            logger.info("[PREDICTOR] Subscription gelöscht, Agent beendet.")


if __name__ == "__main__":
    try:
        asyncio.run(run_predictor())
    except KeyboardInterrupt:
        print("\n[PREDICTOR] Beendet durch Benutzer (Strg+C).")
