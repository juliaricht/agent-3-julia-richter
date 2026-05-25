"""Headless-Tests fuer factory_model_de (kein OPC UA-Server noetig)."""

import factory_model_de as fm
from factory_model_de import (
    FactoryModelExtended, MachineAgent, MaintenanceAgent, OrderAgent,
    count_orders, count_machines, count_hot_machines, count_maintenance,
)


def _bare_model(seed=1):
    """Minimalmodell ohne automatische Agenten (density=0)."""
    return FactoryModelExtended(
        width=5, height=5, density=0.0, threshold=70,
        n_maintenance=0, n_orders=0, seed=seed,
    )


def _place_machine(model, pos, name="M01", temp=20.0, state="OK", busy=False):
    m = MachineAgent(model, threshold=model.threshold, machine_name=name)
    m.temperature = temp
    m.state = state
    m.busy = busy
    model.grid.place_agent(m, pos)
    return m


def test_order_heats_machine():
    # Hitze entsteht durch ARBEIT: ein Auftrag belegt die Maschine und heizt sie.
    model = _bare_model()
    machine = _place_machine(model, (2, 2))
    order = OrderAgent(model, duration=20)
    model.grid.place_agent(order, (2, 2))
    start = machine.temperature
    for _ in range(8):
        order.step()
    assert machine.busy is True
    assert machine.temperature > start


def test_idle_machine_does_not_run_away():
    # Leerlauf: Maschine driftet symmetrisch und kuehlt leicht -> kein Weglaufen.
    model = _bare_model()
    machine = _place_machine(model, (2, 2), temp=30.0)
    for _ in range(60):
        machine.step()
    assert machine.temperature < 70  # geht nicht allein in HOT


def test_decide_state_thresholds():
    model = _bare_model()
    machine = _place_machine(model, (2, 2))

    machine.temperature = 80
    machine.state = "OK"
    machine.decide()
    assert machine.state == "HOT"
    assert model.hot_events_occurred == 1  # Uebergang in HOT gezaehlt

    # Bleibt HOT -> kein erneutes Zaehlen
    machine.decide()
    assert model.hot_events_occurred == 1

    machine.temperature = 60  # > 0.75*70 = 52.5
    machine.state = "OK"
    machine.decide()
    assert machine.state == "WARM"

    machine.temperature = 40
    machine.decide()
    assert machine.state == "OK"

    machine.temperature = 10
    machine.decide()
    assert machine.state == "COOL"


def test_maintenance_cools_earlywarning():
    model = _bare_model()
    machine = _place_machine(model, (2, 2), name="M01", temp=65.0, state="WARM")
    maint = MaintenanceAgent(model)
    model.grid.place_agent(maint, (1, 1))  # benachbart (Moore)

    fm.server_flags["M01"] = {"EarlyWarning": True, "RepairNeeded": False}

    maint.step()

    assert machine.temperature == 65.0 - model.cooling_power
    assert model.hot_events_prevented == 1
    assert fm.opcua_earlywarning_buffer.get("M01") is False
    assert maint.pos == (2, 2)  # ist zur Maschine gewandert


def test_maintenance_fixes_hot():
    model = _bare_model()
    machine = _place_machine(model, (2, 2), name="M01", temp=90.0, state="HOT", busy=True)
    maint = MaintenanceAgent(model)
    model.grid.place_agent(maint, (2, 2))  # gleiche Zelle

    maint.step()

    assert machine.state == "OK"
    assert machine.busy is False
    assert machine.temperature == model.repair_target
    assert fm.opcua_repair_buffer.get("M01") is False


def test_order_occupies_and_frees():
    model = _bare_model()
    machine = _place_machine(model, (2, 2), name="M01")
    order = OrderAgent(model, duration=2)
    model.grid.place_agent(order, (2, 2))

    order.step()  # belegt die Maschine
    assert machine.busy is True
    assert order.assigned_machine is machine

    # weiterarbeiten bis Auftrag fertig
    for _ in range(5):
        if order not in model.agents:
            break
        order.step()

    assert machine.busy is False
    assert model.orders_completed >= 1
    assert count_orders(model) == 0


def test_order_continuous_generation():
    model = FactoryModelExtended(
        width=6, height=6, density=0.3, threshold=70,
        n_maintenance=1, n_orders=5, seed=7,
    )
    model.order_spawn_rate = 1.0
    model.target_backlog = 5

    total_open = 0
    steps = 60
    for _ in range(steps):
        model.step()
        total_open += count_orders(model)

    # Anders als frueher (einmalig erzeugt -> faellt auf 0): der Backlog erholt
    # sich kontinuierlich. Mittlere offene Auftragszahl bleibt deutlich > 0.
    assert total_open / steps > 1.0
    assert count_orders(model) > 0  # am Ende erholt


def test_model_runs_without_error():
    model = FactoryModelExtended(seed=42)
    for _ in range(100):
        model.step()
    df = model.datacollector.get_model_vars_dataframe()
    for col in ("HotMachines", "AvgTemp", "MaxTemp", "PraevRate", "AbgeschlossenKumul"):
        assert col in df.columns
    assert len(df) == 101  # initial collect + 100 Schritte


def test_early_warning_fires_before_hot():
    # Eine kontinuierlich geheizte Maschine muss eine Frühwarnung ausloesen,
    # BEVOR sie HOT wird (Trendprognose).
    model = _bare_model()
    machine = _place_machine(model, (2, 2))
    order = OrderAgent(model, duration=40)
    model.grid.place_agent(order, (2, 2))
    warned_before_hot = False
    for _ in range(40):
        machine.step()
        order.step()
        if machine.state == "HOT":
            break
        if machine.early_warning:
            warned_before_hot = True
    assert warned_before_hot


def test_early_warning_published_to_opc_buffer():
    # Frühwarnung muss in den OPC-Puffer geschrieben werden (Signalweg ueber OPC UA):
    # opcua_writer -> Mxx_EarlyWarning-Knoten -> opcua_reader -> server_flags.
    model = _bare_model()
    machine = _place_machine(model, (2, 2), name="M01")
    order = OrderAgent(model, duration=40)
    model.grid.place_agent(order, (2, 2))
    for _ in range(40):
        machine.step()
        order.step()
        if machine.early_warning:
            break
    assert machine.early_warning is True
    assert fm.opcua_earlywarning_buffer.get("M01") is True  # an OPC gemeldet


def test_set_maintenance_count():
    model = _bare_model()
    assert count_maintenance(model) == 0
    model.set_maintenance_count(3)
    assert count_maintenance(model) == 3
    model.set_maintenance_count(1)
    assert count_maintenance(model) == 1


def test_reset_parameters():
    model = _bare_model()
    model.threshold = 99
    model.order_heat = 5.5
    model.use_prediction = False
    model.set_maintenance_count(4)
    model.reset_parameters()
    assert model.threshold == 70
    assert model.order_heat == 2.0
    assert model.use_prediction is True
    assert count_maintenance(model) == 0  # _bare_model default n_maintenance=0


def test_predictive_beats_reactive():
    # Validierung: praediktiv erzeugt weniger tatsaechliche HOT-Ereignisse als rein reaktiv.
    def occurred(use_pred, seed):
        m = FactoryModelExtended(width=10, height=10, density=0.2, threshold=70,
                                 n_maintenance=2, n_orders=10, seed=seed)
        m.use_prediction = use_pred
        for _ in range(200):
            m.step()
        return m.hot_events_occurred
    pred = sum(occurred(True, s) for s in range(4))
    react = sum(occurred(False, s) for s in range(4))
    assert pred < react


def test_system_tends_to_low_hot():
    # Kernziel: mit Standardparametern (2 Wartungsagenten) strebt das System
    # wenige HOT-Maschinen an. Kein Weglaufen wie zuvor.
    model = FactoryModelExtended(
        width=10, height=10, density=0.2, threshold=70,
        n_maintenance=2, n_orders=10, seed=2,
    )
    hots = []
    for _ in range(150):
        model.step()
        hots.append(count_hot_machines(model))
    last_avg = sum(hots[-40:]) / 40
    n = count_machines(model)
    # Im eingeschwungenen Zustand sind fast keine Maschinen HOT.
    assert last_avg < max(2.0, 0.1 * n)
    # Praevention dominiert ueber tatsaechliche HOT-Ereignisse.
    assert model.hot_events_prevented > model.hot_events_occurred


def test_live_threshold_changes_behavior():
    model = _bare_model()
    machine = _place_machine(model, (2, 2), temp=60.0)

    model.threshold = 70
    machine.decide()
    assert machine.state == "WARM"  # 60 > 52.5, < 70

    # Schwellwert live senken -> sofort HOT
    model.threshold = 50
    machine.decide()
    assert machine.state == "HOT"
