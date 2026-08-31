"""Lock/Unlock (secciones 22-25 de V3): una pieza bloqueada no se puede mover,
rotar, girar ni quitar hasta desbloquearla."""

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
        quantity=2,
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


def test_lock_then_move_is_rejected():
    result = _pack([_window()])
    piece = result["placed"][0]

    r = client.post("/api/lock-piece", json={"piece_id": piece["id"]})
    assert r.status_code == 200
    locked = next(p for p in r.json()["placed"] if p["id"] == piece["id"])
    assert locked["locked"] is True

    move = {"piece_id": piece["id"], "x": 3000, "y": 0, "z": 0, "dx": piece["dx"], "dy": piece["dy"], "dz": piece["dz"]}
    r2 = client.post("/api/apply-move", json=move)
    assert r2.status_code == 409
    assert "bloqueada" in r2.json()["detail"]


def test_lock_then_rotate_turn_remove_are_rejected():
    result = _pack([_window(quantity=1)])
    piece = result["placed"][0]
    client.post("/api/lock-piece", json={"piece_id": piece["id"]})

    for endpoint in ["/api/rotate-piece", "/api/turn-piece", "/api/remove-piece"]:
        r = client.post(endpoint, json={"piece_id": piece["id"]})
        assert r.status_code == 409, f"{endpoint} deberia rechazar una pieza bloqueada"


def test_unlock_allows_move_again():
    result = _pack([_window(quantity=1)])
    piece = result["placed"][0]
    client.post("/api/lock-piece", json={"piece_id": piece["id"]})

    r = client.post("/api/unlock-piece", json={"piece_id": piece["id"]})
    assert r.status_code == 200
    unlocked = next(p for p in r.json()["placed"] if p["id"] == piece["id"])
    assert unlocked["locked"] is False

    move = {"piece_id": piece["id"], "x": 2000, "y": 0, "z": 0, "dx": piece["dx"], "dy": piece["dy"], "dz": piece["dz"]}
    r2 = client.post("/api/apply-move", json=move)
    assert r2.status_code == 200


def test_lock_unknown_piece_404():
    _pack([_window(quantity=1)])
    r = client.post("/api/lock-piece", json={"piece_id": "does-not-exist"})
    assert r.status_code == 404
