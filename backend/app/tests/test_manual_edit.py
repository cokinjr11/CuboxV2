"""Tests de la edicion manual: mover, rotar, quitar, insertar, undo/redo.

Todos reutilizan las mismas reglas que el cubicaje automatico
(orientation.py / geometry.py via manual_move.validate_placement) - no existen
reglas paralelas para la edicion manual.
"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _window(**overrides):
    defaults = dict(
        code="W1",
        description="Ventana test",
        width=1200,
        height=2000,
        thickness=100,
        weight=45,
        quantity=1,
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


def test_move_to_empty_space_succeeds():
    result = _pack([_window(quantity=1)])
    piece = result["placed"][0]

    move = {
        "piece_id": piece["id"],
        "x": 3000,
        "y": 0,
        "z": 0,
        "dx": piece["dx"],
        "dy": piece["dy"],
        "dz": piece["dz"],
    }
    r = client.post("/api/apply-move", json=move)
    assert r.status_code == 200
    moved = next(p for p in r.json()["placed"] if p["id"] == piece["id"])
    assert moved["x"] == 3000


def test_three_level_stack_via_manual_move_api():
    """CUBOX V4 (auditoria Stackable, prioridad 10): la cobertura existente de
    apilamiento (test_geometry.py, test_support_percentage.py) solo prueba las
    funciones puras de geometria; esta prueba ejercita 3 niveles de
    apilamiento a traves de la API real de edicion manual, la que de verdad
    usa un operador."""
    # Ventana chica (dz maximo 700mm en cualquier orientacion valida) para que
    # 3 niveles (<=2100mm) quepan comodamente bajo la altura del 40ft_standard
    # (2393mm) -la ventana default del modulo (2000mm de alto) no cabria 3
    # veces apilada.
    result = _pack([_window(quantity=3, width=500, height=700, thickness=50, weight=8)])
    a, b, c = result["placed"][0], result["placed"][1], result["placed"][2]

    # Fuerza que b y c tomen exactamente la misma huella que a (mismo tipo de
    # ventana -> misma orientacion es valida para las 3) para un apilamiento
    # con soporte 100%, sin depender de que orientacion eligio el auto-pack.
    move_b = {
        "piece_id": b["id"],
        "x": a["x"],
        "y": a["y"],
        "z": a["z"] + a["dz"],
        "dx": a["dx"],
        "dy": a["dy"],
        "dz": a["dz"],
    }
    r1 = client.post("/api/apply-move", json=move_b)
    assert r1.status_code == 200
    b_after = next(p for p in r1.json()["placed"] if p["id"] == b["id"])
    assert b_after["z"] == a["z"] + a["dz"]

    move_c = {
        "piece_id": c["id"],
        "x": a["x"],
        "y": a["y"],
        "z": b_after["z"] + b_after["dz"],
        "dx": a["dx"],
        "dy": a["dy"],
        "dz": a["dz"],
    }
    r2 = client.post("/api/apply-move", json=move_c)
    assert r2.status_code == 200
    placed = r2.json()["placed"]
    c_after = next(p for p in placed if p["id"] == c["id"])
    assert c_after["z"] == b_after["z"] + b_after["dz"]

    # las 3 piezas siguen presentes, sin colisiones (si hubiera colision el
    # apply-move de arriba ya habria devuelto 409 antes de llegar aca)
    ids = {p["id"] for p in placed}
    assert {a["id"], b["id"], c["id"]} <= ids


def test_move_through_another_piece_rejected():
    result = _pack([_window(quantity=2)])
    a, b = result["placed"][0], result["placed"][1]

    move = {"piece_id": a["id"], "x": b["x"], "y": b["y"], "z": b["z"], "dx": a["dx"], "dy": a["dy"], "dz": a["dz"]}
    r = client.post("/api/apply-move", json=move)
    assert r.status_code == 409
    assert "Colisiona" in r.json()["detail"]

    # una mutacion rechazada nunca se apila en el historial
    r2 = client.post("/api/undo")
    assert r2.status_code == 400


def test_move_outside_container_rejected():
    result = _pack([_window(quantity=1)])
    piece = result["placed"][0]

    move = {"piece_id": piece["id"], "x": 999999, "y": 0, "z": 0, "dx": piece["dx"], "dy": piece["dy"], "dz": piece["dz"]}
    r = client.post("/api/apply-move", json=move)
    assert r.status_code == 409
    assert "limites" in r.json()["detail"]


def _move_to_open_corner(piece):
    """El motor ahora coloca la primera pieza pegada a la pared del fondo (sin
    espacio para crecer en +X). Estos tests solo quieren probar el ciclo de
    orientaciones, asi que la piezan se mueve primero a una esquina con
    espacio de sobra en las 3 direcciones."""
    r = client.post(
        "/api/apply-move",
        json={"piece_id": piece["id"], "x": 0, "y": 0, "z": 0, "dx": piece["dx"], "dy": piece["dy"], "dz": piece["dz"]},
    )
    assert r.status_code == 200
    return next(p for p in r.json()["placed"] if p["id"] == piece["id"])


def test_rotate_never_produces_glass_face_orientation():
    result = _pack([_window(quantity=1)])
    piece = _move_to_open_corner(result["placed"][0])

    r = client.post("/api/rotate-piece", json={"piece_id": piece["id"]})
    assert r.status_code == 200
    rotated = next(p for p in r.json()["placed"] if p["id"] == piece["id"])
    assert rotated["dz"] != piece["source_thickness"]
    # solo alterna entre las 2 orientaciones validas: rotar de nuevo debe volver a la original
    r2 = client.post("/api/rotate-piece", json={"piece_id": piece["id"]})
    back = next(p for p in r2.json()["placed"] if p["id"] == piece["id"])
    assert (back["dx"], back["dy"], back["dz"]) == (piece["dx"], piece["dy"], piece["dz"])


def test_turn_never_produces_glass_face_orientation():
    result = _pack([_window(quantity=1)])
    piece = _move_to_open_corner(result["placed"][0])

    r = client.post("/api/turn-piece", json={"piece_id": piece["id"]})
    assert r.status_code == 200
    turned = next(p for p in r.json()["placed"] if p["id"] == piece["id"])
    assert turned["dz"] != turned["source_thickness"]
    # Girar mantiene la misma dimension vertical, solo gira la base 90 grados.
    assert turned["dz"] == piece["dz"]
    assert {turned["dx"], turned["dy"]} == {piece["dx"], piece["dy"]}

    # girar de nuevo debe volver a la orientacion original
    r2 = client.post("/api/turn-piece", json={"piece_id": piece["id"]})
    back = next(p for p in r2.json()["placed"] if p["id"] == piece["id"])
    assert (back["dx"], back["dy"], back["dz"]) == (piece["dx"], piece["dy"], piece["dz"])


def test_rotate_and_turn_combined_reach_all_four_orientations_never_forbidden():
    """Combinando Rotar y Girar se debe poder llegar a las 4 orientaciones
    validas, y nunca a la prohibida (base Width x Height)."""
    result = _pack([_window(quantity=1)])
    piece = _move_to_open_corner(result["placed"][0])
    w, h, t = piece["source_width"], piece["source_height"], piece["source_thickness"]

    seen = {(piece["dx"], piece["dy"], piece["dz"])}
    # Rotar, Girar, Rotar: visita las 4 combinaciones (ver _ROTATE_PAIRS/_TURN_PAIRS)
    for endpoint in ["/api/rotate-piece", "/api/turn-piece", "/api/rotate-piece"]:
        r = client.post(endpoint, json={"piece_id": piece["id"]})
        assert r.status_code == 200
        current = next(p for p in r.json()["placed"] if p["id"] == piece["id"])
        dims = (current["dx"], current["dy"], current["dz"])
        seen.add(dims)
        # nunca la cara de vidrio (Width x Height) como base
        assert current["dz"] != t

    assert len(seen) == 4


def test_rotate_rejected_when_new_orientation_does_not_fit():
    # width=2500 excede la altura interna del 40ft standard (2393mm). La orientacion
    # inicial (P1-a: vertical=height=1000) si cabe, pero al rotar a P2-a el vertical
    # pasa a ser width=2500, que ya no cabe -> debe rechazarse y el estado no cambia.
    container_height = 2393
    result = _pack([_window(code="TALL", width=2500, height=1000, thickness=80, quantity=1)])
    piece = result["placed"][0]
    assert piece["dz"] == 1000 <= container_height

    r = client.post("/api/rotate-piece", json={"piece_id": piece["id"]})
    assert r.status_code == 409

    state = client.get("/api/state").json()
    unchanged = next(p for p in state["placed"] if p["id"] == piece["id"])
    assert (unchanged["dx"], unchanged["dy"], unchanged["dz"]) == (piece["dx"], piece["dy"], piece["dz"])


def test_stack_on_non_stackable_rejected():
    # height baja para que las dos, apiladas, quepan en la altura del contenedor
    # (lo que se quiere probar es la regla de stackable, no un choque con el techo).
    result = _pack(
        [
            _window(code="BASE", height=800, stackable=False, quantity=1),
            _window(code="TOP", height=800, quantity=1),
        ]
    )
    base = next(p for p in result["placed"] if p["code"] == "BASE")
    top = next(p for p in result["placed"] if p["code"] == "TOP")

    move = {
        "piece_id": top["id"],
        "x": base["x"],
        "y": base["y"],
        "z": base["dz"],
        "dx": top["dx"],
        "dy": top["dy"],
        "dz": top["dz"],
    }
    r = client.post("/api/apply-move", json=move)
    assert r.status_code == 409
    assert "apilable" in r.json()["detail"] or "no apilable" in r.json()["detail"]


def test_invalid_position_does_not_change_saved_state():
    result = _pack([_window(quantity=2)])
    a, b = result["placed"][0], result["placed"][1]
    original = (a["x"], a["y"], a["z"])

    move = {"piece_id": a["id"], "x": b["x"], "y": b["y"], "z": b["z"], "dx": a["dx"], "dy": a["dy"], "dz": a["dz"]}
    r = client.post("/api/apply-move", json=move)
    assert r.status_code == 409

    state = client.get("/api/state").json()
    unchanged = next(p for p in state["placed"] if p["id"] == a["id"])
    assert (unchanged["x"], unchanged["y"], unchanged["z"]) == original


def test_valid_move_updates_placement_and_metrics():
    result = _pack([_window(quantity=1)])
    piece = result["placed"][0]

    move = {"piece_id": piece["id"], "x": 4000, "y": 0, "z": 0, "dx": piece["dx"], "dy": piece["dy"], "dz": piece["dz"]}
    r = client.post("/api/apply-move", json=move)
    assert r.status_code == 200
    body = r.json()
    moved = next(p for p in body["placed"] if p["id"] == piece["id"])
    assert moved["x"] == 4000
    assert body["metrics"]["loaded_pieces"] == 1


def test_remove_piece_moves_to_unloaded_and_can_be_reinserted():
    result = _pack([_window(quantity=1)])
    piece = result["placed"][0]

    r = client.post("/api/remove-piece", json={"piece_id": piece["id"]})
    assert r.status_code == 200
    body = r.json()
    assert len(body["placed"]) == 0
    assert len(body["unloaded"]) == 1
    unloaded_item = body["unloaded"][0]
    assert unloaded_item["id"] == piece["id"]
    assert unloaded_item["reason"] == "Removido manualmente"

    r2 = client.post(
        "/api/insert-piece",
        json={
            "unloaded_id": unloaded_item["id"],
            "x": 0,
            "y": 0,
            "z": 0,
            "dx": piece["dx"],
            "dy": piece["dy"],
            "dz": piece["dz"],
        },
    )
    assert r2.status_code == 200
    body2 = r2.json()
    assert len(body2["placed"]) == 1
    assert len(body2["unloaded"]) == 0


def test_insert_rejects_glass_face_orientation():
    result = _pack([_window(quantity=1)])
    piece = result["placed"][0]
    client.post("/api/remove-piece", json={"piece_id": piece["id"]})

    r = client.post(
        "/api/insert-piece",
        json={"unloaded_id": piece["id"], "x": 0, "y": 0, "z": 0, "dx": 1200, "dy": 2000, "dz": 100},
    )
    assert r.status_code == 409
    assert "vidrio" in r.json()["detail"]


def test_undo_redo_move():
    result = _pack([_window(quantity=1)])
    piece = result["placed"][0]
    original = (piece["x"], piece["y"], piece["z"])

    move = {"piece_id": piece["id"], "x": 5000, "y": 0, "z": 0, "dx": piece["dx"], "dy": piece["dy"], "dz": piece["dz"]}
    r = client.post("/api/apply-move", json=move)
    assert r.status_code == 200

    r_undo = client.post("/api/undo")
    assert r_undo.status_code == 200
    reverted = next(p for p in r_undo.json()["placed"] if p["id"] == piece["id"])
    assert (reverted["x"], reverted["y"], reverted["z"]) == original

    r_redo = client.post("/api/redo")
    assert r_redo.status_code == 200
    redone = next(p for p in r_redo.json()["placed"] if p["id"] == piece["id"])
    assert redone["x"] == 5000
