"""CUBOX 2.0 - Fase 5C-FINAL: Tilt / Inclination Control.

Tilt es un caso especial de Panels & Fragile, PLAN-LEVEL ONLY (no hay
override de item, no hay columna de Excel):

    PANEL Hard Constraint (solo PANEL) -> Plan Tilt Settings -> System
    Default (allow_tilt=False)

`tilt_angle` es SIGNED: el rango real es [-max_tilt_angle, +max_tilt_angle].
La colision usa AABB como broad phase (geometry.py, sin cambios) + OBB/SAT
como narrow phase cuando alguna pieza esta inclinada (tilt_collision.py).

Fase 5C FINAL SIMPLIFICATION: un panel inclinado es valido si cumple
PANEL_EDGE_ONLY, abs(tilt_angle) <= Maximum Tilt del plan, cabe dentro del
Load Space, respeta el soporte de base normal (check_support, sin cambios) y
no penetra fisicamente ninguna otra pieza (precise_collision, OBB/SAT). NO
se exige que toque otro panel ni una pared -esa exigencia (introducida y
luego ampliada en 2 rondas previas de Fase 5C) resulto mas restrictiva que
el comportamiento de producto pretendido y fue removida por completo (ver
core/manual_move.py, core/packer.py, core/final_validation.py -ninguno
llama ya a check_lateral_support/check_wall_support, que tampoco existen
mas en core/tilt_collision.py).

Ver core/handling_rules.py:resolve_plan_tilt, core/orientation.py (apply_tilt,
candidatos +/- acotados), core/tilt_collision.py, core/packer.py,
core/manual_move.py:validate_tilt_change, core/final_validation.py.
"""

import io
import math

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook

from app.core.final_validation import validate_for_export
from app.core.geometry import Box, boxes_overlap, check_support
from app.core.handling_rules import resolve_effective_item, resolve_plan_tilt
from app.core.manual_move import validate_move, validate_placement, validate_tilt_change
from app.core.orientation import apply_tilt, get_valid_orientations, is_valid_orientation
from app.core.packer import pack_container
from app.core.tilt_collision import precise_collision
from app.main import app
from app.models.containers import build_custom_load_space
from app.models.schemas import (
    TILT_MAX_ANGLE_DEG,
    Dimensions3D,
    ItemType,
    LoadSpaceType,
    OrientationPolicy,
    PackingResult,
    PlacedPiece,
    PlanHandlingRules,
    WindowItem,
)

client = TestClient(app)

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _panel(**overrides):
    defaults = dict(code="PNL1", width=800, height=1200, thickness=80, weight=20, quantity=1, item_type=ItemType.PANEL)
    defaults.update(overrides)
    return WindowItem(**defaults)


def _placed_panel(**overrides) -> PlacedPiece:
    defaults = dict(
        id="P1", code="PNL1", weight=20, stackable=True, priority=0,
        x=0, y=0, z=0, dx=800, dy=80, dz=1200, orientation_label="P1-a",
        source_dimensions=Dimensions3D(length=800, width=80, height=1200),
        item_type=ItemType.PANEL, orientation_policy=OrientationPolicy.PANEL_EDGE_ONLY,
        allow_tilt=True, max_tilt_angle=20.0, tilt_angle=0.0, tilt_axis="y",
        base_dx=800.0, base_dy=80.0, base_dz=1200.0,
    )
    defaults.update(overrides)
    return PlacedPiece(**defaults)


def _build_workbook_bytes(headers: list[str], rows: list[list]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _template_headers(profile: str) -> list[str]:
    r = client.get(f"/api/import-template/{profile}")
    assert r.status_code == 200
    workbook = load_workbook(io.BytesIO(r.content))
    return [c.value for c in next(workbook["Items"].iter_rows(min_row=1, max_row=1))]


# ---------------------------------------------------------------------------
# Template (runtime): las columnas via la ruta HTTP REAL que usa el boton
# Download Template (GET /api/import-template/{profile}).
# ---------------------------------------------------------------------------


def test_panel_template_does_not_contain_tilt_columns():
    """Fase 5C-FINAL, seccion 3: Tilt NO es un campo de Excel -PLAN-LEVEL
    ONLY (Wizard -> Handling Rules)."""
    headers = _template_headers("panel")
    assert "Allow Tilt" not in headers
    assert "Max Tilt Angle" not in headers


def test_box_template_does_not_contain_tilt_columns():
    assert "Allow Tilt" not in _template_headers("box")
    assert "Max Tilt Angle" not in _template_headers("box")


def test_pallet_template_does_not_contain_tilt_columns():
    assert "Allow Tilt" not in _template_headers("pallet")
    assert "Max Tilt Angle" not in _template_headers("pallet")


def test_old_panel_excel_file_with_tilt_columns_still_imports_normally():
    """Un archivo de una version anterior (Fase 5C intermedia) que SI traia
    Allow Tilt/Max Tilt Angle debe seguir importando bien -esas columnas
    simplemente se ignoran (no estan en _COLUMN_ALIASES)."""
    content = _build_workbook_bytes(
        ["Code", "Quantity", "Width", "Height", "Thickness", "Weight", "Allow Tilt", "Max Tilt Angle"],
        [["W1", 3, 800, 1200, 80, 20, "Yes", 12]],
    )
    r = client.post("/api/import-items-excel", files={"file": ("old.xlsx", content, XLSX_MIME)}, data={"profile": "panel"})
    assert r.status_code == 200
    preview = r.json()
    assert preview["is_valid"] is True
    assert len(preview["items"]) == 1
    assert preview["items"][0]["allow_tilt"] is False  # ignorado, no viene de Excel
    assert preview["items"][0]["max_tilt_angle"] is None


def test_panel_import_never_produces_item_level_tilt_fields():
    content = _build_workbook_bytes(
        ["Code", "Quantity", "Width", "Height", "Thickness", "Weight"], [["W1", 1, 800, 1200, 80, 20]]
    )
    r = client.post("/api/import-items-excel", files={"file": ("f.xlsx", content, XLSX_MIME)}, data={"profile": "panel"})
    assert r.status_code == 200
    item = r.json()["items"][0]
    assert "allow_tilt_override" not in item
    assert "max_tilt_angle_override" not in item


# ---------------------------------------------------------------------------
# Rules: resolve_plan_tilt -PLAN-LEVEL ONLY, PANEL Hard Constraint gate,
# System Default False/0.
# ---------------------------------------------------------------------------


def test_resolve_plan_tilt_disabled_by_default():
    assert resolve_plan_tilt(ItemType.PANEL, None) == (False, 0.0)


def test_resolve_plan_tilt_disabled_when_plan_says_no():
    plan = PlanHandlingRules(default_allow_tilt=False, default_max_tilt_angle=20)
    assert resolve_plan_tilt(ItemType.PANEL, plan) == (False, 0.0)


def test_resolve_plan_tilt_enabled_by_plan():
    plan = PlanHandlingRules(default_allow_tilt=True, default_max_tilt_angle=20)
    assert resolve_plan_tilt(ItemType.PANEL, plan) == (True, 20.0)


def test_resolve_plan_tilt_only_applies_to_panel():
    plan = PlanHandlingRules(default_allow_tilt=True, default_max_tilt_angle=20)
    assert resolve_plan_tilt(ItemType.BOX, plan) == (False, 0.0)
    assert resolve_plan_tilt(ItemType.PALLET, plan) == (False, 0.0)
    assert resolve_plan_tilt(ItemType.CUSTOM, plan) == (False, 0.0)


def test_resolve_plan_tilt_no_plan_max_means_zero_even_if_allowed():
    plan = PlanHandlingRules(default_allow_tilt=True)
    assert resolve_plan_tilt(ItemType.PANEL, plan) == (True, 0.0)


def test_resolve_plan_tilt_clamps_to_safe_domain():
    plan = PlanHandlingRules.model_construct(default_allow_tilt=True, default_max_tilt_angle=999.0)
    assert resolve_plan_tilt(ItemType.PANEL, plan) == (True, TILT_MAX_ANGLE_DEG)


def test_resolve_effective_item_applies_plan_tilt():
    panel = _panel()
    plan = PlanHandlingRules(default_allow_tilt=True, default_max_tilt_angle=15)
    resolved = resolve_effective_item(panel, plan)
    assert resolved.allow_tilt is True
    assert resolved.max_tilt_angle == 15


def test_resolve_effective_item_box_never_gets_tilt():
    box = WindowItem(
        code="B1", dimensions=Dimensions3D(length=600, width=400, height=300), weight=25, quantity=1,
        item_type=ItemType.BOX,
    )
    plan = PlanHandlingRules(default_allow_tilt=True, default_max_tilt_angle=15)
    resolved = resolve_effective_item(box, plan)
    assert resolved.allow_tilt is False
    assert resolved.max_tilt_angle == 0.0


def test_load_item_has_no_item_level_tilt_override_fields():
    """Fase 5C-FINAL, seccion 3: Tilt es PLAN-LEVEL ONLY -no existe override
    de item (a diferencia de Stackable/Orientation)."""
    assert "allow_tilt_override" not in WindowItem.model_fields
    assert "max_tilt_angle_override" not in WindowItem.model_fields


def test_panel_edge_only_remains_mandatory_with_tilt_enabled():
    panel = _panel(orientation_override=OrientationPolicy.FREE)
    plan = PlanHandlingRules(
        default_allow_tilt=True, default_max_tilt_angle=20, default_orientation_policy=OrientationPolicy.FREE
    )
    resolved = resolve_effective_item(panel, plan)
    assert resolved.orientation_policy == OrientationPolicy.PANEL_EDGE_ONLY


def test_pack_endpoint_hard_constraint_survives_tilt_and_orientation_override():
    """Escenario E-equivalente del pedido 5C (seccion 15): ni un
    orientation_override explicito ni Tilt habilitado pueden aflojar
    PANEL_EDGE_ONLY."""
    r = client.post(
        "/api/pack",
        json={
            "items": [
                {
                    "code": "W1", "width": 800, "height": 1200, "thickness": 80, "weight": 20,
                    "quantity": 1, "item_type": "panel", "orientation_override": "free",
                }
            ],
            "container_id": "40ft_standard",
            "plan_handling_rules": {"default_allow_tilt": True, "default_max_tilt_angle": 20},
        },
    )
    assert r.status_code == 200
    assert r.json()["best"]["placed"][0]["orientation_policy"] == "panel_edge_only"


# ---------------------------------------------------------------------------
# orientation.py: apply_tilt SIGNED (envolvente simetrica), candidatos +/-
# acotados con un angulo intermedio, is_valid_orientation continuo.
# ---------------------------------------------------------------------------


def test_apply_tilt_positive_and_negative_give_the_same_envelope():
    """La envolvente AABB es simetrica -+M y -M inclinan hacia lados
    distintos pero ocupan la MISMA caja (seccion 6/7 del pedido: el signo
    determina la direccion, la magnitud determina la inclinacion)."""
    pos = apply_tilt(800, 80, 1200, "y", 20)
    neg = apply_tilt(800, 80, 1200, "y", -20)
    assert pos == pytest.approx(neg)


def test_apply_tilt_zero_is_a_no_op():
    assert apply_tilt(800, 80, 1200, "y", 0) == (800, 80, 1200)


def test_apply_tilt_known_30_degrees():
    dx, dy, dz = apply_tilt(800, 80, 1200, "y", 30)
    assert dx == pytest.approx(800)
    assert dy == pytest.approx(1200 * 0.5 + 80 * math.sqrt(3) / 2)
    assert dz == pytest.approx(1200 * math.sqrt(3) / 2 + 80 * 0.5)


def test_get_valid_orientations_without_tilt_returns_the_4_base_orientations():
    dims = Dimensions3D(length=800, width=80, height=1200)
    orientations = get_valid_orientations(dims, OrientationPolicy.PANEL_EDGE_ONLY)
    assert len(orientations) == 4
    assert all(o.tilt_angle == 0 for o in orientations)


def test_get_valid_orientations_zero_degrees_always_tried_first():
    dims = Dimensions3D(length=800, width=80, height=1200)
    orientations = get_valid_orientations(dims, OrientationPolicy.PANEL_EDGE_ONLY, allow_tilt=True, max_tilt_angle=20)
    assert all(o.tilt_angle == 0 for o in orientations[:4])


def test_get_valid_orientations_includes_both_positive_and_negative_candidates():
    dims = Dimensions3D(length=800, width=80, height=1200)
    orientations = get_valid_orientations(dims, OrientationPolicy.PANEL_EDGE_ONLY, allow_tilt=True, max_tilt_angle=20)
    angles = {o.tilt_angle for o in orientations}
    assert any(a > 0 for a in angles)
    assert any(a < 0 for a in angles)


def test_get_valid_orientations_includes_an_intermediate_magnitude_not_just_max():
    """Seccion 11 del pedido: no solo 0 y el maximo -debe incluir al menos
    un angulo intermedio (25/50/75/100% del maximo, seccion 10)."""
    dims = Dimensions3D(length=800, width=80, height=1200)
    orientations = get_valid_orientations(dims, OrientationPolicy.PANEL_EDGE_ONLY, allow_tilt=True, max_tilt_angle=20)
    magnitudes = sorted({round(abs(o.tilt_angle), 6) for o in orientations if o.tilt_angle != 0})
    assert magnitudes == [5.0, 10.0, 15.0, 20.0]


def test_get_valid_orientations_candidate_count_is_small_and_bounded():
    dims = Dimensions3D(length=800, width=80, height=1200)
    orientations = get_valid_orientations(dims, OrientationPolicy.PANEL_EDGE_ONLY, allow_tilt=True, max_tilt_angle=20)
    # 4 orientaciones base + (4 orientaciones * 2 signos * 4 fracciones) = 36 -acotado, deterministico.
    assert len(orientations) == 36


def test_is_valid_orientation_accepts_continuous_signed_magnitude():
    """Tilt MANUAL admite cualquier angulo continuo dentro del rango, no
    solo los candidatos discretos del packer automatico."""
    dims = Dimensions3D(length=800, width=80, height=1200)
    dx, dy, dz = apply_tilt(800, 80, 1200, "y", 7)
    assert is_valid_orientation(dims, dx, dy, dz, OrientationPolicy.PANEL_EDGE_ONLY, allow_tilt=True, max_tilt_angle=20)
    dx2, dy2, dz2 = apply_tilt(800, 80, 1200, "y", -7)
    assert is_valid_orientation(dims, dx2, dy2, dz2, OrientationPolicy.PANEL_EDGE_ONLY, allow_tilt=True, max_tilt_angle=20)


def test_is_valid_orientation_rejects_beyond_max():
    dims = Dimensions3D(length=800, width=80, height=1200)
    dx, dy, dz = apply_tilt(800, 80, 1200, "y", 25)
    assert not is_valid_orientation(dims, dx, dy, dz, OrientationPolicy.PANEL_EDGE_ONLY, allow_tilt=True, max_tilt_angle=20)


def test_is_valid_orientation_rejects_tilted_geometry_when_tilt_not_allowed():
    dims = Dimensions3D(length=800, width=80, height=1200)
    dx, dy, dz = apply_tilt(800, 80, 1200, "y", 10)
    assert not is_valid_orientation(dims, dx, dy, dz, OrientationPolicy.PANEL_EDGE_ONLY)


# ---------------------------------------------------------------------------
# Precise collision (AABB broad phase -> OBB/SAT narrow phase).
# he=(100,50,150) (base_dx=200,base_dy=100,base_dz=300), ambas piezas
# rotadas -45 grados sobre tilt_axis="y", separadas a lo largo de Y -valores
# verificados numericamente (busqueda + biseccion) antes de escribir el test.
# ---------------------------------------------------------------------------


def _big_tilted_box(box_id: str, y_center: float, angle_deg: float) -> Box:
    dx, dy, dz = apply_tilt(200.0, 100.0, 300.0, "y", angle_deg)
    return Box(
        id=box_id, x=0.0, y=y_center - dy / 2, z=0.0, dx=dx, dy=dy, dz=dz,
        tilt_angle=angle_deg, tilt_axis="y", base_dx=200.0, base_dy=100.0, base_dz=300.0, item_type=ItemType.PANEL,
    )


def test_scenario_m_aabb_overlap_without_real_intersection_is_not_a_collision():
    """Escenario M del pedido (regresion obligatoria): 2 paneles inclinados
    cuyas envolventes AABB se solapan pero cuya geometria fisica real no."""
    a = _big_tilted_box("A", 0.0, -45.0)
    b = _big_tilted_box("B", 145.0, -45.0)
    assert boxes_overlap(a, b) is True  # el broad phase SI ve solape
    assert precise_collision(a, b) is False  # el narrow phase lo descarta: no es real


def test_moving_closer_turns_the_same_pair_into_a_real_collision():
    a = _big_tilted_box("A", 0.0, -45.0)
    b = _big_tilted_box("B", 140.0, -45.0)
    assert precise_collision(a, b) is True


def test_precise_collision_matches_aabb_when_neither_piece_is_tilted():
    """Sin Tilt de ningun lado, el narrow phase ni se ejecuta -el broad
    phase (AABB) ya es exacto para cajas axis-aligned (performance)."""
    a = Box(id="A", x=0, y=0, z=0, dx=100, dy=100, dz=100)
    b = Box(id="B", x=50, y=0, z=0, dx=100, dy=100, dz=100)
    assert precise_collision(a, b) == boxes_overlap(a, b) == True  # noqa: E712
    c = Box(id="C", x=500, y=0, z=0, dx=100, dy=100, dz=100)
    assert precise_collision(a, c) == boxes_overlap(a, c) == False  # noqa: E712


# ---------------------------------------------------------------------------
# Fase 5C FINAL SIMPLIFICATION: un panel inclinado NO necesita tocar otro
# panel ni una pared para ser valido -solo base normal (check_support, sin
# cambios) + ninguna penetracion fisica real (precise_collision, sin
# cambios). Acceptance Tests A/B/C/D/E/F del pedido de simplificacion.
# ---------------------------------------------------------------------------


def _tilted_panel_box(box_id: str, y: float, tilt_angle: float, x: float = 0.0) -> Box:
    dx, dy, dz = apply_tilt(1200.0, 80.0, 1200.0, "y", tilt_angle)
    return Box(
        id=box_id, x=x, y=y, z=0.0, dx=dx, dy=dy, dz=dz,
        tilt_angle=tilt_angle, tilt_axis="y", base_dx=1200.0, base_dy=80.0, base_dz=1200.0, item_type=ItemType.PANEL,
    )


def test_isolated_tilted_panel_needs_no_neighbor_to_be_a_valid_placement():
    """Reemplaza el escenario J original (que exigia apoyo lateral): un
    panel inclinado y completamente aislado -sin ningun otro panel ni pared
    cerca- es una colocacion valida via validate_move (base_support +
    colision precisa, nada mas)."""
    space = build_custom_load_space("Roomy", LoadSpaceType.CUSTOM, 3000, 3000, 3000, 50000)
    dx, dy, dz = apply_tilt(800.0, 80.0, 1200.0, "y", 15.0)
    piece = _placed_panel(id="T", code="T", x=1000, y=1400, z=0, dx=dx, dy=dy, dz=dz, tilt_angle=15.0, max_tilt_angle=20.0)
    ok, reason = validate_move(piece, 1000.0, 1400.0, 0.0, dx, dy, dz, [], space)
    assert ok is True, reason


def _panel_piece_from_big_tilted_box(box_id: str, y_center: float, angle_deg: float) -> PlacedPiece:
    """Version PlacedPiece de _big_tilted_box (misma geometria 200x100x300),
    para pasar por validate_move en vez de solo precise_collision."""
    box = _big_tilted_box(box_id, y_center, angle_deg)
    return _placed_panel(
        id=box_id, code=box_id, x=box.x, y=box.y, z=box.z, dx=box.dx, dy=box.dy, dz=box.dz,
        tilt_angle=angle_deg, max_tilt_angle=45.0, base_dx=200.0, base_dy=100.0, base_dz=300.0,
        source_dimensions=Dimensions3D(length=200, width=100, height=300),
    )


def test_tilted_panels_side_by_side_with_aabb_overlap_but_no_real_intersection_is_valid():
    """Acceptance Test C: 2 paneles inclinados en arreglo "\\ \\" (misma
    pareja de test_scenario_m: envolventes AABB solapadas, geometria fisica
    real separada) siguen siendo una colocacion valida via validate_move
    -sin exigir ningun contacto entre ellas."""
    a = _panel_piece_from_big_tilted_box("A", 0.0, -45.0)
    b = _panel_piece_from_big_tilted_box("B", 145.0, -45.0)
    assert boxes_overlap(
        Box(id=a.id, x=a.x, y=a.y, z=a.z, dx=a.dx, dy=a.dy, dz=a.dz),
        Box(id=b.id, x=b.x, y=b.y, z=b.z, dx=b.dx, dy=b.dy, dz=b.dz),
    ) is True  # confirma que el escenario realmente ejercita AABB overlap

    space = build_custom_load_space("Roomy", LoadSpaceType.CUSTOM, 3000, 3000, 3000, 50000)
    ok, reason = validate_move(b, b.x, b.y, b.z, b.dx, b.dy, b.dz, [a], space)
    assert ok is True, reason


def test_touching_tilted_panels_are_valid_contact_not_collision():
    """Acceptance Test D: 2 caras fisicas reales de paneles inclinados que
    se TOCAN (sin penetrar) siguen siendo validas -contacto, no colision."""
    base_dx, base_dy, base_dz = 800.0, 80.0, 1200.0
    space = build_custom_load_space("Roomy", LoadSpaceType.CUSTOM, 3000, 3000, 3000, 50000)
    angle = 20.0
    dx, dy, dz = apply_tilt(base_dx, base_dy, base_dz, "y", angle)
    center_distance = _touching_center_distance_y(angle, angle, base_dx, base_dy, base_dz)
    a = _placed_panel(
        id="A", code="A", x=0, y=1000 - dy / 2, z=0, dx=dx, dy=dy, dz=dz,
        tilt_angle=angle, max_tilt_angle=30.0, base_dx=base_dx, base_dy=base_dy, base_dz=base_dz,
    )
    b_center_y = 1000 + center_distance
    b = _placed_panel(
        id="B", code="B", x=0, y=b_center_y - dy / 2, z=0, dx=dx, dy=dy, dz=dz,
        tilt_angle=angle, max_tilt_angle=30.0, base_dx=base_dx, base_dy=base_dy, base_dz=base_dz,
    )
    ok, reason = validate_move(b, b.x, b.y, b.z, b.dx, b.dy, b.dz, [a], space)
    assert ok is True, reason


def test_penetrating_tilted_panels_are_rejected():
    """Acceptance Test E: mover un panel inclinado DENTRO de otro (penetracion
    fisica real) sigue siendo invalido."""
    base_dx, base_dy, base_dz = 800.0, 80.0, 1200.0
    space = build_custom_load_space("Roomy", LoadSpaceType.CUSTOM, 3000, 3000, 3000, 50000)
    angle = 20.0
    dx, dy, dz = apply_tilt(base_dx, base_dy, base_dz, "y", angle)
    center_distance = _touching_center_distance_y(angle, angle, base_dx, base_dy, base_dz)
    a = _placed_panel(
        id="A", code="A", x=0, y=1000 - dy / 2, z=0, dx=dx, dy=dy, dz=dz,
        tilt_angle=angle, max_tilt_angle=30.0, base_dx=base_dx, base_dy=base_dy, base_dz=base_dz,
    )
    b_center_y = 1000 + center_distance - 10.0  # 10mm mas cerca: penetracion real
    b = _placed_panel(
        id="B", code="B", x=0, y=b_center_y - dy / 2, z=0, dx=dx, dy=dy, dz=dz,
        tilt_angle=angle, max_tilt_angle=30.0, base_dx=base_dx, base_dy=base_dy, base_dz=base_dz,
    )
    ok, reason = validate_move(b, b.x, b.y, b.z, b.dx, b.dy, b.dz, [a], space)
    assert ok is False
    assert "Colisiona" in reason


def test_tilted_panel_touching_a_wall_is_valid_crossing_it_is_rejected():
    """Acceptance Test F: tocar una pared del Load Space (sin cruzarla) es
    valido -sin ningun estado de soporte especial; dentro de sus limites es
    exactamente lo que ya hacia within_container (sin cambios). Cruzarla
    sigue siendo invalido."""
    space = build_custom_load_space("Roomy", LoadSpaceType.CUSTOM, 3000, 3000, 3000, 50000)
    dx, dy, dz = apply_tilt(800.0, 80.0, 1200.0, "y", 15.0)
    piece = _placed_panel(id="T", code="T", x=0, y=1400, z=0, dx=dx, dy=dy, dz=dz, tilt_angle=15.0, max_tilt_angle=20.0)

    touching_ok, touching_reason = validate_move(piece, 0.0, 0.0, 0.0, dx, dy, dz, [], space)  # y=0: toca la pared en Y=0
    assert touching_ok is True, touching_reason

    crossing_ok, crossing_reason = validate_move(piece, 0.0, -10.0, 0.0, dx, dy, dz, [], space)  # cruza Y=0
    assert crossing_ok is False
    assert "limites" in crossing_reason.lower()


# ---------------------------------------------------------------------------
# Automatic packing: 0 grados preferido; Tilt solo como fallback; candidatos
# +/- acotados; angulo intermedio descubierto automaticamente cuando es la
# UNICA opcion valida (con soporte lateral real de una pieza preplaced).
# ---------------------------------------------------------------------------


def test_scenario_a_tilt_disabled_behaves_exactly_like_before():
    r = client.post(
        "/api/pack",
        json={
            "items": [{"code": "W1", "width": 800, "height": 1200, "thickness": 80, "weight": 20, "quantity": 1, "item_type": "panel"}],
            "container_id": "40ft_standard",
            "plan_handling_rules": {"default_allow_tilt": False},
        },
    )
    assert r.status_code == 200
    piece = r.json()["best"]["placed"][0]
    assert piece["allow_tilt"] is False
    assert piece["tilt_angle"] == 0.0


def test_scenario_b_tilt_enabled_but_unnecessary_stays_at_zero():
    r = client.post(
        "/api/pack",
        json={
            "items": [{"code": "W1", "width": 800, "height": 1200, "thickness": 80, "weight": 20, "quantity": 1, "item_type": "panel"}],
            "container_id": "40ft_standard",
            "plan_handling_rules": {"default_allow_tilt": True, "default_max_tilt_angle": 20},
        },
    )
    assert r.status_code == 200
    piece = r.json()["best"]["placed"][0]
    assert piece["allow_tilt"] is True  # sigue disponible para Tilt manual
    assert piece["tilt_angle"] == 0.0  # pero el automatico prefirio 0


def test_scenario_f_zero_degrees_always_preferred_when_it_fits():
    space = build_custom_load_space("Roomy", LoadSpaceType.CUSTOM, 3000, 3000, 3000, 50000)
    item = WindowItem(
        code="PNL", width=800, height=1200, thickness=80, weight=20, quantity=1, item_type=ItemType.PANEL,
        allow_tilt=True, max_tilt_angle=20,
    )
    result = pack_container([item], space)
    assert len(result.placed) == 1
    assert result.placed[0].tilt_angle == 0.0


def test_scenario_j_isolated_panel_that_needs_tilt_is_placed_without_any_support():
    """Acceptance Test I del pedido de simplificacion final: SIN ninguna
    pieza preplaced ni pared cerca de proposito, un panel que solo cabe
    inclinado (altura del Load Space menor que su altura vertical a 0
    grados) se coloca de todos modos -Cubox ya NO exige que toque otro
    panel ni una pared para aceptar el candidato inclinado."""
    space = build_custom_load_space("Narrow", LoadSpaceType.CUSTOM, 2000, 3000, 795, 50000)
    item = WindowItem(
        code="PNL", width=800, height=1200, thickness=80, weight=20, quantity=1, item_type=ItemType.PANEL,
        allow_tilt=True, max_tilt_angle=20,
    )
    result = pack_container([item], space)
    assert len(result.placed) == 1
    assert len(result.unloaded) == 0
    assert result.placed[0].tilt_angle != 0.0


def test_scenario_i_automatic_packing_discovers_an_intermediate_tilt_angle():
    """Escenario I del pedido: 0 grados no entra (muy alto), el maximo (20)
    tampoco (probado indirectamente: el packer prueba 5/10 primero, ambos
    mas altos que 0 grados -ver docstring de _TILT_CANDIDATE_FRACTIONS-, y
    solo 15/20 caben en altura). Con una pieza vecina YA colocada (preplaced,
    la unica forma de darle apoyo lateral a la primera pieza que se coloca)
    alineada en X y tocando en Y, el packer descubre automaticamente que 15
    grados (75% del maximo, un angulo INTERMEDIO -ni 0 ni el maximo 20) es
    el primer candidato que cumple altura + ancho + apoyo lateral."""
    space = build_custom_load_space("Narrow", LoadSpaceType.CUSTOM, 2000, 400, 795, 50000)
    neighbor = PlacedPiece(
        id="NEIGHBOR", code="NEIGHBOR", weight=20, stackable=True, priority=0,
        x=0, y=0, z=0, dx=1200, dy=80, dz=1200, orientation_label="seed",
        source_dimensions=Dimensions3D(length=1200, width=80, height=1200),
        item_type=ItemType.PANEL, orientation_policy=OrientationPolicy.PANEL_EDGE_ONLY,
        allow_tilt=False, max_tilt_angle=0.0, tilt_angle=0.0, tilt_axis="y",
        base_dx=1200.0, base_dy=80.0, base_dz=1200.0,
    )
    item = WindowItem(
        code="PNL", width=800, height=1200, thickness=80, weight=20, quantity=1, item_type=ItemType.PANEL,
        allow_tilt=True, max_tilt_angle=20,
    )
    result = pack_container([item], space, preplaced=[neighbor])
    assert len(result.placed) == 2
    placed = next(p for p in result.placed if p.id != "NEIGHBOR")
    assert abs(placed.tilt_angle) == pytest.approx(15.0)
    assert placed.tilt_angle != 0.0
    assert abs(placed.tilt_angle) != 20.0  # no es el maximo: es el intermedio


# ---------------------------------------------------------------------------
# Manual Tilt (/api/set-tilt): SIGNED, abs(angle) <= max, colision/soporte
# precisos, boundary exacto, rechazo limpio.
# ---------------------------------------------------------------------------


_A_Y = 1000.0  # posicion Y de A a 0 grados, bien lejos de las paredes para poder crecer simetrico


def _neighbor_y_for_angle(angle_deg: float) -> float:
    """Un cambio de Tilt rota A alrededor de su propio CENTRO (ver
    manual_move.py:validate_tilt_change) -la envolvente crece SIMETRICA a
    ambos lados de ese centro, sin importar el signo. Por eso el vecino B
    tiene que estar posicionado EXACTAMENTE donde va a quedar el borde
    nuevo de A una vez inclinada (no donde esta A a 0 grados) para darle
    soporte lateral real sin colisionar -esta funcion precalcula esa
    posicion con la misma formula que usa el backend (apply_tilt)."""
    _, dy, _ = apply_tilt(800.0, 80.0, 1200.0, "y", angle_deg)
    a_center_y = _A_Y + 40.0
    if angle_deg >= 0:  # A se inclina hacia -Y: B va del lado -Y, tocando el borde nuevo
        return a_center_y - dy / 2 - 80.0
    return a_center_y + dy / 2  # A se inclina hacia +Y: B va del lado +Y


def _pack_and_position_pair_for_angle(angle_deg: float, max_tilt: float = 20.0):
    """2 paneles en un contenedor comodo: A queda fijo en (0, _A_Y, 0) a 0
    grados; B se reposiciona (via /api/apply-move) exactamente donde A
    necesita apoyo lateral para inclinarse a `angle_deg` -ver
    _neighbor_y_for_angle. Devuelve el id de A (la pieza que se va a
    inclinar)."""
    r = client.post(
        "/api/pack",
        json={
            "items": [{"code": "PNL", "width": 800, "height": 1200, "thickness": 80, "weight": 20, "quantity": 2, "item_type": "panel"}],
            "custom_load_space": {"name": "Roomy", "load_space_type": "custom", "length": 3000, "width": 3000, "height": 3000, "max_weight": 50000},
            "plan_handling_rules": {"default_allow_tilt": True, "default_max_tilt_angle": max_tilt},
        },
    )
    assert r.status_code == 200
    ids = [p["id"] for p in r.json()["best"]["placed"]]
    a_id, b_id = ids[0], ids[1]
    ra = client.post("/api/apply-move", json={"piece_id": a_id, "x": 0, "y": _A_Y, "z": 0, "dx": 800, "dy": 80, "dz": 1200})
    assert ra.status_code == 200
    b_y = _neighbor_y_for_angle(angle_deg)
    rb = client.post("/api/apply-move", json={"piece_id": b_id, "x": 0, "y": b_y, "z": 0, "dx": 800, "dy": 80, "dz": 1200})
    assert rb.status_code == 200
    return a_id


def test_scenario_c_manual_positive_tilt_is_accepted_and_signed():
    piece_id = _pack_and_position_pair_for_angle(10.0)
    r = client.post("/api/set-tilt", json={"piece_id": piece_id, "tilt_angle": 10})
    assert r.status_code == 200
    piece = next(p for p in r.json()["placed"] if p["id"] == piece_id)
    assert piece["tilt_angle"] == 10.0


def test_scenario_d_manual_negative_tilt_is_accepted_and_signed():
    piece_id = _pack_and_position_pair_for_angle(-10.0)
    r = client.post("/api/set-tilt", json={"piece_id": piece_id, "tilt_angle": -10})
    assert r.status_code == 200
    piece = next(p for p in r.json()["placed"] if p["id"] == piece_id)
    assert piece["tilt_angle"] == -10.0


def test_scenario_e_boundary_exact_max_accepted_beyond_max_rejected():
    piece_id = _pack_and_position_pair_for_angle(12.0, max_tilt=12.0)
    ok_pos = client.post("/api/set-tilt", json={"piece_id": piece_id, "tilt_angle": 12})
    assert ok_pos.status_code == 200
    bad_pos = client.post("/api/set-tilt", json={"piece_id": piece_id, "tilt_angle": 13})
    assert bad_pos.status_code in (409, 422)

    piece_id2 = _pack_and_position_pair_for_angle(-12.0, max_tilt=12.0)
    ok_neg = client.post("/api/set-tilt", json={"piece_id": piece_id2, "tilt_angle": -12})
    assert ok_neg.status_code == 200
    bad_neg = client.post("/api/set-tilt", json={"piece_id": piece_id2, "tilt_angle": -13})
    assert bad_neg.status_code in (409, 422)


def test_set_tilt_rejects_when_plan_disallows_tilt():
    piece_id = _pack_and_position_pair_for_angle(5.0, max_tilt=0.0)  # allow_tilt True pero max=0
    r = client.post("/api/set-tilt", json={"piece_id": piece_id, "tilt_angle": 5})
    assert r.status_code == 409


def test_validate_tilt_change_signed_range_directly():
    """Un panel completamente aislado (sin vecinos: ya no hace falta
    ninguno para darle soporte lateral) sigue respetando el rango SIGNED
    [-max, +max] -esa es la unica restriccion de angulo."""
    piece = _placed_panel(max_tilt_angle=15.0, y=_A_Y)
    space = build_custom_load_space("Roomy", LoadSpaceType.CUSTOM, 3000, 3000, 3000, 50000)
    ok, reason, dims = validate_tilt_change(piece, 15.0, [], space)
    assert ok is True, reason
    ok, reason, dims = validate_tilt_change(piece, -15.0, [], space)
    assert ok is True, reason
    ok, reason, dims = validate_tilt_change(piece, 15.001, [], space)
    assert ok is False
    ok, reason, dims = validate_tilt_change(piece, -15.001, [], space)
    assert ok is False


def test_acceptance_a_isolated_manual_tilt_chain_positive():
    """Acceptance Test A del pedido de simplificacion final: un panel
    aislado (espacio libre alrededor) acepta 0° -> +10° y luego +10° ->
    +20° sin necesitar ningun otro panel ni pared."""
    piece = _placed_panel(max_tilt_angle=20.0, y=_A_Y)
    space = build_custom_load_space("Roomy", LoadSpaceType.CUSTOM, 3000, 3000, 3000, 50000)
    ok, reason, dims = validate_tilt_change(piece, 10.0, [], space)
    assert ok is True, reason
    piece.tilt_angle = 10.0
    piece.x, piece.y, piece.z, piece.dx, piece.dy, piece.dz = dims
    ok, reason, dims = validate_tilt_change(piece, 20.0, [], space)
    assert ok is True, reason


def test_acceptance_b_isolated_manual_tilt_chain_negative():
    """Acceptance Test B: la misma cadena, en la direccion negativa
    -0° -> -10° -> -20°, sin crashear ni producir geometria invalida."""
    piece = _placed_panel(max_tilt_angle=20.0, y=_A_Y)
    space = build_custom_load_space("Roomy", LoadSpaceType.CUSTOM, 3000, 3000, 3000, 50000)
    ok, reason, dims = validate_tilt_change(piece, -10.0, [], space)
    assert ok is True, reason
    piece.tilt_angle = -10.0
    piece.x, piece.y, piece.z, piece.dx, piece.dy, piece.dz = dims
    ok, reason, dims = validate_tilt_change(piece, -20.0, [], space)
    assert ok is True, reason
    assert all(math.isfinite(v) for v in dims)


def test_manual_move_preserves_signed_tilt():
    """Seccion 29 del pedido: un movimiento manual normal NO debe resetear
    el Tilt a 0/AABB-vertical."""
    piece_id = _pack_and_position_pair_for_angle(-8.0)
    r = client.post("/api/set-tilt", json={"piece_id": piece_id, "tilt_angle": -8})
    assert r.status_code == 200
    r2 = client.post("/api/validate-move", json={"piece_id": piece_id, "x": 100, "y": 100, "z": 0, "dx": 800, "dy": 800, "dz": 800})
    # dx/dy/dz de un movimiento normal deben ser los MISMOS que la pieza ya tiene (tilt preservado),
    # asi que un dx/dy/dz distinto (vertical/AABB) para la misma pieza se rechaza como orientacion invalida.
    assert r2.json()["valid"] is False


def test_rotate_on_a_tilted_piece_is_rejected_cleanly():
    """Seccion 35 del pedido: comportamiento aceptado para esta fase -
    rechazo limpio (409), sin intentar re-resolver el eje de Tilt."""
    piece_id = _pack_and_position_pair_for_angle(10.0)
    r = client.post("/api/set-tilt", json={"piece_id": piece_id, "tilt_angle": 10})
    assert r.status_code == 200
    r2 = client.post("/api/rotate-piece", json={"piece_id": piece_id})
    assert r2.status_code == 409


# ---------------------------------------------------------------------------
# Final Validation: independiente, no confia en que el packer/manual move ya
# lo hicieron bien.
# ---------------------------------------------------------------------------


def test_final_validation_flags_signed_tilt_beyond_max():
    piece = _placed_panel(max_tilt_angle=10.0, tilt_angle=15.0)  # estado corrupto a proposito
    space = build_custom_load_space("Roomy", LoadSpaceType.CUSTOM, 3000, 3000, 3000, 50000)
    metrics_stub = {
        "total_pieces": 1, "loaded_pieces": 1, "unloaded_pieces": 0, "used_volume_pct": 0, "total_weight": 20,
        "weight_utilization_pct": 0, "floor_utilization_pct": 0, "container_floor_area": 0, "used_floor_area": 0,
        "max_payload": 50000, "number_of_groups": 0, "number_of_systems": 0, "weight_balance_pct": 100,
        "left_weight_kg": 0, "right_weight_kg": 0, "left_weight_pct": 0, "right_weight_pct": 0, "front_weight_kg": 0,
        "back_weight_kg": 0, "front_weight_pct": 0, "back_weight_pct": 0, "center_of_mass_x": 0, "center_of_mass_y": 0,
        "center_of_mass_z": 0,
    }
    state = PackingResult(container=space, placed=[piece], unloaded=[], metrics=metrics_stub)
    errors = validate_for_export(state, space)
    assert any("excede el maximo" in e.lower() or "orientacion invalida" in e.lower() for e in errors)


def test_final_validation_passes_for_a_valid_supported_tilted_pair():
    space = build_custom_load_space("Roomy", LoadSpaceType.CUSTOM, 3000, 3000, 3000, 50000)
    panel_dims = Dimensions3D(length=1200, width=80, height=1200)
    neighbor = _placed_panel(
        id="N", code="N", x=0, y=0, z=0, dx=1200, dy=80, dz=1200, allow_tilt=False, max_tilt_angle=0.0,
        tilt_angle=0.0, base_dx=1200.0, base_dy=80.0, base_dz=1200.0, source_dimensions=panel_dims,
    )
    dx, dy, dz = apply_tilt(1200.0, 80.0, 1200.0, "y", 15.0)
    tilted = _placed_panel(
        id="T", code="T", x=0, y=80, z=0, dx=dx, dy=dy, dz=dz, tilt_angle=15.0,
        base_dx=1200.0, base_dy=80.0, base_dz=1200.0, max_tilt_angle=20.0, source_dimensions=panel_dims,
    )
    metrics_stub = {
        "total_pieces": 2, "loaded_pieces": 2, "unloaded_pieces": 0, "used_volume_pct": 0, "total_weight": 40,
        "weight_utilization_pct": 0, "floor_utilization_pct": 0, "container_floor_area": 0, "used_floor_area": 0,
        "max_payload": 50000, "number_of_groups": 0, "number_of_systems": 0, "weight_balance_pct": 100,
        "left_weight_kg": 0, "right_weight_kg": 0, "left_weight_pct": 0, "right_weight_pct": 0, "front_weight_kg": 0,
        "back_weight_kg": 0, "front_weight_pct": 0, "back_weight_pct": 0, "center_of_mass_x": 0, "center_of_mass_y": 0,
        "center_of_mass_z": 0,
    }
    state = PackingResult(container=space, placed=[neighbor, tilted], unloaded=[], metrics=metrics_stub)
    errors = validate_for_export(state, space)
    assert errors == []


def test_final_validation_passes_for_an_isolated_tilted_panel():
    """Reemplaza la expectativa original (rechazo por falta de soporte
    lateral): un panel inclinado, completamente aislado (sin ningun otro
    panel ni pared cerca), es una colocacion valida para Final
    Validation/export -PANEL_EDGE_ONLY, rango firmado, containment,
    soporte de base y sin penetracion, nada mas."""
    space = build_custom_load_space("Roomy", LoadSpaceType.CUSTOM, 3000, 3000, 3000, 50000)
    dx, dy, dz = apply_tilt(1200.0, 80.0, 1200.0, "y", 15.0)
    isolated = _placed_panel(
        id="T", code="T", x=0, y=1400, z=0, dx=dx, dy=dy, dz=dz, tilt_angle=15.0, max_tilt_angle=20.0,
        base_dx=1200.0, base_dy=80.0, base_dz=1200.0, source_dimensions=Dimensions3D(length=1200, width=80, height=1200),
    )
    state = PackingResult(container=space, placed=[isolated], unloaded=[], metrics=_metrics_stub())
    errors = validate_for_export(state, space)
    assert errors == []


# ---------------------------------------------------------------------------
# Reports: el signo del Tilt NUNCA se descarta.
# ---------------------------------------------------------------------------


def test_excel_export_reports_signed_tilt():
    from app.core.excel_export import _format_signed_tilt

    assert _format_signed_tilt(12.0) == "+12°"
    assert _format_signed_tilt(-12.0) == "-12°"
    assert _format_signed_tilt(0.0) == ""


def test_pdf_export_reports_signed_tilt():
    from app.core.pdf_export import _format_signed_tilt

    assert _format_signed_tilt(8.0) == "+8°"
    assert _format_signed_tilt(-8.0) == "-8°"


# ---------------------------------------------------------------------------
# Regresion: comportamiento existente sin tocar.
# ---------------------------------------------------------------------------


def test_zero_tilt_backward_compatibility_pack_result_unaffected():
    """Un plan que nunca menciona Tilt se comporta identico a antes de que
    el concepto existiera (seccion 39, Escenario A del pedido)."""
    r = client.post(
        "/api/pack",
        json={
            "items": [{"code": "W1", "width": 800, "height": 1200, "thickness": 80, "weight": 20, "quantity": 1, "item_type": "panel"}],
            "container_id": "40ft_standard",
        },
    )
    assert r.status_code == 200
    piece = r.json()["best"]["placed"][0]
    assert piece["allow_tilt"] is False
    assert piece["tilt_angle"] == 0.0
    assert piece["orientation_policy"] == "panel_edge_only"


# ---------------------------------------------------------------------------
# Runtime bug fixes: Negative Tilt (Bug 2 del pedido) -auditoria completa de
# signo en toda la matematica de Tilt: la ENVOLVENTE (magnitud, AABB) usa
# abs(angulo); la geometria FISICA real (OBB/SAT, ejes de rotacion) usa el
# angulo SIGNED. Ningun angulo negativo debe producir NaN/Infinity/dimension
# negativa.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("angle", [1, -1, 5, -5, 10, -10, 20, -20, 29, -29, 30, -30])
def test_apply_tilt_never_produces_nan_infinity_or_negative_dimensions(angle):
    dx, dy, dz = apply_tilt(800.0, 80.0, 1200.0, "y", angle)
    for value in (dx, dy, dz):
        assert math.isfinite(value)
        assert value > 0


@pytest.mark.parametrize("magnitude", [1, 5, 10, 20, 29, 30])
def test_apply_tilt_symmetric_magnitude_for_positive_and_negative(magnitude):
    """Seccion 3 del pedido de bugfix: para la MISMA magnitud, +angulo y
    -angulo deben dar la envolvente EXACTAMENTE igual (solo la geometria
    FISICA -OBB/SAT, direccion de rotacion- cambia con el signo)."""
    pos = apply_tilt(800.0, 80.0, 1200.0, "y", magnitude)
    neg = apply_tilt(800.0, 80.0, 1200.0, "y", -magnitude)
    assert pos == pytest.approx(neg)


def test_signed_tilt_axes_for_tilt_are_valid_rotation_matrices():
    """axes_for_tilt (tilt_collision.py) usa el angulo SIGNED (no abs) para
    la geometria fisica real -a diferencia de apply_tilt. Los 3 ejes deben
    seguir siendo ortonormales para cualquier signo."""
    from app.core.tilt_collision import axes_for_tilt

    for angle in (1, -1, 15, -15, 30, -30):
        axes = axes_for_tilt("y", angle)
        for axis in axes:
            assert math.isfinite(axis[0]) and math.isfinite(axis[1]) and math.isfinite(axis[2])
            norm = math.sqrt(sum(c * c for c in axis))
            assert norm == pytest.approx(1.0)
        # ortogonalidad entre pares de ejes
        for i in range(3):
            for j in range(i + 1, 3):
                dot = sum(axes[i][k] * axes[j][k] for k in range(3))
                assert dot == pytest.approx(0.0, abs=1e-9)


def _live_pack_two_adjacent(max_tilt=30.0):
    """2 paneles side-by-side en un contenedor angosto (clearance=0 por
    defecto), para tests end-to-end de signo via /api/set-tilt."""
    r = client.post(
        "/api/pack",
        json={
            "items": [{"code": "PNL", "width": 800, "height": 1200, "thickness": 80, "weight": 20, "quantity": 2, "item_type": "panel"}],
            "custom_load_space": {"name": "Pair", "load_space_type": "custom", "length": 1000, "width": 200, "height": 1300, "max_weight": 5000},
            "plan_handling_rules": {"default_allow_tilt": True, "default_max_tilt_angle": max_tilt},
        },
    )
    assert r.status_code == 200
    placed = sorted(r.json()["best"]["placed"], key=lambda p: p["y"])
    return placed[0]["id"], placed[1]["id"]  # (lower_y_id, higher_y_id)


@pytest.mark.parametrize("angle", [1, -1, 10, -10])
def test_manual_set_tilt_small_and_large_signed_angles_do_not_crash(angle):
    """Regresion directa del Bug 2: +1/-1 grados (el primer click de la UI)
    deben responder con 200 o 409 -NUNCA un 500, NaN, ni un error de
    serializacion. Reproduce contra el endpoint HTTP real."""
    lower, higher = _live_pack_two_adjacent()
    piece_id = higher if angle >= 0 else lower
    r = client.post("/api/set-tilt", json={"piece_id": piece_id, "tilt_angle": angle})
    assert r.status_code in (200, 409)
    if r.status_code == 200:
        piece = next(p for p in r.json()["placed"] if p["id"] == piece_id)
        assert piece["tilt_angle"] == angle
        assert all(math.isfinite(piece[k]) for k in ("dx", "dy", "dz", "x", "y", "z"))


def test_set_tilt_request_rejects_out_of_domain_angle_without_crashing():
    """El propio 422 de Pydantic (angulo fuera de [-30, 30]) debe seguir
    siendo un error HTTP normal -la regresion real del Bug 2 vivia en el
    FRONTEND (renderizar el `detail` como children de React), no aca; este
    test solo confirma que el backend responde con una lista de errores
    bien formada, consumible por un cliente correcto."""
    r = client.post("/api/set-tilt", json={"piece_id": "whatever", "tilt_angle": -999})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert isinstance(detail, list)
    assert all("msg" in item for item in detail)


# ---------------------------------------------------------------------------
# Runtime bug fixes: Panel contact (Bug 3 del pedido) -AABB overlap vs SAT
# separation, contacto exacto, penetracion real, para las 4 combinaciones de
# signo pedidas explicitamente: +/+ , -/-, +/-, tilted-vs-vertical.
# ---------------------------------------------------------------------------


_PAIR_BASE_DX, _PAIR_BASE_DY, _PAIR_BASE_DZ = 200.0, 100.0, 300.0


def _touching_center_distance_y(
    angle_a: float,
    angle_b: float,
    base_dx: float = _PAIR_BASE_DX,
    base_dy: float = _PAIR_BASE_DY,
    base_dz: float = _PAIR_BASE_DZ,
) -> float:
    """Distancia mundo (centro a centro, solo eje Y) a la que 2 OBB fisicas
    reales (mismas dimensiones base, angulos angle_a/angle_b) quedan en
    contacto EXACTO. NO asume una formula cerrada -que solo es valida para
    el caso "tilted vs vertical" y da falsos positivos para pares del MISMO
    angulo (2 paneles paralelos: la envolvente AABB se toca mucho antes de
    que las caras fisicas reales lo hagan, ver test_scenario_m ya existente
    en este archivo). En su lugar, encuentra el limite exacto por busqueda
    binaria sobre obb_overlap (SAT real, la misma funcion de produccion),
    lo cual generaliza correctamente a cualquier combinacion de signos."""
    from app.core.tilt_collision import OrientedBox, axes_for_tilt, obb_overlap

    half = (base_dx / 2.0, base_dy / 2.0, base_dz / 2.0)
    axes_a = axes_for_tilt("y", angle_a)
    axes_b = axes_for_tilt("y", angle_b)

    def overlapping_at(distance: float) -> bool:
        box_a = OrientedBox(center=(0.0, 0.0, 0.0), axes=axes_a, half_extents=half)
        box_b = OrientedBox(center=(0.0, distance, 0.0), axes=axes_b, half_extents=half)
        return obb_overlap(box_a, box_b, tol=1e-9)

    assert overlapping_at(0.0)
    hi = base_dy + base_dz  # cota superior: seguro ya separados
    assert not overlapping_at(hi)
    lo = 0.0
    for _ in range(60):
        mid = (lo + hi) / 2.0
        if overlapping_at(mid):
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def _obb_pair_boxes(angle_a, angle_b, y_gap_from_touch=0.0):
    """2 Box grandes (base_dx=200, base_dy=100, base_dz=300) tilt_axis="y",
    centradas en Y separadas por la distancia EXACTA de contacto fisico real
    (via _touching_center_distance_y, no una formula de envolvente) mas
    `y_gap_from_touch` (positivo = separadas, negativo = penetrando)."""
    dx_a, dy_a, dz_a = apply_tilt(_PAIR_BASE_DX, _PAIR_BASE_DY, _PAIR_BASE_DZ, "y", angle_a)
    dx_b, dy_b, dz_b = apply_tilt(_PAIR_BASE_DX, _PAIR_BASE_DY, _PAIR_BASE_DZ, "y", angle_b)
    center_distance = _touching_center_distance_y(angle_a, angle_b) + y_gap_from_touch
    a = Box(id="A", x=0, y=-dy_a / 2, z=0, dx=dx_a, dy=dy_a, dz=dz_a, tilt_angle=angle_a, tilt_axis="y", base_dx=_PAIR_BASE_DX, base_dy=_PAIR_BASE_DY, base_dz=_PAIR_BASE_DZ, item_type=ItemType.PANEL)
    b = Box(id="B", x=0, y=center_distance - dy_b / 2, z=0, dx=dx_b, dy=dy_b, dz=dz_b, tilt_angle=angle_b, tilt_axis="y", base_dx=_PAIR_BASE_DX, base_dy=_PAIR_BASE_DY, base_dz=_PAIR_BASE_DZ, item_type=ItemType.PANEL)
    return a, b


@pytest.mark.parametrize(
    "angle_a,angle_b,label",
    [
        (20, 20, "+theta vs +theta"),
        (-20, -20, "-theta vs -theta"),
        (20, -20, "+theta vs -theta"),
        (20, 0, "tilted vs vertical"),
    ],
)
def test_obb_sat_exact_contact_is_valid_for_all_sign_combinations(angle_a, angle_b, label):
    """Seccion 8 del pedido de bugfix: las 4 combinaciones de signo deben
    aceptar el contacto EXACTO (gap=0) como VALIDO -sin penetracion real."""
    a, b = _obb_pair_boxes(angle_a, angle_b, y_gap_from_touch=0.0)
    assert precise_collision(a, b) is False, f"{label}: contacto exacto no deberia ser colision"


@pytest.mark.parametrize(
    "angle_a,angle_b,label",
    [
        (20, 20, "+theta vs +theta"),
        (-20, -20, "-theta vs -theta"),
        (20, -20, "+theta vs -theta"),
        (20, 0, "tilted vs vertical"),
    ],
)
def test_obb_sat_real_penetration_is_invalid_for_all_sign_combinations(angle_a, angle_b, label):
    """Misma pareja, movida 10mm hacia adentro (penetracion real) -debe
    rechazarse para las 4 combinaciones de signo."""
    a, b = _obb_pair_boxes(angle_a, angle_b, y_gap_from_touch=-10.0)
    assert precise_collision(a, b) is True, f"{label}: penetracion real deberia detectarse"


def test_obb_sat_small_separation_is_valid_contact_not_collision():
    """Separacion pequena (5mm, dentro de lo que séria un ajuste de
    posicionamiento real) tras el contacto exacto -sigue siendo valido, no
    colision."""
    a, b = _obb_pair_boxes(15, 15, y_gap_from_touch=5.0)
    assert precise_collision(a, b) is False


def test_manual_move_uses_narrow_phase_not_aabb_broad_phase():
    """Seccion 2 del pedido de bugfix: NINGUN caller debe rechazar solo por
    AABB overlap antes de la fase estrecha -incluyendo Manual Move
    (validate_move en manual_move.py, la funcion que respalda
    /api/apply-move). Dos paneles YA inclinados +20/+20 (paralelos) cuyas
    AABB (envolvente axis-aligned) SI se superponen -ver el mismo patron
    que test_scenario_m_aabb_overlap_without_real_intersection_is_not_a_collision,
    ya establecido en este archivo- pero cuyos OBB reales (SAT) estan
    exactamente en contacto -sin penetracion- deben poder moverse a esa
    posicion; movidos 10mm mas cerca (penetracion fisica real) deben
    rechazarse."""
    base_dx, base_dy, base_dz = 800.0, 80.0, 1200.0
    space = build_custom_load_space("Roomy", LoadSpaceType.CUSTOM, 3000, 3000, 3000, 50000)
    angle = 20.0
    dx, dy, dz = apply_tilt(base_dx, base_dy, base_dz, "y", angle)
    center_distance = _touching_center_distance_y(angle, angle, base_dx, base_dy, base_dz)

    a = _placed_panel(
        id="A", code="A", x=0, y=1000 - dy / 2, z=0, dx=dx, dy=dy, dz=dz,
        tilt_angle=angle, max_tilt_angle=30.0,
        base_dx=base_dx, base_dy=base_dy, base_dz=base_dz,
    )

    def _b_at(gap: float) -> PlacedPiece:
        b_center_y = 1000 + center_distance + gap
        return _placed_panel(
            id="B", code="B", x=0, y=b_center_y - dy / 2, z=0, dx=dx, dy=dy, dz=dz,
            tilt_angle=angle, max_tilt_angle=30.0,
            base_dx=base_dx, base_dy=base_dy, base_dz=base_dz,
        )

    b_touch = _b_at(0.0)
    aabb_a = Box(id=a.id, x=a.x, y=a.y, z=a.z, dx=a.dx, dy=a.dy, dz=a.dz)
    aabb_b_touch = Box(id=b_touch.id, x=b_touch.x, y=b_touch.y, z=b_touch.z, dx=b_touch.dx, dy=b_touch.dy, dz=b_touch.dz)
    assert boxes_overlap(aabb_a, aabb_b_touch), "el escenario debe ejercitar AABB overlap real (no solo separacion) para probar que el narrow phase es lo que realmente decide"

    ok_touch, reason_touch = validate_move(b_touch, b_touch.x, b_touch.y, b_touch.z, b_touch.dx, b_touch.dy, b_touch.dz, [a], space)
    assert ok_touch is True, reason_touch

    b_penetrate = _b_at(-10.0)
    ok_pen, reason_pen = validate_move(b_penetrate, b_penetrate.x, b_penetrate.y, b_penetrate.z, b_penetrate.dx, b_penetrate.dy, b_penetrate.dz, [a], space)
    assert ok_pen is False
    assert "Colisiona" in reason_pen


# ---------------------------------------------------------------------------
# Fase 5C FINAL SIMPLIFICATION: la exigencia de soporte lateral obligatorio
# (de otro panel o de una pared del Load Space, introducida y luego ampliada
# en 2 rondas previas de Fase 5C) fue removida por completo -resulto mas
# restrictiva que el comportamiento de producto pretendido. Ya no existen
# check_lateral_support/check_wall_support/check_lateral_or_wall_support en
# core/tilt_collision.py, ni el campo PlacedPiece.tilt_lateral_support_ok.
# Regression tests: PANEL_EDGE_ONLY, preferencia de 0 grados y penetracion
# fisica real siguen intactas.
# ---------------------------------------------------------------------------


def _metrics_stub(total_weight: float = 20.0, total_pieces: int = 1) -> dict:
    return {
        "total_pieces": total_pieces, "loaded_pieces": total_pieces, "unloaded_pieces": 0, "used_volume_pct": 0,
        "total_weight": total_weight, "weight_utilization_pct": 0, "floor_utilization_pct": 0, "container_floor_area": 0,
        "used_floor_area": 0, "max_payload": 50000, "number_of_groups": 0, "number_of_systems": 0,
        "weight_balance_pct": 100, "left_weight_kg": 0, "right_weight_kg": 0, "left_weight_pct": 0,
        "right_weight_pct": 0, "front_weight_kg": 0, "back_weight_kg": 0, "front_weight_pct": 0, "back_weight_pct": 0,
        "center_of_mass_x": 0, "center_of_mass_y": 0, "center_of_mass_z": 0,
    }


def test_acceptance_i_automatic_tilt_fallback_needs_no_support():
    """Acceptance Test I: un panel que no cabe a 0 grados pero si a un
    angulo legal de Tilt se coloca automaticamente -sin necesitar ningun
    panel ni pared cerca."""
    space = build_custom_load_space("Narrow", LoadSpaceType.CUSTOM, 2000, 3000, 795, 50000)
    item = WindowItem(
        code="PNL", width=800, height=1200, thickness=80, weight=20, quantity=1, item_type=ItemType.PANEL,
        allow_tilt=True, max_tilt_angle=20,
    )
    result = pack_container([item], space)
    assert len(result.placed) == 1
    assert len(result.unloaded) == 0
    assert result.placed[0].tilt_angle != 0.0


def test_regression_panel_edge_only_still_enforced_for_tilted_pieces():
    """El removimiento del soporte lateral obligatorio no debilita
    PANEL_EDGE_ONLY: una orientacion invalida (acostada sobre su cara
    grande) sigue siendo invalida sin importar Tilt."""
    space = build_custom_load_space("Roomy", LoadSpaceType.CUSTOM, 3000, 3000, 3000, 50000)
    corrupted = _placed_panel(id="T", code="T", x=0, y=0, z=0, dx=800, dy=1200, dz=80, tilt_angle=0.0, allow_tilt=False, max_tilt_angle=0.0)
    state = PackingResult(container=space, placed=[corrupted], unloaded=[], metrics=_metrics_stub())
    errors = validate_for_export(state, space)
    assert any("orientacion" in e.lower() for e in errors)


def test_regression_automatic_packing_still_prefers_zero_degrees():
    """Acceptance Test H: con un plan normal donde los paneles caben de
    pie, el packer automatico los deja a 0 grados aunque Tilt este
    habilitado -nunca inclina sin necesidad."""
    space = build_custom_load_space("Roomy", LoadSpaceType.CUSTOM, 3000, 3000, 3000, 50000)
    item = WindowItem(
        code="PNL", width=800, height=1200, thickness=80, weight=20, quantity=1, item_type=ItemType.PANEL,
        allow_tilt=True, max_tilt_angle=20,
    )
    result = pack_container([item], space)
    assert len(result.placed) == 1
    assert result.placed[0].tilt_angle == 0.0


def test_regression_real_penetration_still_rejected_by_final_validation():
    """La colision precisa (OBB/SAT) sigue bloqueando penetracion fisica
    real entre 2 piezas -eso nunca dejo de ser obligatorio."""
    space = build_custom_load_space("Roomy", LoadSpaceType.CUSTOM, 3000, 3000, 3000, 50000)
    dx, dy, dz = apply_tilt(1200.0, 80.0, 1200.0, "y", 15.0)
    neighbor = _placed_panel(id="N", code="N", x=0, y=0, z=0, dx=1200, dy=80, dz=1200, allow_tilt=False, max_tilt_angle=0.0, tilt_angle=0.0, base_dx=1200.0, base_dy=80.0, base_dz=1200.0, source_dimensions=Dimensions3D(length=1200, width=80, height=1200))
    penetrating = _placed_panel(
        id="T", code="T", x=0, y=40, z=0, dx=dx, dy=dy, dz=dz, tilt_angle=15.0, max_tilt_angle=20.0,
        base_dx=1200.0, base_dy=80.0, base_dz=1200.0, source_dimensions=Dimensions3D(length=1200, width=80, height=1200),
    )
    state = PackingResult(container=space, placed=[neighbor, penetrating], unloaded=[], metrics=_metrics_stub(total_weight=40, total_pieces=2))
    errors = validate_for_export(state, space)
    assert any("colision" in e.lower() for e in errors)
