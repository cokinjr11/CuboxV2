"""
CUBOX 2.0 - Fase 6: Palletized Load end-to-end. Cubre los tests A-P de la
seccion 29 del plan.

Este archivo NO reimplementa fisica/orientacion/secuencia para PALLET: como
confirma el reporte "before coding" de esta fase, el packer, orientation.py,
manual_move.py, road_weight.py y sequence.py ya son 100% genericos (cero
referencias a ItemType) desde fases anteriores. Estos tests existen para
DOCUMENTAR y PROBAR que un pallet -tratado como un unico Load Unit rigido ya
construido, dimensiones = Length/Width/Height, Height = altura TOTAL cargada-
efectivamente atraviesa ese motor generico sin ningun camino de codigo
especial, igual que ya se probo para BOX en test_box_end_to_end.py.

Unico cambio real de esta fase en el importador: PALLET paso de
orientation_mode="none" a "optional" (solo UPRIGHT/FIXED) para que Floor
Rotation (wizard) tenga efecto real -ver core/import_items.py."""

import io

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.core.final_validation import validate_for_export
from app.core.orientation import get_valid_orientations
from app.core.packer import compute_metrics, pack_container
from app.core.pdf_export import build_container_report_pdf
from app.core.road_weight import evaluate_road_weight, weight_point_from_placed
from app.core.sequence import compute_load_sequence, compute_unload_sequence
from app.main import app
from app.models.containers import build_custom_load_space
from app.models.schemas import (
    ContainerReportRequest,
    Dimensions3D,
    ItemType,
    LoadingAnchor,
    LoadSpaceSpec,
    LoadSpaceType,
    OrientationPolicy,
    PackingResult,
    PlacedPiece,
    RoadSupport,
    RoadWeightConfig,
    WindowItem,
)

client = TestClient(app)
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _build_workbook_bytes(headers: list[str], rows: list[list]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _import_pallet(rows, headers=None, **form_extra):
    content = _build_workbook_bytes(
        headers or ["Code", "Quantity", "Length", "Width", "Height", "Weight"], rows
    )
    data = {"profile": "pallet", **form_extra}
    r = client.post("/api/import-items-excel", files={"file": ("pallet.xlsx", content, XLSX_MIME)}, data=data)
    assert r.status_code == 200
    return r.json()


def _pallet_item(length=1200, width=1000, height=1650, weight=780, quantity=1, code="PAL",
                  orientation_policy=OrientationPolicy.UPRIGHT, stackable=False, max_stack_weight=None):
    """Un pallet ya construido: Height es la altura TOTAL cargada, Weight el
    peso TOTAL cargado (seccion 5 del pedido) -no se modela por separado el
    pallet de madera ni las cajas encima, eso queda para Pallet Builder."""
    return WindowItem(
        code=code, dimensions=Dimensions3D(length=length, width=width, height=height), weight=weight, quantity=quantity,
        item_type=ItemType.PALLET, orientation_policy=orientation_policy,
        stackable=stackable, max_stack_weight=max_stack_weight,
    )


# ---------------------------------------------------------------------------
# TEST A - PALLET IMPORT: canonical dimensions + ItemType.PALLET.
# ---------------------------------------------------------------------------


def test_a_pallet_import_uses_canonical_dimensions_and_item_type():
    body = _import_pallet([["PAL-001", 4, 1200, 1000, 1650, 780]])
    assert body["is_valid"] is True
    item = body["items"][0]
    assert item["item_type"] == "pallet"
    assert item["dimensions"] == {"length": 1200, "width": 1000, "height": 1650}
    assert item["weight"] == 780
    assert item["quantity"] == 4


# ---------------------------------------------------------------------------
# TEST B - UPRIGHT PALLET: floor rotation allowed, solo 2 orientaciones
# validas (base L x W o W x L, Height siempre vertical).
# ---------------------------------------------------------------------------


def test_b_upright_pallet_has_exactly_two_valid_orientations():
    body = _import_pallet(
        [["PAL-001", 1, 1200, 1000, 1650, 780, "UPRIGHT"]],
        headers=["Code", "Quantity", "Length", "Width", "Height", "Weight", "Orientation"],
    )
    item = body["items"][0]
    assert item["orientation_policy"] == "upright"

    dims = Dimensions3D(**item["dimensions"])
    orientations = get_valid_orientations(dims, OrientationPolicy.UPRIGHT)
    assert len(orientations) == 2
    footprints = {frozenset((o.dx, o.dy)) for o in orientations}
    assert footprints == {frozenset((1200, 1000))}
    assert all(o.dz == 1650 for o in orientations)  # Height jamas deja de ser vertical


# ---------------------------------------------------------------------------
# TEST C - FIXED PALLET: floor rotation deshabilitado, exactamente 1
# orientacion.
# ---------------------------------------------------------------------------


def test_c_fixed_pallet_has_exactly_one_valid_orientation():
    body = _import_pallet(
        [["PAL-002", 1, 1200, 1000, 1650, 780, "FIXED"]],
        headers=["Code", "Quantity", "Length", "Width", "Height", "Weight", "Orientation"],
    )
    item = body["items"][0]
    assert item["orientation_policy"] == "fixed"

    dims = Dimensions3D(**item["dimensions"])
    orientations = get_valid_orientations(dims, OrientationPolicy.FIXED)
    assert len(orientations) == 1
    assert (orientations[0].dx, orientations[0].dy, orientations[0].dz) == (1200, 1000, 1650)


def test_c_pallet_orientation_rejects_free_and_panel_edge_only():
    """PALLET solo admite UPRIGHT/FIXED (seccion 8): FREE/PANEL_EDGE_ONLY
    deben rechazarse con un error claro, no aceptarse silenciosamente."""
    body = _import_pallet(
        [["PAL-003", 1, 1200, 1000, 1650, 780, "FREE"]],
        headers=["Code", "Quantity", "Length", "Width", "Height", "Weight", "Orientation"],
    )
    assert body["is_valid"] is False
    assert any(e["code"] == "UNSUPPORTED_ORIENTATION_FOR_PROFILE" for e in body["errors"])


# ---------------------------------------------------------------------------
# TEST D - ORIENTATION OVERRIDE: valor explicito de Excel gana sobre el
# default del plan (Floor Rotation del wizard).
# ---------------------------------------------------------------------------


def test_d_explicit_excel_orientation_overrides_wizard_floor_rotation_default():
    # Wizard: Floor Rotation = Allowed (UPRIGHT) como default del plan.
    body = _import_pallet(
        [["PAL-004", 1, 1200, 1000, 1650, 780, "FIXED"]],  # Excel dice FIXED explicito
        headers=["Code", "Quantity", "Length", "Width", "Height", "Weight", "Orientation"],
        default_orientation_policy="upright",
    )
    assert body["is_valid"] is True
    item = body["items"][0]
    assert item["orientation_policy"] == "fixed"
    assert not any(w["code"] == "ORIENTATION_DEFAULTED" for w in body["warnings"])


def test_d_plan_default_applies_when_orientation_cell_is_empty():
    body = _import_pallet(
        [["PAL-005", 1, 1200, 1000, 1650, 780, ""]],
        headers=["Code", "Quantity", "Length", "Width", "Height", "Weight", "Orientation"],
        default_orientation_policy="fixed",  # Floor Rotation = Not Allowed en el wizard
    )
    item = body["items"][0]
    assert item["orientation_policy"] == "fixed"
    assert any(w["code"] == "ORIENTATION_DEFAULTED" for w in body["warnings"])


def test_d_system_default_upright_when_no_excel_value_and_no_plan_default():
    body = _import_pallet([["PAL-006", 1, 1200, 1000, 1650, 780]])  # sin columna Orientation, sin plan default
    item = body["items"][0]
    assert item["orientation_policy"] == "upright"


# ---------------------------------------------------------------------------
# TEST E - STACKABLE DEFAULT: el default del plan aplica cuando el item no
# trae valor explicito.
# ---------------------------------------------------------------------------


def test_e_pallet_stackable_default_from_plan_applies_when_cell_empty():
    body = _import_pallet(
        [["PAL-007", 1, 1200, 1000, 1650, 780, ""]],
        headers=["Code", "Quantity", "Length", "Width", "Height", "Weight", "Stackable"],
        default_stackable="true",
    )
    item = body["items"][0]
    assert item["stackable"] is True
    assert any(w["code"] == "STACKABLE_DEFAULTED" for w in body["warnings"])


# ---------------------------------------------------------------------------
# TEST F - STACKABLE OVERRIDE: "No" explicito en Excel gana sobre "Yes" del
# plan (ejemplo textual del pedido, seccion 12: wizard Yes, Excel PAL-004 No
# -> resultado No).
# ---------------------------------------------------------------------------


def test_f_explicit_excel_stackable_no_overrides_wizard_yes():
    body = _import_pallet(
        [["PAL-004", 1, 1200, 1000, 1650, 780, "No"]],
        headers=["Code", "Quantity", "Length", "Width", "Height", "Weight", "Stackable"],
        default_stackable="true",
    )
    item = body["items"][0]
    assert item["stackable"] is False
    assert not any(w["code"] == "STACKABLE_DEFAULTED" for w in body["warnings"])


# ---------------------------------------------------------------------------
# TEST G - MAX STACK WEIGHT: el limite existente se respeta para PALLET.
# ---------------------------------------------------------------------------


def test_g_pallet_max_stack_weight_is_respected():
    # Ancho = footprint exacto de BASE: no queda lugar al lado en el piso,
    # la unica forma de colocar TOP seria apilandolo sobre BASE.
    space = build_custom_load_space("Test Space", LoadSpaceType.CONTAINER, 1300, 1100, 3500, 50000)
    base = _pallet_item(code="BASE", length=1200, width=1000, height=1000, weight=780, quantity=1, stackable=True)
    base.max_stack_weight = 500  # TOP pesa mas que esto -> no puede apoyarse

    top = _pallet_item(code="TOP", length=1200, width=1000, height=1000, weight=780, quantity=1)

    result = pack_container([base, top], space, strategy="highest_priority")

    placed_codes = {p.code for p in result.placed}
    unloaded_codes = {u.code for u in result.unloaded}
    assert placed_codes == {"BASE"}
    assert unloaded_codes == {"TOP"}
    assert validate_for_export(result, space) == []


# ---------------------------------------------------------------------------
# TEST H - NON-STACKABLE SUPPORT: un pallet no-stackable nunca soporta a
# otro.
# ---------------------------------------------------------------------------


def test_h_non_stackable_pallet_is_never_used_as_support():
    space = build_custom_load_space("Narrow Space", LoadSpaceType.CONTAINER, 1200, 1000, 3500, 50000)
    item = _pallet_item(length=1200, width=1000, height=1650, weight=780, quantity=2, stackable=False)

    result = pack_container([item], space, strategy="highest_priority")

    assert len(result.placed) == 1
    assert len(result.unloaded) == 1
    assert result.placed[0].z == 0
    assert validate_for_export(result, space) == []


# ---------------------------------------------------------------------------
# TEST I - PRESET CONTAINER PACK: Palletized Load empaqueta exitosamente.
# ---------------------------------------------------------------------------


def test_i_pallet_packs_successfully_in_preset_container():
    space = build_custom_load_space("40ft Standard-like", LoadSpaceType.CONTAINER, 12000, 2350, 2390, 28000)
    item = _pallet_item(length=1200, width=1000, height=1650, weight=780, quantity=8)

    result = pack_container([item], space, strategy="highest_priority")

    assert len(result.placed) == 8
    for p in result.placed:
        assert p.item_type == ItemType.PALLET
        assert p.dz == 1650  # UPRIGHT: Height siempre vertical
    assert validate_for_export(result, space) == []


# ---------------------------------------------------------------------------
# TEST J - CUSTOM TRUCK: usa las dimensiones reales del Truck.
# ---------------------------------------------------------------------------


def test_j_pallet_packs_inside_actual_custom_truck_dimensions():
    truck = build_custom_load_space("Warehouse Truck 01", LoadSpaceType.TRUCK, 7200, 2400, 2500, 8000)
    item = _pallet_item(length=1200, width=1000, height=1600, weight=500, quantity=4)

    result = pack_container([item], truck, strategy="highest_priority")

    assert len(result.placed) == 4
    for p in result.placed:
        assert p.x + p.dx <= truck.length + 1e-6
        assert p.y + p.dy <= truck.width + 1e-6
        assert p.z + p.dz <= truck.height + 1e-6
    assert result.container.load_space_type == LoadSpaceType.TRUCK


# ---------------------------------------------------------------------------
# TEST K - CUSTOM TRAILER: idem.
# ---------------------------------------------------------------------------


def test_k_pallet_packs_inside_actual_custom_trailer_dimensions():
    trailer = build_custom_load_space("Flatbed Trailer 01", LoadSpaceType.TRAILER, 13600, 2480, 2700, 24000)
    item = _pallet_item(length=1200, width=1000, height=1600, weight=500, quantity=10)

    result = pack_container([item], trailer, strategy="highest_priority")

    assert len(result.placed) == 10
    for p in result.placed:
        assert p.x + p.dx <= trailer.length + 1e-6
        assert p.y + p.dy <= trailer.width + 1e-6
        assert p.z + p.dz <= trailer.height + 1e-6
    assert result.container.load_space_type == LoadSpaceType.TRAILER


# ---------------------------------------------------------------------------
# TEST L - ROAD WEIGHT: una colocacion de pallet pesado que sobrecargaria un
# support configurado es rechazada. RoadWeightConfig no tiene ningun camino
# de codigo especifico por ItemType (ver road_weight.py) -este test prueba
# que efectivamente aplica igual para PALLET.
# ---------------------------------------------------------------------------


def _pallet_placed(piece_id, x, weight, dx=1200, dy=1000, dz=1650, y=0, z=0, stackable=False):
    return PlacedPiece(
        id=piece_id, code=piece_id, weight=weight, stackable=stackable, priority=1,
        x=x, y=y, z=z, dx=dx, dy=dy, dz=dz,
        orientation_label="UPRIGHT-a (L x W, vertical H)", source_width=dx, source_height=dz, source_thickness=dy,
        item_type=ItemType.PALLET, orientation_policy=OrientationPolicy.UPRIGHT,
    )


def test_l_heavy_pallet_overloading_a_configured_road_support_is_rejected():
    supports = [
        RoadSupport(id="A", name="Front Axle", position_x_mm=1000, max_load_kg=5000, baseline_load_kg=0),
        RoadSupport(id="B", name="Rear Axle", position_x_mm=6000, max_load_kg=5000, baseline_load_kg=0),
    ]
    space = LoadSpaceSpec(
        id="road-pallet-test", name="Road Pallet Test", load_space_type=LoadSpaceType.TRUCK,
        length=7000, width=2400, height=2500, max_weight=100000,
        road_weight_config=RoadWeightConfig(enabled=True, supports=supports),
    )
    # Un solo pallet MUY pesado justo sobre Support B -sobrecarga ese support
    # aunque el payload total (3000kg) este muy por debajo de max_weight.
    overloaded = [_pallet_placed("heavy", x=5800, weight=9000)]
    result = evaluate_road_weight(space.road_weight_config, (weight_point_from_placed(p) for p in overloaded))
    assert result.valid is False

    # El packer real, en cambio, nunca deberia llegar a colocar ese pallet en
    # esa posicion: prueba que termina unloaded o reubicado en vez de violar
    # el limite.
    heavy_item = _pallet_item(length=1200, width=1000, height=1650, weight=9000, quantity=1)
    packed = pack_container([heavy_item], space, strategy="highest_priority")
    final_road_weight = evaluate_road_weight(
        space.road_weight_config, (weight_point_from_placed(p) for p in packed.placed)
    )
    assert final_road_weight.valid is True


# ---------------------------------------------------------------------------
# TEST M - LOCK / OPTIMIZE REMAINING: un pallet bloqueado se preserva EXACTO
# (posicion, orientacion, dimensiones, peso, stackable, max_stack_weight) y
# el resto vuelve a empaquetar alrededor -via API real, igual patron que
# test_optimize_remaining_preserves_box_rules_end_to_end.
# ---------------------------------------------------------------------------


def test_m_lock_and_optimize_remaining_preserves_pallet_exactly():
    pallet_item = {
        "code": "PAL1",
        "dimensions": {"length": 1200, "width": 1000, "height": 1650},
        "weight": 780,
        "quantity": 6,
        "item_type": "pallet",
        "orientation_policy": "upright",
        "stackable": False,
        "max_stack_weight": 1200,
    }
    r = client.post(
        "/api/pack",
        json={"items": [pallet_item], "container_id": "40ft_standard", "optimization_mode": "best_space"},
    )
    assert r.status_code == 200
    best = r.json()["best"]
    assert best["metrics"]["loaded_pieces"] == 6

    piece_to_lock = best["placed"][0]
    lock_resp = client.post("/api/lock-piece", json={"piece_id": piece_to_lock["id"]})
    assert lock_resp.status_code == 200

    r2 = client.post("/api/optimize-remaining")
    assert r2.status_code == 200
    reoptimized = r2.json()["best"]
    assert reoptimized["metrics"]["loaded_pieces"] == 6

    for p in reoptimized["placed"]:
        assert p["item_type"] == "pallet"
        assert p["orientation_policy"] == "upright"
        assert p["max_stack_weight"] == 1200
        assert p["stackable"] is False
        assert p["weight"] == 780
        assert p["dz"] == 1650  # UPRIGHT: Height canonico siempre vertical
        if p["id"] == piece_to_lock["id"]:
            assert p["locked"] is True
            assert p["x"] == piece_to_lock["x"]
            assert p["y"] == piece_to_lock["y"]
            assert p["z"] == piece_to_lock["z"]
            assert p["dx"] == piece_to_lock["dx"]
            assert p["dy"] == piece_to_lock["dy"]


# ---------------------------------------------------------------------------
# TEST N - LOAD SEQUENCE: bottom-before-top cuando hay apilado, y el anchor
# elegido (BACK_RIGHT/BACK_LEFT) se respeta -reusa integramente el motor de
# Fase 5.1 (sequence.py), sin ningun camino especial para PALLET.
# ---------------------------------------------------------------------------


def _placed_pallet_stack(space):
    bottom = _pallet_placed("bottom", x=space.length - 1200, weight=780, y=space.width - 1000, z=0, stackable=True)
    top = _pallet_placed("top", x=space.length - 1200, weight=780, y=space.width - 1000, z=1650)
    return [bottom, top]


def test_n_pallet_load_sequence_bottom_before_top_and_respects_anchor():
    space = build_custom_load_space("Seq Test", LoadSpaceType.CONTAINER, 12000, 2400, 3500, 50000)
    stack = _placed_pallet_stack(space)

    sequence = compute_load_sequence(stack, space, LoadingAnchor.BACK_RIGHT)
    assert sequence.index("bottom") < sequence.index("top")
    assert sequence[0] == "bottom"  # unico piso disponible -> arranca ahi


def test_n_pallet_load_sequence_back_left_anchor():
    space = build_custom_load_space("Seq Test", LoadSpaceType.CONTAINER, 12000, 2400, 3500, 50000)
    right_pallet = _pallet_placed("right", x=space.length - 1200, weight=780, y=space.width - 1000, z=0)
    left_pallet = _pallet_placed("left", x=space.length - 1200, weight=780, y=0, z=0)

    sequence = compute_load_sequence([right_pallet, left_pallet], space, LoadingAnchor.BACK_LEFT)
    assert sequence[0] == "left"


# ---------------------------------------------------------------------------
# TEST O - UNLOAD SEQUENCE: top-before-bottom cuando hay apilado, y
# accesibilidad desde la puerta.
# ---------------------------------------------------------------------------


def test_o_pallet_unload_sequence_top_before_bottom():
    space = build_custom_load_space("Seq Test", LoadSpaceType.CONTAINER, 12000, 2400, 3500, 50000)
    stack = _placed_pallet_stack(space)

    unload = compute_unload_sequence(stack)
    assert unload.index("top") < unload.index("bottom")


def test_o_pallet_unload_sequence_prefers_door_accessible_module():
    front = _pallet_placed("front", x=100, weight=780)
    back = _pallet_placed("back", x=10000, weight=780, y=1200)

    unload = compute_unload_sequence([front, back])
    assert unload.index("front") < unload.index("back")


# ---------------------------------------------------------------------------
# TEST P - PDF SMOKE: un plan PALLET exporta sin excepciones. Documentado
# como debt (igual que BOX): el PDF sigue usando encabezados legacy
# Width/Height/Thickness (source_width/source_height/source_thickness),
# numericamente correctos (Width<-Length, Height<-Height, Thickness<-Width)
# pero con nombres de columna que no tienen sentido para un Load Unit
# generico -no se rediseña en esta fase (seccion 25 del pedido).
# ---------------------------------------------------------------------------


def test_p_pallet_plan_exports_container_report_pdf_without_crashing():
    space = build_custom_load_space("Test Space", LoadSpaceType.CONTAINER, 12000, 2400, 3500, 50000)
    item = _pallet_item(length=1200, width=1000, height=1650, weight=780, quantity=5)
    result = pack_container([item], space, strategy="highest_priority")
    metrics = compute_metrics(space, result.placed, result.unloaded)
    packing_result = PackingResult(
        container=space, placed=result.placed, unloaded=result.unloaded, metrics=metrics,
        load_sequence=[p.id for p in result.placed], unload_sequence=[p.id for p in result.placed],
    )

    pdf_bytes = build_container_report_pdf(packing_result, ContainerReportRequest(include_overview_image=False))

    assert pdf_bytes[:4] == b"%PDF"


# ---------------------------------------------------------------------------
# Dataset realista (seccion 28): 15-30 instancias de pallet, footprints
# mixtos, alturas/pesos distintos, Stackable Yes/No, Max Stack Weight,
# Groups. SOLO dimensiones de prueba -no se afirma que sean estandares
# universales de pallet.
# ---------------------------------------------------------------------------

_REALISTIC_PALLET_ROWS = [
    # Code,      Qty, Length, Width, Height, Weight, Description,        Orientation, Stackable, MaxStackWeight, Group
    ["PAL-A01", 4, 1200, 1000, 1500, 650, "Canned goods", "UPRIGHT", "Yes", 800, "GROCERY"],
    ["PAL-A02", 3, 1200, 1000, 1800, 900, "Canned goods (tall)", "UPRIGHT", "Yes", 600, "GROCERY"],
    ["PAL-B01", 5, 1200, 800, 1400, 500, "Dry goods", "UPRIGHT", "Yes", 700, "GROCERY"],
    ["PAL-B02", 2, 1200, 800, 2000, 950, "Dry goods (tall)", "UPRIGHT", "No", "", "GROCERY"],
    ["PAL-C01", 3, 1000, 1000, 1600, 700, "Appliances", "FIXED", "No", "", "ELECTRONICS"],
    ["PAL-C02", 2, 1000, 1000, 2100, 1100, "Appliances (large)", "FIXED", "No", "", "ELECTRONICS"],
    ["PAL-D01", 4, 1140, 1140, 1450, 620, "Beverages", "UPRIGHT", "Yes", 750, "BEVERAGE"],
    ["PAL-D02", 3, 1140, 1140, 1900, 980, "Beverages (tall)", "UPRIGHT", "Yes", 500, "BEVERAGE"],
    ["PAL-E01", 2, 1200, 1000, 2200, 1200, "Machinery parts", "FIXED", "No", "", "INDUSTRIAL"],
    ["PAL-E02", 2, 1200, 1000, 1650, 780, "Machinery parts (std)", "UPRIGHT", "Yes", 900, "INDUSTRIAL"],
]


def _expand_realistic_rows_to_instance_count():
    return sum(row[1] for row in _REALISTIC_PALLET_ROWS)


def test_realistic_pallet_dataset_has_15_to_30_instances():
    total = _expand_realistic_rows_to_instance_count()
    assert 15 <= total <= 30, f"dataset de prueba fuera del rango pedido (15-30): {total}"


def test_realistic_pallet_dataset_imports_and_packs_end_to_end():
    body = _import_pallet(
        [row[:6] + [row[6]] + row[7:9] + [row[9]] + [row[10]] for row in _REALISTIC_PALLET_ROWS],
        headers=[
            "Code", "Quantity", "Length", "Width", "Height", "Weight",
            "Description", "Orientation", "Stackable", "Max Stack Weight", "Group",
        ],
    )
    assert body["is_valid"] is True, body["errors"]
    assert body["summary"]["total_units"] == _expand_realistic_rows_to_instance_count()

    items = [WindowItem(**item) for item in body["items"]]
    space = build_custom_load_space("40ft High Cube-like", LoadSpaceType.CONTAINER, 12000, 2350, 2690, 28000)
    result = pack_container(items, space, strategy="highest_priority")

    assert len(result.placed) + len(result.unloaded) == _expand_realistic_rows_to_instance_count()
    assert validate_for_export(result, space) == []
    for p in result.placed:
        assert p.item_type == ItemType.PALLET
