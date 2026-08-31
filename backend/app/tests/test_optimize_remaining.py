"""Optimize Remaining (secciones 26-28, 61-62 de V3): reoptimiza todo menos
las piezas Locked, que deben quedar exactamente donde estaban."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _window(**overrides):
    defaults = dict(
        code="W1",
        description="Ventana test",
        width=800,
        height=1200,
        thickness=80,
        weight=15,
        quantity=15,
        system="SysA",
        group="G1",
        stackable=True,
        priority=1,
    )
    defaults.update(overrides)
    return defaults


def _pack(items, container_id="40ft_standard"):
    r = client.post("/api/pack", json={"items": items, "container_id": container_id, "optimization_mode": "best_space"})
    assert r.status_code == 200
    return r.json()["best"]


def test_optimize_remaining_keeps_locked_pieces_unchanged():
    result = _pack([_window(quantity=15)])
    assert result["metrics"]["loaded_pieces"] == 15

    to_lock = result["placed"][:3]
    for p in to_lock:
        r = client.post("/api/lock-piece", json={"piece_id": p["id"]})
        assert r.status_code == 200

    r = client.post("/api/optimize-remaining")
    assert r.status_code == 200
    after = r.json()["best"]

    for original in to_lock:
        current = next(p for p in after["placed"] if p["id"] == original["id"])
        assert current["locked"] is True
        assert (current["x"], current["y"], current["z"]) == (original["x"], original["y"], original["z"])
        assert (current["dx"], current["dy"], current["dz"]) == (original["dx"], original["dy"], original["dz"])
        assert current["orientation_label"] == original["orientation_label"]

    ids = [p["id"] for p in after["placed"]]
    assert len(ids) == len(set(ids)), "no debe haber ids duplicados"
    assert after["metrics"]["loaded_pieces"] == 15


def test_optimize_remaining_reorganizes_unlocked_and_unloaded():
    container = "20ft_standard"  # mas chico: fuerza que algo quede unloaded
    # 500 piezas excede con margen la capacidad teorica del contenedor
    # (piso ~5898x2352mm / footprint minimo 800x80mm, con a lo sumo 2 capas
    # de alto -> muy por debajo de 500), a diferencia de una cantidad chica
    # que puede terminar cabiendo entera y dejar el test sin caso de prueba.
    result = _pack([_window(quantity=500)], container_id=container)
    assert result["metrics"]["unloaded_pieces"] >= 1, "el test necesita al menos una pieza sin cargar de entrada"

    total_before = result["metrics"]["total_pieces"]
    unloaded_before = result["metrics"]["unloaded_pieces"]

    # bloquea 2 piezas cargadas; el resto (cargadas + no cargadas) puede reorganizarse
    to_lock = result["placed"][:2]
    for p in to_lock:
        client.post("/api/lock-piece", json={"piece_id": p["id"]})

    r = client.post("/api/optimize-remaining")
    assert r.status_code == 200
    after = r.json()["best"]

    # conservacion: ninguna pieza aparece ni desaparece, solo se reorganiza
    assert after["metrics"]["total_pieces"] == total_before
    assert after["metrics"]["loaded_pieces"] + after["metrics"]["unloaded_pieces"] == total_before
    # con 30 piezas y solo 2 locked, el resultado no tiene por que ser identico
    # al de antes, pero no deberia empeorar el numero de piezas sin cargar
    assert after["metrics"]["unloaded_pieces"] <= unloaded_before


def test_optimize_remaining_passes_optimization_mode_through():
    """Regresion del bug reportado en CUBOX V4 (prioridad 8): antes,
    /api/optimize-remaining no aceptaba body y siempre reusaba el
    optimization_mode del ultimo /api/pack -si el usuario elegia Keep Groups
    Together/Keep Systems Together *despues* de bloquear piezas, esa
    seleccion nunca llegaba al backend."""
    from app.api.routes import _current_state
    from app.models.schemas import OptimizationMode

    result = _pack([_window(quantity=10)])  # /api/pack guarda optimization_mode=best_space
    assert _current_state["optimization_mode"] == OptimizationMode.BEST_SPACE

    piece_id = result["placed"][0]["id"]
    client.post("/api/lock-piece", json={"piece_id": piece_id})

    r = client.post("/api/optimize-remaining", json={"optimization_mode": "keep_groups"})
    assert r.status_code == 200
    assert _current_state["optimization_mode"] == OptimizationMode.KEEP_GROUPS

    # sin mandar nada, se mantiene lo ultimo guardado (no vuelve a best_space solo)
    r2 = client.post("/api/optimize-remaining")
    assert r2.status_code == 200
    assert _current_state["optimization_mode"] == OptimizationMode.KEEP_GROUPS


def test_optimize_remaining_passes_weight_balance_mode_through():
    from app.api.routes import _current_state
    from app.models.schemas import WeightBalanceMode

    result = _pack([_window(quantity=10)])
    assert _current_state["weight_balance_mode"] == WeightBalanceMode.NORMAL

    piece_id = result["placed"][0]["id"]
    client.post("/api/lock-piece", json={"piece_id": piece_id})

    r = client.post("/api/optimize-remaining", json={"weight_balance_mode": "important"})
    assert r.status_code == 200
    assert _current_state["weight_balance_mode"] == WeightBalanceMode.IMPORTANT


def test_optimize_remaining_without_active_pack_returns_400():
    from app.api.routes import _current_state

    _current_state["result"] = None
    r = client.post("/api/optimize-remaining")
    assert r.status_code == 400


def test_optimize_remaining_rejects_when_locked_pieces_conflict():
    """Esto no deberia poder pasar via la API normal (mover una pieza
    bloqueada ya esta prohibido), pero Optimize Remaining valida el estado de
    las piezas Locked de todas formas antes de correr (seccion 28)."""
    from app.api.routes import _current_state

    result = _pack([_window(quantity=2)])
    a, b = result["placed"][0], result["placed"][1]
    client.post("/api/lock-piece", json={"piece_id": a["id"]})
    client.post("/api/lock-piece", json={"piece_id": b["id"]})

    state = _current_state["result"]
    piece_a = next(p for p in state.placed if p.id == a["id"])
    piece_b = next(p for p in state.placed if p.id == b["id"])
    piece_b.x, piece_b.y, piece_b.z = piece_a.x, piece_a.y, piece_a.z

    r = client.post("/api/optimize-remaining")
    assert r.status_code == 409
    assert "colisionan" in r.json()["detail"]
