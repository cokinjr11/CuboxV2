"""
CUBOX 2.0 - Fase 3A: modelo generico de dimensiones (Dimensions3D).

Cubre los Tests A-J especificados para esta fase. El mapeo legacy ->
generico (dimensions_from_legacy) y la regla critica de vidrio en si ya
tienen cobertura dedicada en test_orientation.py -este archivo se enfoca en
que la NUEVA capa generica (Dimensions3D, item.dimensions/piece.
source_dimensions, PanelDimensionMapping) se integre correctamente en el
packer, la edicion manual y Optimize Remaining, para PANEL y para tipos
genericos (BOX/PALLET) por igual."""

from fastapi.testclient import TestClient

from app.core.orientation import get_valid_orientations, is_valid_orientation, toggle_orientation
from app.core.packer import pack_container
from app.main import app
from app.models.containers import get_container
from app.models.schemas import Dimensions3D, ItemType, OrientationPolicy, PlacedPiece, WindowItem, dimensions_from_legacy

client = TestClient(app)
CONTAINER = get_container("40ft_standard")


# ---------------------------------------------------------------------------
# TEST A - un item legacy (width/height/thickness) mapea correctamente a
# Dimensions3D.
# ---------------------------------------------------------------------------


def test_legacy_window_maps_into_generic_dimensions():
    item = WindowItem(code="w", width=1200, height=2000, thickness=100, weight=45, quantity=1)
    dims = item.dimensions

    assert isinstance(dims, Dimensions3D)
    assert dims.length == 1200  # legacy.width
    assert dims.width == 100  # legacy.thickness
    assert dims.height == 2000  # legacy.height


# ---------------------------------------------------------------------------
# TEST B - las orientaciones PANEL_EDGE_ONLY legacy no cambian: la cara de
# vidrio jamas es la base, ahora pasando por item.dimensions.
# ---------------------------------------------------------------------------


def test_legacy_panel_orientations_unchanged_via_generic_dimensions():
    item = WindowItem(code="w", width=1200, height=2000, thickness=100, weight=45, quantity=1)

    orientations = get_valid_orientations(item.dimensions, item.resolved_orientation_policy)
    assert len(orientations) == 4
    for o in orientations:
        assert o.dz != 100  # legacy.thickness jamas vertical

    assert is_valid_orientation(item.dimensions, dx=1200, dy=100, dz=2000, policy=item.resolved_orientation_policy) is True
    assert is_valid_orientation(item.dimensions, dx=1200, dy=2000, dz=100, policy=item.resolved_orientation_policy) is False


# ---------------------------------------------------------------------------
# TEST C - BOX generico (Length=600, Width=400, Height=300), FREE -> 6
# orientaciones.
# ---------------------------------------------------------------------------


def test_generic_box_free_has_six_orientations():
    dims = Dimensions3D(length=600, width=400, height=300)
    orientations = get_valid_orientations(dims, OrientationPolicy.FREE)

    assert len(orientations) == 6
    combos = {(o.dx, o.dy, o.dz) for o in orientations}
    assert len(combos) == 6
    for dx, dy, dz in combos:
        assert {dx, dy, dz} == {600, 400, 300}


# ---------------------------------------------------------------------------
# TEST D - mismas dimensiones, UPRIGHT: piso 600x400, Height=300 siempre
# vertical.
# ---------------------------------------------------------------------------


def test_generic_box_upright_orientations():
    dims = Dimensions3D(length=600, width=400, height=300)
    orientations = get_valid_orientations(dims, OrientationPolicy.UPRIGHT)

    assert len(orientations) == 2
    for o in orientations:
        assert o.dz == 300
        assert {o.dx, o.dy} == {600, 400}


# ---------------------------------------------------------------------------
# TEST E - PALLET 1200 x 1000 x 150, politica upright/floor-only: nunca
# parado de canto.
# ---------------------------------------------------------------------------


def test_generic_pallet_never_stands_vertically():
    dims = Dimensions3D(length=1200, width=1000, height=150)
    orientations = get_valid_orientations(dims, OrientationPolicy.UPRIGHT)

    assert len(orientations) == 2
    for o in orientations:
        assert o.dz == 150
        assert {o.dx, o.dy} == {1200, 1000}

    assert is_valid_orientation(dims, dx=150, dy=1000, dz=1200, policy=OrientationPolicy.UPRIGHT) is False
    assert is_valid_orientation(dims, dx=1000, dy=150, dz=1200, policy=OrientationPolicy.UPRIGHT) is False


# ---------------------------------------------------------------------------
# TEST F - el packer existente coloca items BOX genericos correctamente.
# ---------------------------------------------------------------------------


def test_packer_places_generic_box_items():
    item = WindowItem(
        code="BOX1", width=600, height=400, thickness=300, weight=50, quantity=5,
        item_type=ItemType.BOX, orientation_policy=OrientationPolicy.FREE,
    )
    result = pack_container([item], CONTAINER, strategy="highest_priority")

    assert len(result.placed) == 5
    for p in result.placed:
        assert p.item_type == ItemType.BOX
        assert is_valid_orientation(p.source_dimensions, p.dx, p.dy, p.dz, p.resolved_orientation_policy)


# ---------------------------------------------------------------------------
# TEST G - regresion: el dataset de ventanas/panel sigue siendo fisicamente
# valido y preserva la regla de la cara prohibida.
# ---------------------------------------------------------------------------


def test_panel_packer_regression_preserves_forbidden_face_rule():
    item = WindowItem(code="W1", width=1200, height=2000, thickness=100, weight=45, quantity=10)
    result = pack_container([item], CONTAINER, strategy="highest_priority")

    assert len(result.placed) == 10
    for p in result.placed:
        assert p.dz != p.source_thickness
        assert is_valid_orientation(p.source_dimensions, p.dx, p.dy, p.dz, p.resolved_orientation_policy)


# ---------------------------------------------------------------------------
# TEST H - rotacion manual: BOX generico y PANEL legacy usan cada uno el
# conjunto de orientaciones correcto.
# ---------------------------------------------------------------------------


def _placed(**overrides) -> PlacedPiece:
    defaults = dict(
        id="p", code="p", weight=45, stackable=True, priority=1,
        x=0, y=0, z=0, dx=1200, dy=100, dz=2000,
        orientation_label="P1-a", source_width=1200, source_height=2000, source_thickness=100,
    )
    defaults.update(overrides)
    return PlacedPiece(**defaults)


def test_manual_rotation_uses_correct_orientation_set_for_panel():
    panel_piece = _placed()  # PANEL legacy por defecto

    target = toggle_orientation(
        panel_piece.source_dimensions, panel_piece.dx, panel_piece.dy, panel_piece.dz, panel_piece.resolved_orientation_policy
    )

    assert target is not None
    assert target.dz != panel_piece.source_thickness  # sigue sin apoyarse en el vidrio
    assert is_valid_orientation(
        panel_piece.source_dimensions, target.dx, target.dy, target.dz, panel_piece.resolved_orientation_policy
    )


def test_manual_rotation_uses_correct_orientation_set_for_box():
    box_piece = _placed(
        id="b", code="b", weight=20, dx=600, dy=300, dz=400,
        source_width=600, source_height=400, source_thickness=300,
        item_type=ItemType.BOX, orientation_policy=OrientationPolicy.FREE,
    )

    target = toggle_orientation(
        box_piece.source_dimensions, box_piece.dx, box_piece.dy, box_piece.dz, box_piece.resolved_orientation_policy
    )

    assert target is not None
    assert {target.dx, target.dy, target.dz} == {600, 400, 300}
    assert is_valid_orientation(
        box_piece.source_dimensions, target.dx, target.dy, target.dz, box_piece.resolved_orientation_policy
    )


# ---------------------------------------------------------------------------
# TEST I - Optimize Remaining: al reconstruir BOX generico y PANEL legacy,
# su perfil semantico (item_type/orientation_policy) sobrevive.
# ---------------------------------------------------------------------------


def test_optimize_remaining_preserves_item_type_and_orientation_policy():
    panel_item = dict(code="PANEL1", width=1200, height=2000, thickness=100, weight=45, quantity=3)
    box_item = dict(
        code="BOX1", width=600, height=400, thickness=300, weight=20, quantity=3,
        item_type="box", orientation_policy="free",
    )

    r = client.post(
        "/api/pack",
        json={"items": [panel_item, box_item], "container_id": "40ft_standard", "optimization_mode": "best_space"},
    )
    assert r.status_code == 200
    assert r.json()["best"]["metrics"]["loaded_pieces"] == 6

    r = client.post("/api/optimize-remaining")
    assert r.status_code == 200
    reoptimized = r.json()["best"]
    assert reoptimized["metrics"]["loaded_pieces"] == 6

    by_code: dict[str, list[dict]] = {}
    for p in reoptimized["placed"]:
        by_code.setdefault(p["code"], []).append(p)

    for p in by_code["PANEL1"]:
        assert p["item_type"] == "panel"
        assert p["dz"] != p["source_thickness"]  # la regla de vidrio sigue vigente tras Optimize Remaining

    for p in by_code["BOX1"]:
        assert p["item_type"] == "box"
        assert p["orientation_policy"] == "free"


# ---------------------------------------------------------------------------
# TEST J - compatibilidad de API: un request legacy con width/height/
# thickness sigue siendo aceptado tal cual.
# ---------------------------------------------------------------------------


def test_legacy_api_request_with_width_height_thickness_still_works():
    r = client.post(
        "/api/pack",
        json={
            "items": [{"code": "W1", "width": 1200, "height": 2000, "thickness": 100, "weight": 45, "quantity": 3}],
            "container_id": "40ft_standard",
        },
    )

    assert r.status_code == 200
    assert r.json()["best"]["metrics"]["loaded_pieces"] == 3


# ---------------------------------------------------------------------------
# Sanidad adicional: dimensions_from_legacy es el unico punto de conversion,
# usado igual para PANEL y para tipos genericos.
# ---------------------------------------------------------------------------


def test_dimensions_from_legacy_is_a_fixed_positional_mapping():
    dims = dimensions_from_legacy(width=10, height=20, thickness=30)
    assert dims == Dimensions3D(length=10, width=30, height=20)
