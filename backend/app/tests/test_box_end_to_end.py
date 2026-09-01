"""
CUBOX 2.0 - Fase 5: Loose Boxes end-to-end. Cubre los escenarios A-L de la
seccion 37 del plan, mas un smoke test de reportes (seccion 32) y de la
precedencia de ImportDefaults a traves del endpoint real (no solo a nivel
de funcion pura).

Convenciones: los tests A-E/L/M/N pasan por la API real (TestClient) porque
son especificamente sobre el WIRING (import -> preview -> workspace ->
pack -> optimize-remaining -> reportes). Los tests F-K son a nivel de
pack_container directo (igual que test_road_weight.py) porque son sobre la
fisica del packer, no sobre el wiring HTTP."""

import io

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.core.final_validation import validate_for_export
from app.core.packer import compute_metrics, pack_container
from app.core.pdf_export import build_container_report_pdf
from app.main import app
from app.models.containers import build_custom_load_space
from app.models.schemas import (
    ContainerReportRequest,
    Dimensions3D,
    ItemType,
    LoadSpaceType,
    OrientationPolicy,
    PackingResult,
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


def _import_box(rows, headers=None, **form_extra):
    content = _build_workbook_bytes(
        headers or ["Code", "Quantity", "Length", "Width", "Height", "Weight", "Orientation", "Stackable"], rows
    )
    data = {"profile": "box", **form_extra}
    r = client.post("/api/import-items-excel", files={"file": ("box.xlsx", content, XLSX_MIME)}, data=data)
    assert r.status_code == 200
    return r.json()


def _box_item(width=1000, height=1000, thickness=1000, weight=300, quantity=1, code="ITEM", orientation_policy=OrientationPolicy.FREE):
    """Cubo de dimensiones iguales bajo FREE por defecto: la orientacion
    resultante nunca es ambigua para verificar fisica (todas las
    permutaciones dan el mismo dx/dy/dz)."""
    return WindowItem(
        code=code, width=width, height=height, thickness=thickness, weight=weight, quantity=quantity,
        item_type=ItemType.BOX, orientation_policy=orientation_policy,
    )


# ---------------------------------------------------------------------------
# TEST A - import BOX con Orientation default del plan (celda vacia).
# ---------------------------------------------------------------------------


def test_box_import_applies_plan_orientation_default_when_cell_empty():
    body = _import_box(
        [["B1", 3, 600, 400, 300, 25, "", "Yes"]],
        default_orientation_policy="upright",
    )
    assert body["is_valid"] is True
    item = body["items"][0]
    assert item["orientation_policy"] == "upright"
    assert any(w["code"] == "ORIENTATION_DEFAULTED" for w in body["warnings"])
    assert any("plan" in w["message"].lower() for w in body["warnings"] if w["code"] == "ORIENTATION_DEFAULTED")


# ---------------------------------------------------------------------------
# TEST B - Orientation explicito en Excel gana sobre el default del plan.
# ---------------------------------------------------------------------------


def test_explicit_excel_orientation_overrides_plan_default():
    body = _import_box(
        [["B1", 3, 600, 400, 300, 25, "fixed", "Yes"]],
        default_orientation_policy="upright",
    )
    assert body["is_valid"] is True
    item = body["items"][0]
    assert item["orientation_policy"] == "fixed"
    assert not any(w["code"] == "ORIENTATION_DEFAULTED" for w in body["warnings"])


# ---------------------------------------------------------------------------
# TEST C - default de Stackable del plan.
# ---------------------------------------------------------------------------


def test_box_import_applies_plan_stackable_default_when_cell_empty():
    body = _import_box(
        [["B1", 3, 600, 400, 300, 25, "free", ""]],
        default_stackable="true",
    )
    assert body["is_valid"] is True
    item = body["items"][0]
    assert item["stackable"] is True
    assert any(w["code"] == "STACKABLE_DEFAULTED" and "plan" in w["message"].lower() for w in body["warnings"])


# ---------------------------------------------------------------------------
# TEST D - Stackable explicito en Excel gana sobre el default del plan.
# ---------------------------------------------------------------------------


def test_explicit_excel_stackable_overrides_plan_default():
    body = _import_box(
        [["B1", 3, 600, 400, 300, 25, "free", "No"]],
        default_stackable="true",
    )
    assert body["is_valid"] is True
    item = body["items"][0]
    assert item["stackable"] is False
    assert not any(w["code"] == "STACKABLE_DEFAULTED" for w in body["warnings"])


# ---------------------------------------------------------------------------
# TEST E - sin Excel y sin default de plan -> fallback de sistema seguro
# (FREE / No).
# ---------------------------------------------------------------------------


def test_box_import_without_excel_or_plan_default_uses_safe_system_fallback():
    body = _import_box([["B1", 3, 600, 400, 300, 25, "", ""]])  # sin form_extra: sin ImportDefaults
    assert body["is_valid"] is True
    item = body["items"][0]
    assert item["orientation_policy"] == "free"
    assert item["stackable"] is False
    codes = {w["code"] for w in body["warnings"]}
    assert "STACKABLE_DEFAULTED" in codes
    assert "ORIENTATION_DEFAULTED" not in codes  # FREE es el default de sistema, no un default de plan


# ---------------------------------------------------------------------------
# TEST F - BOX FREE empaqueta valido (6 orientaciones posibles).
# ---------------------------------------------------------------------------


def test_box_free_packs_validly():
    container = build_custom_load_space("Test Space", LoadSpaceType.CONTAINER, 6000, 2400, 2500, 50000)
    item = _box_item(width=600, height=400, thickness=300, weight=50, quantity=5, orientation_policy=OrientationPolicy.FREE)
    result = pack_container([item], container, strategy="highest_priority")

    assert len(result.placed) == 5
    for p in result.placed:
        assert p.item_type == ItemType.BOX
        assert {p.dx, p.dy, p.dz} == {600, 400, 300}
    assert validate_for_export(result, container) == []


# ---------------------------------------------------------------------------
# TEST G - BOX UPRIGHT: Height (canonico) permanece siempre vertical.
# ---------------------------------------------------------------------------


def test_box_upright_keeps_height_vertical():
    container = build_custom_load_space("Test Space", LoadSpaceType.CONTAINER, 6000, 2400, 2500, 50000)
    # dimensions_from_legacy(600, 300, 400) = Dimensions3D(length=600, width=400, height=300)
    # -> canonico height=300 debe ser siempre la vertical (dz).
    item = _box_item(width=600, height=300, thickness=400, weight=50, quantity=4, orientation_policy=OrientationPolicy.UPRIGHT)
    result = pack_container([item], container, strategy="highest_priority")

    assert len(result.placed) == 4
    for p in result.placed:
        assert p.dz == 300
        assert {p.dx, p.dy} == {600, 400}
    assert validate_for_export(result, container) == []


# ---------------------------------------------------------------------------
# TEST H - BOX Stackable=False nunca se usa como soporte.
# ---------------------------------------------------------------------------


def test_box_non_stackable_is_never_used_as_support():
    # Container angosto: solo entra 1 caja por fila en X/Y a nivel piso, asi
    # que la unica forma de colocar la 2da caja seria apilandola sobre la
    # 1ra -pero la 1ra no es stackable, asi que debe quedar unloaded en vez
    # de apoyarse sobre ella.
    container = build_custom_load_space("Narrow Space", LoadSpaceType.CONTAINER, 1000, 1000, 2500, 50000)
    item = _box_item(width=1000, height=1000, thickness=1000, weight=50, quantity=2, orientation_policy=OrientationPolicy.FREE)
    item.stackable = False  # LoadItem no es frozen; mas simple que reconstruir con Field extra

    result = pack_container([item], container, strategy="highest_priority")

    assert len(result.placed) == 1
    assert len(result.unloaded) == 1
    assert result.placed[0].z == 0
    assert validate_for_export(result, container) == []


# ---------------------------------------------------------------------------
# TEST I - BOX Max Stack Weight se respeta.
# ---------------------------------------------------------------------------


def test_box_max_stack_weight_is_respected():
    # Ancho = exactamente el footprint de BASE (500mm): no queda lugar al
    # lado en el piso, asi que la unica forma de colocar TOP seria
    # apilandolo -lo cual el limite de peso debe impedir.
    container = build_custom_load_space("Test Space", LoadSpaceType.CONTAINER, 1000, 500, 2500, 50000)
    base = _box_item(code="BASE", width=1000, height=1000, thickness=500, weight=50, quantity=1, orientation_policy=OrientationPolicy.FREE)
    base.stackable = True
    base.max_stack_weight = 10  # el que va encima pesa mas que esto -> no puede apoyarse

    top = _box_item(code="TOP", width=1000, height=1000, thickness=500, weight=50, quantity=1, orientation_policy=OrientationPolicy.FREE)

    result = pack_container([base, top], container, strategy="highest_priority")

    placed_codes = {p.code for p in result.placed}
    unloaded_codes = {u.code for u in result.unloaded}
    assert placed_codes == {"BASE"}
    assert unloaded_codes == {"TOP"}
    assert validate_for_export(result, container) == []


# ---------------------------------------------------------------------------
# TEST J - BOX en Custom Truck: empaqueta dentro de las dimensiones reales
# del Truck.
# ---------------------------------------------------------------------------


def test_box_packs_inside_actual_custom_truck_dimensions():
    truck = build_custom_load_space("Warehouse Truck 01", LoadSpaceType.TRUCK, 7200, 2400, 2500, 8000)
    item = _box_item(width=600, height=400, thickness=300, weight=50, quantity=10, orientation_policy=OrientationPolicy.FREE)

    result = pack_container([item], truck, strategy="highest_priority")

    assert len(result.placed) == 10
    for p in result.placed:
        assert p.x + p.dx <= truck.length + 1e-6
        assert p.y + p.dy <= truck.width + 1e-6
        assert p.z + p.dz <= truck.height + 1e-6
    assert result.container.load_space_type == LoadSpaceType.TRUCK


# ---------------------------------------------------------------------------
# TEST K - BOX en Custom Trailer: idem.
# ---------------------------------------------------------------------------


def test_box_packs_inside_actual_custom_trailer_dimensions():
    trailer = build_custom_load_space("Flatbed Trailer 01", LoadSpaceType.TRAILER, 13600, 2480, 2700, 24000)
    item = _box_item(width=600, height=400, thickness=300, weight=50, quantity=10, orientation_policy=OrientationPolicy.FREE)

    result = pack_container([item], trailer, strategy="highest_priority")

    assert len(result.placed) == 10
    for p in result.placed:
        assert p.x + p.dx <= trailer.length + 1e-6
        assert p.y + p.dy <= trailer.width + 1e-6
        assert p.z + p.dz <= trailer.height + 1e-6
    assert result.container.load_space_type == LoadSpaceType.TRAILER


# ---------------------------------------------------------------------------
# TEST L - Optimize Remaining preserva las reglas de BOX de punta a punta
# (via API real: pack -> lock -> optimize-remaining).
# ---------------------------------------------------------------------------


def test_optimize_remaining_preserves_box_rules_end_to_end():
    box_item = {
        "code": "BOX1",
        "dimensions": {"length": 600, "width": 400, "height": 300},
        "weight": 20,
        "quantity": 5,
        "item_type": "box",
        "orientation_policy": "upright",
        "stackable": True,
        "max_stack_weight": 500,
    }
    r = client.post(
        "/api/pack",
        json={"items": [box_item], "container_id": "40ft_standard", "optimization_mode": "best_space"},
    )
    assert r.status_code == 200
    best = r.json()["best"]
    assert best["metrics"]["loaded_pieces"] == 5

    piece_to_lock = best["placed"][0]
    lock_resp = client.post("/api/lock-piece", json={"piece_id": piece_to_lock["id"]})
    assert lock_resp.status_code == 200

    r2 = client.post("/api/optimize-remaining")
    assert r2.status_code == 200
    reoptimized = r2.json()["best"]
    assert reoptimized["metrics"]["loaded_pieces"] == 5

    for p in reoptimized["placed"]:
        assert p["item_type"] == "box"
        assert p["orientation_policy"] == "upright"
        assert p["max_stack_weight"] == 500
        assert p["dz"] == 300  # UPRIGHT: height canonico siempre vertical
        if p["id"] == piece_to_lock["id"]:
            assert p["locked"] is True
            assert p["x"] == piece_to_lock["x"]
            assert p["y"] == piece_to_lock["y"]
            assert p["z"] == piece_to_lock["z"]


# ---------------------------------------------------------------------------
# Smoke test de reportes (seccion 32): un plan BOX exporta sin crashear.
# Documentado como debt: las columnas del PDF siguen usando encabezados
# legacy Width/Height/Thickness (ver core/pdf_export.py), no se rediseñan
# en esta fase.
# ---------------------------------------------------------------------------


def test_box_plan_exports_container_report_pdf_without_crashing():
    container = build_custom_load_space("Test Space", LoadSpaceType.CONTAINER, 6000, 2400, 2500, 50000)
    item = _box_item(width=600, height=400, thickness=300, weight=50, quantity=3, orientation_policy=OrientationPolicy.FREE)
    result = pack_container([item], container, strategy="highest_priority")
    metrics = compute_metrics(container, result.placed, result.unloaded)
    packing_result = PackingResult(
        container=container, placed=result.placed, unloaded=result.unloaded, metrics=metrics,
        load_sequence=[p.id for p in result.placed], unload_sequence=[p.id for p in result.placed],
    )

    pdf_bytes = build_container_report_pdf(packing_result, ContainerReportRequest(include_overview_image=False))

    assert pdf_bytes[:4] == b"%PDF"
