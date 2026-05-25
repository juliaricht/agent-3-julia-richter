"""Fabrikmodell und Agenten (Mesa 3.x).

Dieses Modul enthält die Simulationslogik (Maschinen-, Wartungs-
und Auftragsagenten sowie das erweiterte Fabrikmodell). Es ist bewusst von der
OPC UA-Anbindung entkoppelt: Agenten lesen/schreiben nur die unten definierten
Modul-Puffer (einfache dicts). Läuft kein OPC UA-Task, bleiben die Puffer leer
und das Modell ist vollständig eigenstädig lauffähig (z. B. für pytest).

Das Notebook importiert dieselben Puffer-Objekte aus diesem Modul, sodass die
OPC UA-Writer/Reader-Tasks und die Agenten denselben Zustand teilen.
"""

import random
from collections import deque

from mesa import Agent, Model
from mesa.space import MultiGrid
from mesa.datacollection import DataCollector


# ─────────────────────────────────────────────────────────────────────────────
# Gemeinsame Datenpuffer (Entkopplung Mesa <-> OPC UA)
#   - opcua_write_buffer:        Maschinenname -> {Temperature, State, Busy}
#   - opcua_repair_buffer:       RepairNeeded-Flags die zurückgesetzt werden
#   - opcua_earlywarning_buffer: EarlyWarning-Flags die zurückgesetzt werden
#   - server_flags:              vom Reader befüllt, von Agenten gelesen
# Das Notebook importiert diese Objekte direkt; die OPC-Tasks mutieren sie
# in-place, daher teilen Agenten und Tasks denselben Zustand.
# ─────────────────────────────────────────────────────────────────────────────
opcua_write_buffer: dict = {}
opcua_repair_buffer: dict = {}
opcua_earlywarning_buffer: dict = {}
server_flags: dict = {}


def set_opcua_buffers(write=None, repair=None, earlywarning=None, flags=None):
    """Ersetzt die Modul-Puffer durch eigene dicts (v. a. fuer Tests/Isolation).

    Im Normalbetrieb nicht nötig - das Notebook importiert Puffer direkt.
    Im Test ermöglicht der Setter frische, isolierte Puffer pro Testfall.
    """
    global opcua_write_buffer, opcua_repair_buffer, opcua_earlywarning_buffer, server_flags
    if write is not None:
        opcua_write_buffer = write
    if repair is not None:
        opcua_repair_buffer = repair
    if earlywarning is not None:
        opcua_earlywarning_buffer = earlywarning
    if flags is not None:
        server_flags = flags


def reset_opcua_buffers():
    """Leert alle Modul-Puffer in-place (Testhilfe, behaelt Objektidentitaet)."""
    opcua_write_buffer.clear()
    opcua_repair_buffer.clear()
    opcua_earlywarning_buffer.clear()
    server_flags.clear()


# ─────────────────────────────────────────────────────────────────────────────
# Agenten
# ─────────────────────────────────────────────────────────────────────────────
class MachineAgent(Agent):
    """Reaktiver Maschinenagent mit OPC UA-Anbindung über gemeinsamen Datenpuffer."""

    def __init__(self, model, threshold=70, machine_name=None):
        super().__init__(model)
        self.temperature = 20.0
        self.threshold = threshold
        self.state = "OK"
        self.prev_state = "OK"  # Zustandsübergang HOT erkennen
        self.busy = False
        self.machine_name = machine_name
        self.early_warning = False                 # lokale Trendprognose
        self._ew_published = False                  # zuletzt an OPC gemeldeter EW-Wert
        self.temp_history = deque([20.0], maxlen=6)  # für die Trendberechnung

    def sense_temperature(self):
        # HOT-Maschinen brauchen Wartung, um abzukühlen (kein Selbstheilen).
        # Sonst symmetrisches Rauschen (netto ~0): Hitze entsteht durch ARBEIT
        # (belegte Maschinen, +5/Schritt durch Auftraege), nicht durch Drift.
        if self.state == "HOT":
            return
        d = self.model.temp_drift_max
        self.temperature += random.uniform(-d, d)
        if not self.busy:
            # Leerlauf: leichte Abkühlung Richtung Umgebungstemperatur (20°C)
            self.temperature += (20.0 - self.temperature) * 0.05
        self.temperature = max(15.0, self.temperature)

    def decide(self):
        thr = self.model.threshold  # live veraenderbarer Schwellwert
        self.prev_state = self.state
        if self.temperature > thr:
            self.state = "HOT"
        elif self.temperature > 0.75 * thr:
            self.state = "WARM"
        elif self.temperature < 20:
            self.state = "COOL"
        else:
            self.state = "OK"
        # HOT-Ereignis zählen (nur beim ersten Übergang in HOT)
        if self.state == "HOT" and self.prev_state != "HOT":
            self.model.hot_events_occurred += 1

    def predict_early_warning(self):
        """Einfache Trendprognose (lineare Extrapolation der letzten Messwerte).

        Frühwarnung, wenn die Maschine (noch nicht HOT) bei gleichbleibendem Trend
        innerhalb von warn_lookahead Schritten den Schwellwert ueberschreiten wird.
        Entspricht dem Szenario-Ansatz (gleitender Mittelwert + lineare Regression).
        """
        self.temp_history.append(self.temperature)
        if self.state == "HOT":
            self.early_warning = False
            return
        h = list(self.temp_history)
        slope = (h[-1] - h[0]) / (len(h) - 1) if len(h) >= 2 else 0.0
        projected = self.temperature + slope * self.model.warn_lookahead
        thr = self.model.threshold
        self.early_warning = (self.temperature >= 0.5 * thr) and (projected >= thr)

    def act(self):
        pass

    def step(self):
        self.sense_temperature()
        self.decide()
        self.predict_early_warning()
        self.act()
        if self.machine_name:
            opcua_write_buffer[self.machine_name] = {
                "Temperature": round(self.temperature, 2),
                "State":       self.state,
                "Busy":        self.busy,
            }
            # Frühwarnung ueber OPC UA veröffentlichen (nur bei Aenderung).
            # Der opcua_writer schreibt den Wert auf den Knoten Mxx_EarlyWarning,
            # der opcua_reader liest ihn zurueck in server_flags -> echter
            # Signalweg Maschine -> OPC UA Server -> Wartungsagent.
            if self.early_warning != self._ew_published:
                opcua_earlywarning_buffer[self.machine_name] = self.early_warning
                self._ew_published = self.early_warning


def machine_is_warned(machine):
    """Frühwarnung aktiv? Lokale Trendprognose ODER OPC-EarlyWarning-Flag."""
    if getattr(machine, "early_warning", False):
        return True
    return bool(
        machine.machine_name
        and server_flags.get(machine.machine_name, {}).get("EarlyWarning", False)
    )


class MaintenanceAgent(Agent):
    """Praediktiv-reaktiver Wartungsagent.

    Steuert aktiv frühgewarnte / heiße Maschinen an und kühlt sie, BEVOR sie
    HOT werden (Prävention) bzw. repariert sie, falls bereits HOT. Dadurch
    strebt das System einen Zustand mit möglichst wenigen HOT-Maschinen an.

    Prioritöäten pro step():
      1. heißeste frühgewarnte Maschine (lokal ODER OPC) ansteuern/kühlen
      2. sonst heißeste Maschine über Beobachtungsschwelle ansteuern/kühlen
      3. stehen bleiben, wenn alles ruhig ist
    """

    def _cool_machine(self, machine):
        """Kühlt eine Maschine und zählt Prävention bzw. Reparatur."""
        thr = self.model.threshold
        war_hot = machine.state == "HOT"
        machine.temperature = max(20.0, machine.temperature - self.model.cooling_power)
        machine.state = (
            "OK"   if machine.temperature < thr * 0.75 else
            "WARM" if machine.temperature < thr else
            "HOT"
        )
        if war_hot:
            # reaktive Reparatur: HOT zurück auf sicheres Niveau
            machine.temperature = min(machine.temperature, self.model.repair_target)
            machine.state = "OK"
            machine.busy = False
            if machine.machine_name:
                opcua_repair_buffer[machine.machine_name] = False
        else:
            # präventive Kühlung -> HOT verhindert; Frühwarnung aufheben
            self.model.hot_events_prevented += 1
            machine.early_warning = False
            machine._ew_published = False
            if machine.machine_name:
                opcua_earlywarning_buffer[machine.machine_name] = False

    def step(self):
        thr = self.model.threshold
        target = None
        target_temp = -1.0

        if self.model.use_prediction:
            # Heißeste relevante Maschine ansteuern. "Relevant" = bereits HOT
            # (Reparatur, höchste Temperatur -> höchste Priorität), frühgewarnt
            # (lokal ODER OPC) oder über Beobachtungsschwelle (60% des Schwellwerts).
            # Da nach Temperatur sortiert wird, werden HOT-Maschinen zuerst behandelt
            # und trotzdem Frühwarnungen präventiv gekühlt.
            watch_temp = 0.6 * thr
            for agent in self.model.agents:
                if not isinstance(agent, MachineAgent):
                    continue
                relevant = (agent.state == "HOT" or machine_is_warned(agent)
                            or agent.temperature >= watch_temp)
                if relevant and agent.temperature > target_temp:
                    target_temp = agent.temperature
                    target = agent
        else:
            # rein reaktiv: nur bereits HOT gewordene Maschinen ansteuern/reparieren
            for agent in self.model.agents:
                if (isinstance(agent, MachineAgent) and agent.state == "HOT"
                        and agent.temperature > target_temp):
                    target_temp = agent.temperature
                    target = agent

        if target is not None:
            tx, ty = target.pos
            cx, cy = self.pos
            if max(abs(tx - cx), abs(ty - cy)) <= 1:
                # in Reichweite -> auf die Maschine ziehen und kuehlen
                self.model.grid.move_agent(self, target.pos)
                self._cool_machine(target)
            else:
                # test
                nx = cx + (1 if tx > cx else -1 if tx < cx else 0)
                ny = cy + (1 if ty > cy else -1 if ty < cy else 0)
                self.model.grid.move_agent(self, (nx, ny))
            return

        # Priorität 3: alles ruhig -> stehen bleiben (kein nervöses "Springen")


class OrderAgent(Agent):
    """Produktionsauftrag: sucht eine freie Maschine, belegt sie fürr 'duration' Schritte.

    Unbelegte Aufträge bewegen sich gerichtet zur nächsten freien Maschine
    (statt rein zufällig), was das "Herumspringen" in der Visualisierung reduziert.
    """

    def __init__(self, model, duration=10):
        super().__init__(model)
        self.duration = duration
        self.assigned_machine = None

    def _step_towards(self, target_pos):
        """Bewegt sich eine Zelle in Richtung target_pos (Chebyshev/Moore-Schritt)."""
        cx, cy = self.pos
        tx, ty = target_pos
        nx = cx + (1 if tx > cx else -1 if tx < cx else 0)
        ny = cy + (1 if ty > cy else -1 if ty < cy else 0)
        self.model.grid.move_agent(self, (nx, ny))

    def _nearest_free_machine_pos(self):
        """Position der nächstgelegenen freien (nicht HOT, nicht belegten) Maschine."""
        best_pos = None
        best_dist = None
        cx, cy = self.pos
        for agent in self.model.agents:
            if isinstance(agent, MachineAgent) and agent.state != "HOT" and not agent.busy:
                ax, ay = agent.pos
                dist = max(abs(ax - cx), abs(ay - cy))  # Chebyshev-Distanz
                if best_dist is None or dist < best_dist:
                    best_dist = dist
                    best_pos = agent.pos
        return best_pos

    def step(self):
        if self.assigned_machine is None:
            target = self._nearest_free_machine_pos()
            if target is not None and target != self.pos:
                self._step_towards(target)
            elif target is None:
                # keine freie Maschine -> zufaellig bewegen
                neighbours = self.model.grid.get_neighborhood(
                    self.pos, moore=True, include_center=True
                )
                self.model.grid.move_agent(self, random.choice(neighbours))

            # Freie Maschine auf aktueller Zelle belegen
            for agent in self.model.grid.get_cell_list_contents([self.pos]):
                if isinstance(agent, MachineAgent) and agent.state != "HOT" and not agent.busy:
                    agent.busy = True
                    agent.temperature += self.model.order_heat
                    self.assigned_machine = agent
                    break
        else:
            self.duration -= 1
            for agent in self.model.grid.get_cell_list_contents([self.pos]):
                if isinstance(agent, MachineAgent) and agent.state != "HOT":
                    agent.temperature += self.model.order_heat
            if self.duration <= 0:
                self.assigned_machine.busy = False
                self.model.orders_completed += 1
                self.model.grid.remove_agent(self)
                self.remove()


# ─────────────────────────────────────────────────────────────────────────────
# Reporter-Hilfsfunktionen
# ─────────────────────────────────────────────────────────────────────────────
def count_hot_machines(model):
    return sum(1 for a in model.agents if isinstance(a, MachineAgent) and a.state == "HOT")


def count_warm_machines(model):
    return sum(1 for a in model.agents if isinstance(a, MachineAgent) and a.state == "WARM")


def count_machines(model):
    return sum(1 for a in model.agents if isinstance(a, MachineAgent))


def count_early_warnings(model):
    return sum(
        1 for a in model.agents
        if isinstance(a, MachineAgent) and machine_is_warned(a)
    )


def count_maintenance(model):
    return sum(1 for a in model.agents if isinstance(a, MaintenanceAgent))


def count_orders(model):
    return sum(1 for a in model.agents if isinstance(a, OrderAgent))


def avg_temperature(model):
    n = count_machines(model)
    if n == 0:
        return 0.0
    return sum(a.temperature for a in model.agents if isinstance(a, MachineAgent)) / n


def max_temperature(model):
    return max(
        (a.temperature for a in model.agents if isinstance(a, MachineAgent)),
        default=0.0,
    )


def prevention_rate(model):
    """Praeventionsrate in Prozent: verhindert / (verhindert + eingetreten)."""
    total = model.hot_events_prevented + model.hot_events_occurred
    if total == 0:
        return 0.0
    return 100.0 * model.hot_events_prevented / total


# ─────────────────────────────────────────────────────────────────────────────
# Modell
# ─────────────────────────────────────────────────────────────────────────────
class FactoryModelExtended(Model):
    """Erweitertes Fabrikmodell mit Maschinen-, Wartungs- und Auftragsagenten.

    Live veränderbare Parameter (mit Steuerpanel veränderbar):
      threshold, cooling_power, repair_target, temp_drift_max,
      order_spawn_rate, target_backlog
    """

    def __init__(self, width=10, height=10, density=0.3, threshold=70,
                 n_maintenance=2, n_orders=5, seed=None):
        super().__init__(seed=seed)
        self.width = width
        self.height = height
        self.grid = MultiGrid(width, height, torus=False)

        # Live veränderbare Parameter
        self.threshold        = threshold
        self.cooling_power    = 15.0           # präventive Kühlung (EarlyWarning)
        self.repair_target    = threshold - 40  # Zieltemperatur nach HOT-Reparatur
        self.temp_drift_max   = 2.0            # obere Grenze von sense_temperature
        self.order_spawn_rate = 0.3            # Wahrscheinlichkeit pro fehlendem Auftrag
        self.target_backlog   = n_orders       # angestrebte Anzahl offener Aufträge
        self.order_heat       = 2.0            # Erwärmung pro Schritt durch einen Auftrag
        self.warn_lookahead   = 5              # Prognosehorizont (Schritte) für Frühwarnung
        self.use_prediction   = True           # True=prädiktiv (Frühwarnung), False=rein reaktiv

        # Standardwerte für den "Zuruecksetzen"-Knopf merken
        self._param_defaults = {
            "threshold":        self.threshold,
            "cooling_power":    self.cooling_power,
            "temp_drift_max":   self.temp_drift_max,
            "order_spawn_rate": self.order_spawn_rate,
            "target_backlog":   self.target_backlog,
            "order_heat":       self.order_heat,
            "warn_lookahead":   self.warn_lookahead,
            "use_prediction":   self.use_prediction,
        }
        self._default_n_maintenance = n_maintenance

        # Kennzahlen
        self.hot_events_occurred  = 0  # Maschinen die tatsächlich HOT wurden
        self.hot_events_prevented = 0  # Maschinen die präventiv gekühlt wurden
        self.orders_completed     = 0  # kumuliert abgeschlossene Aufträge

        for _ in range(n_orders):
            pos = (self.random.randrange(width), self.random.randrange(height))
            self.grid.place_agent(OrderAgent(self), pos)

        machine_counter = 1
        for x in range(width):
            for y in range(height):
                if self.random.random() < density:
                    name = f"M{machine_counter:02d}"
                    self.grid.place_agent(
                        MachineAgent(self, threshold=threshold, machine_name=name), (x, y)
                    )
                    machine_counter += 1

        for _ in range(n_maintenance):
            self.add_maintenance_agent()

        self.datacollector = DataCollector(
            model_reporters={
                "HotMachines":    count_hot_machines,
                "WarmMachines":   count_warm_machines,
                "EarlyWarnings":  count_early_warnings,
                "Auftraege":      count_orders,
                "HotVerhindert":  lambda m: m.hot_events_prevented,
                "HotPassiert":    lambda m: m.hot_events_occurred,
                "AvgTemp":        avg_temperature,
                "MaxTemp":        max_temperature,
                "PraevRate":      prevention_rate,
                "AbgeschlossenKumul": lambda m: m.orders_completed,
            }
        )
        self.datacollector.collect(self)

    def add_maintenance_agent(self):
        """Fügt einen Wartungsagenten an zufälliger Position hinzu (auch live)."""
        pos = (self.random.randrange(self.width), self.random.randrange(self.height))
        self.grid.place_agent(MaintenanceAgent(self), pos)

    def remove_maintenance_agent(self):
        """Entfernt einen Wartungsagenten (auch live). Gibt True bei Erfolg zurück."""
        for a in list(self.agents):
            if isinstance(a, MaintenanceAgent):
                self.grid.remove_agent(a)
                a.remove()
                return True
        return False

    def set_maintenance_count(self, n):
        """Setzt die Anzahl der Wartungsagenten live auf n (fügt hinzu/entfernt)."""
        n = max(0, int(n))
        cur = count_maintenance(self)
        while cur < n:
            self.add_maintenance_agent()
            cur += 1
        while cur > n:
            if not self.remove_maintenance_agent():
                break
            cur -= 1

    def reset_parameters(self):
        """Setzt alle Live-Parameter auf die Standardwerte zurüvk."""
        for k, v in self._param_defaults.items():
            setattr(self, k, v)
        self.set_maintenance_count(self._default_n_maintenance)

    def _spawn_orders(self):
        """Erzeugt neue Aufträge bis zum Ziel-Backlog."""
        deficit = self.target_backlog - count_orders(self)
        for _ in range(max(0, deficit)):
            if self.random.random() < self.order_spawn_rate:
                pos = (self.random.randrange(self.width), self.random.randrange(self.height))
                self.grid.place_agent(OrderAgent(self), pos)

    def step(self):
        self._spawn_orders()
        self.agents.shuffle_do("step")
        self.datacollector.collect(self)
