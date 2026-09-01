"""
CUBOX 2.0 - Fase 3A.1: Dimensions3D como UNICA fuente de verdad fisica.

width/height/thickness (y source_*) ya no son campos almacenados: son
`@computed_field` de solo lectura derivados de dimensions/source_dimensions
(ver models/schemas.py:legacy_from_dimensions). Este archivo cubre los
Tests A-K especificados para esta fase; la regla critica de vidrio en si
sigue protegida por test_orientation.py."""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.orientation import get_valid_orientations, is_valid_orientation
from app.main import app
from app.models.schemas import Dimensions3D, ItemType, LoadItem, OrientationPolicy, WindowItem

client = TestClient(app)


# ---------------------------------------------------------------------------
# TEST A - input legacy (width/height/thickness) mapea a Dimensions3D.
# ---------------------------------------------------------------------------


def test_legacy_window_input_maps_to_canonical_dimensions():
    item = WindowItem(code="w", width=1200, height=2000, thickness=100, weight=45, quantity=1)
    assert item.dimensions == Dimensions3D(length=1200, width=100, height=2000)


# ---------------------------------------------------------------------------
# TEST B - input nativo de BOX: Dimensions3D exacto, sin transformacion.
# ---------------------------------------------------------------------------


def test_native_box_input_uses_dimensions_directly():
    item = LoadItem(
        code="b1", dimensions=Dimensions3D(length=600, width=400, height=300), weight=50, quantity=1,
        item_type=ItemType.BOX, orientation_policy=OrientationPolicy.FREE,
    )
    assert item.dimensions == Dimensions3D(length=600, width=400, height=300)


def test_native_box_input_accepts_dict_shape():
    """El shape {"dimensions": {"length":..,"width":..,"height":..}} (como
    llegaria por JSON) tambien funciona, sin pasar por dimensions_from_legacy."""
    item = LoadItem(
        code="b2", dimensions={"length": 600, "width": 400, "height": 300}, weight=20, quantity=1,
        item_type=ItemType.BOX,
    )
    assert item.dimensions == Dimensions3D(length=600, width=400, height=300)


# ---------------------------------------------------------------------------
# TEST C - input nativo de PALLET: dimensiones canonicas sin cambios.
# ---------------------------------------------------------------------------


def test_native_pallet_input_uses_dimensions_directly():
    item = LoadItem(
        code="p1", dimensions=Dimensions3D(length=1200, width=1000, height=150), weight=25, quantity=1,
        item_type=ItemType.PALLET,
    )
    assert item.dimensions == Dimensions3D(length=1200, width=1000, height=150)


# ---------------------------------------------------------------------------
# TEST D - output legacy: width/height/thickness derivados coinciden con el
# comportamiento anterior a esta fase.
# ---------------------------------------------------------------------------


def test_legacy_output_fields_match_expected_values():
    item = WindowItem(code="w", width=1200, height=2000, thickness=100, weight=45, quantity=1)

    assert (item.width, item.height, item.thickness) == (1200, 2000, 100)

    dumped = item.model_dump()
    assert dumped["width"] == 1200
    assert dumped["height"] == 2000
    assert dumped["thickness"] == 100
    assert dumped["dimensions"] == {"length": 1200, "width": 100, "height": 2000}


# ---------------------------------------------------------------------------
# TEST E - representaciones en conflicto se rechazan; equivalentes se aceptan.
# ---------------------------------------------------------------------------


def test_conflicting_legacy_and_generic_dimensions_rejected():
    with pytest.raises(ValidationError):
        WindowItem(
            code="w", width=1200, height=2000, thickness=100,
            dimensions=Dimensions3D(length=999, width=100, height=2000),
            weight=45, quantity=1,
        )


def test_equivalent_legacy_and_generic_dimensions_accepted():
    item = WindowItem(
        code="w", width=1200, height=2000, thickness=100,
        dimensions=Dimensions3D(length=1200, width=100, height=2000),
        weight=45, quantity=1,
    )
    assert item.dimensions == Dimensions3D(length=1200, width=100, height=2000)


def test_partial_legacy_fields_rejected():
    """width/height/thickness deben venir los 3 juntos o ninguno."""
    with pytest.raises(ValidationError):
        WindowItem(code="w", width=1200, height=2000, weight=45, quantity=1)


# ---------------------------------------------------------------------------
# TEST F - regresion de orientacion PANEL: las mismas 4 orientaciones,
# la cara de vidrio jamas es la base.
# ---------------------------------------------------------------------------


def test_panel_orientation_regression():
    item = WindowItem(code="w", width=1200, height=2000, thickness=100, weight=45, quantity=1)

    orientations = get_valid_orientations(item.dimensions, item.resolved_orientation_policy)
    assert len(orientations) == 4
    for o in orientations:
        assert o.dz != 100

    assert is_valid_orientation(item.dimensions, dx=1200, dy=100, dz=2000, policy=item.resolved_orientation_policy) is True
    assert is_valid_orientation(item.dimensions, dx=1200, dy=2000, dz=100, policy=item.resolved_orientation_policy) is False


# ---------------------------------------------------------------------------
# TEST G - BOX nativo, FREE: 6 orientaciones validas.
# ---------------------------------------------------------------------------


def test_native_box_free_has_six_orientations():
    dims = Dimensions3D(length=600, width=400, height=300)
    orientations = get_valid_orientations(dims, OrientationPolicy.FREE)

    assert len(orientations) == 6
    combos = {(o.dx, o.dy, o.dz) for o in orientations}
    assert len(combos) == 6
    for dx, dy, dz in combos:
        assert {dx, dy, dz} == {600, 400, 300}


# ---------------------------------------------------------------------------
# TEST H - BOX nativo, UPRIGHT: 2 rotaciones en el piso, height vertical.
# ---------------------------------------------------------------------------


def test_native_box_upright_orientations():
    dims = Dimensions3D(length=600, width=400, height=300)
    orientations = get_valid_orientations(dims, OrientationPolicy.UPRIGHT)

    assert len(orientations) == 2
    for o in orientations:
        assert o.dz == 300
        assert {o.dx, o.dy} == {600, 400}


# ---------------------------------------------------------------------------
# TEST I - Optimize Remaining: BOX nativo y PANEL legacy sobreviven pack +
# lock + Optimize Remaining sin drift dimensional.
# ---------------------------------------------------------------------------


def test_optimize_remaining_preserves_native_and_legacy_dimensions():
    panel_item = dict(code="PANEL1", width=1200, height=2000, thickness=100, weight=45, quantity=3)
    box_item = dict(
        code="BOX1", dimensions={"length": 600, "width": 400, "height": 300}, weight=20, quantity=3,
        item_type="box", orientation_policy="free",
    )

    r = client.post(
        "/api/pack",
        json={"items": [panel_item, box_item], "container_id": "40ft_standard", "optimization_mode": "best_space"},
    )
    assert r.status_code == 200
    best = r.json()["best"]
    assert best["metrics"]["loaded_pieces"] == 6

    box_piece = next(p for p in best["placed"] if p["code"] == "BOX1")
    assert box_piece["source_dimensions"] == {"length": 600, "width": 400, "height": 300}

    lock_resp = client.post("/api/lock-piece", json={"piece_id": box_piece["id"]})
    assert lock_resp.status_code == 200

    r2 = client.post("/api/optimize-remaining")
    assert r2.status_code == 200
    reoptimized = r2.json()["best"]
    assert reoptimized["metrics"]["loaded_pieces"] == 6

    for p in reoptimized["placed"]:
        if p["code"] == "PANEL1":
            assert p["item_type"] == "panel"
            assert p["dz"] != p["source_thickness"]
            assert p["source_dimensions"] == {"length": 1200, "width": 100, "height": 2000}
        else:
            assert p["item_type"] == "box"
            assert p["source_dimensions"] == {"length": 600, "width": 400, "height": 300}


# ---------------------------------------------------------------------------
# TEST J - edicion manual: un BOX nativo sobrevive insert/move/rotate
# usando las dimensiones canonicas de origen.
# ---------------------------------------------------------------------------


def test_manual_edit_native_box_survives_insert_move_rotate():
    box_item = dict(
        code="BOX1", dimensions={"length": 600, "width": 400, "height": 300}, weight=20, quantity=1,
        item_type="box", orientation_policy="free",
    )
    r = client.post("/api/pack", json={"items": [box_item], "container_id": "40ft_standard"})
    assert r.status_code == 200
    piece = r.json()["best"]["placed"][0]
    assert piece["source_dimensions"] == {"length": 600, "width": 400, "height": 300}

    remove_resp = client.post("/api/remove-piece", json={"piece_id": piece["id"]})
    assert remove_resp.status_code == 200
    unloaded = next(u for u in remove_resp.json()["unloaded"] if u["id"] == piece["id"])
    assert unloaded["dimensions"] == {"length": 600, "width": 400, "height": 300}

    insert_resp = client.post(
        "/api/insert-piece",
        json={
            "unloaded_id": unloaded["id"], "x": piece["x"], "y": piece["y"], "z": piece["z"],
            "dx": piece["dx"], "dy": piece["dy"], "dz": piece["dz"],
        },
    )
    assert insert_resp.status_code == 200
    reinserted = next(p for p in insert_resp.json()["placed"] if p["id"] == unloaded["id"])
    assert reinserted["source_dimensions"] == {"length": 600, "width": 400, "height": 300}

    rotate_resp = client.post("/api/rotate-piece", json={"piece_id": reinserted["id"]})
    assert rotate_resp.status_code == 200
    rotated = next(p for p in rotate_resp.json()["placed"] if p["id"] == reinserted["id"])
    assert {rotated["dx"], rotated["dy"], rotated["dz"]} == {600, 400, 300}
    assert rotated["source_dimensions"] == {"length": 600, "width": 400, "height": 300}


# ---------------------------------------------------------------------------
# TEST K - round trip de serializacion: canonico -> output -> reconstruccion
# produce exactamente las mismas dimensiones fisicas.
# ---------------------------------------------------------------------------


def test_serialization_round_trip_preserves_native_dimensions():
    original = LoadItem(
        code="rt", dimensions=Dimensions3D(length=777, width=333, height=555), weight=10, quantity=1,
        item_type=ItemType.BOX, orientation_policy=OrientationPolicy.FREE,
    )

    reconstructed = LoadItem(**original.model_dump())
    assert reconstructed.dimensions == original.dimensions

    reconstructed_from_json = LoadItem.model_validate_json(original.model_dump_json())
    assert reconstructed_from_json.dimensions == original.dimensions


def test_serialization_round_trip_preserves_legacy_window_dimensions():
    original = WindowItem(code="w", width=1200, height=2000, thickness=100, weight=45, quantity=1)

    reconstructed = WindowItem(**original.model_dump())
    assert reconstructed.dimensions == original.dimensions
    assert (reconstructed.width, reconstructed.height, reconstructed.thickness) == (1200, 2000, 100)
