from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _sample_item():
    return {
        "code": "W1",
        "description": "Ventana test",
        "width": 1200,
        "height": 2000,
        "thickness": 100,
        "weight": 45,
        "quantity": 5,
        "system": "SysA",
        "group": "G1",
        "stackable": True,
        "priority": 1,
    }


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200


def test_list_containers():
    r = client.get("/api/containers")
    assert r.status_code == 200
    ids = [c["id"] for c in r.json()]
    assert "20ft_standard" in ids
    assert "40ft_standard" in ids
    assert "40ft_high_cube" in ids


def test_pack_endpoint():
    r = client.post("/api/pack", json={"items": [_sample_item()], "container_id": "40ft_standard"})
    assert r.status_code == 200
    body = r.json()["best"]
    assert body["metrics"]["total_pieces"] == 5
    assert len(body["placed"]) >= 1


def test_pack_endpoint_populates_load_and_unload_sequence():
    r = client.post("/api/pack", json={"items": [_sample_item()], "container_id": "40ft_standard"})
    best = r.json()["best"]
    assert len(best["load_sequence"]) == best["metrics"]["loaded_pieces"]
    # Mismas piezas en ambas secuencias, sin duplicados. Desde V3, unload ya
    # no es necesariamente el reverso exacto de load (ver core/sequence.py:
    # ahora depende de DeliverySequence y de dependencias de soporte).
    assert sorted(best["load_sequence"]) == sorted(best["unload_sequence"])
    assert len(set(best["load_sequence"])) == len(best["load_sequence"])


def test_pack_unknown_container_404():
    r = client.post("/api/pack", json={"items": [_sample_item()], "container_id": "does-not-exist"})
    assert r.status_code == 404


def test_validate_move_rejects_forbidden_orientation():
    pack_resp = client.post("/api/pack", json={"items": [_sample_item()], "container_id": "40ft_standard"})
    piece = pack_resp.json()["best"]["placed"][0]

    move = {
        "piece_id": piece["id"],
        "x": 0,
        "y": 0,
        "z": 0,
        "dx": 1200,
        "dy": 2000,
        "dz": 100,
    }
    r = client.post("/api/validate-move", json=move)
    assert r.status_code == 200
    assert r.json()["valid"] is False
    assert "acostada" in r.json()["reason"]


def test_validate_move_accepts_valid_new_position():
    pack_resp = client.post("/api/pack", json={"items": [_sample_item()], "container_id": "40ft_standard"})
    piece = pack_resp.json()["best"]["placed"][0]

    move = {
        "piece_id": piece["id"],
        "x": 0,
        "y": 0,
        "z": 0,
        "dx": piece["dx"],
        "dy": piece["dy"],
        "dz": piece["dz"],
    }
    r = client.post("/api/validate-move", json=move)
    assert r.status_code == 200
    assert r.json()["valid"] is True
