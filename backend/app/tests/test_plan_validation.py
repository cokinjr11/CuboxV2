"""Fase 6C: Unified Operational Validation & Export Readiness.

build_plan_validation_result() no reimplementa ninguna regla fisica -junta
collect_validation_issues() (mismos checks de siempre, ver
test_final_validation.py) + PackingResult.operational_warnings (ver
test_sequence.py) + PackingResult.unloaded, y categoriza. Estos tests
verifican la AGREGACION/categorizacion/status, no las reglas fisicas en si
(esas ya tienen su propia cobertura)."""

import time

import pytest
from fastapi.testclient import TestClient

from app.api import routes
from app.core.final_validation import build_plan_validation_result
from app.core.packer import compute_metrics
from app.core.plan_store import PlanRepository
from app.main import app
from app.models.containers import get_container
from app.models.schemas import (
    Dimensions3D,
    OperationalWarning,
    OperationalWarningType,
    PackingResult,
    PlacedPiece,
    RoadSupport,
    RoadWeightConfig,
    UnloadedItem,
)

CONTAINER = get_container("40ft_standard")


def _piece(piece_id, x, y=0, z=0, dx=100, dy=1200, dz=2000, weight=45, stackable=True, max_stack_weight=None):
    return PlacedPiece(
        id=piece_id,
        code=piece_id,
        weight=weight,
        stackable=stackable,
        priority=1,
        max_stack_weight=max_stack_weight,
        x=x,
        y=y,
        z=z,
        dx=dx,
        dy=dy,
        dz=dz,
        orientation_label="P1-a",
        source_width=1200,
        source_height=2000,
        source_thickness=100,
    )


def _unloaded(code="U1"):
    return UnloadedItem(
        id=code,
        code=code,
        description="Unloaded test item",
        dimensions=Dimensions3D(length=1200, width=2000, height=100),
        weight=45,
        reason="No space",
        reason_code="NO_VALID_SPACE",
    )


def _result(placed=None, unloaded=None, operational_warnings=None, container=CONTAINER) -> PackingResult:
    placed = placed or []
    unloaded = unloaded or []
    metrics = compute_metrics(container, placed, unloaded)
    return PackingResult(
        container=container,
        placed=placed,
        unloaded=unloaded,
        metrics=metrics,
        operational_warnings=operational_warnings or [],
    )


def _category(result, category):
    return next(c for c in result.categories if c.category == category)


# ---------------------------------------------------------------------------
# Seccion 38 del pedido: READY / READY_WITH_WARNINGS / NOT_READY
# ---------------------------------------------------------------------------


def test_ready_when_nothing_wrong():
    result = build_plan_validation_result(_result([_piece("a", x=0, y=0)]), CONTAINER)
    assert result.status == "ready"
    assert result.valid is True
    assert result.error_count == 0
    assert result.warning_count == 0
    assert result.unloaded_count == 0
    assert _category(result, "load_space").status == "pass"


def test_ready_with_warnings_due_to_delivery_conflict():
    warning = OperationalWarning(
        type=OperationalWarningType.DELIVERY_SEQUENCE_CONFLICT,
        message="P001 is scheduled for Delivery Sequence 1 but is blocked by P002 (Delivery Sequence 3).",
        item_id="P001",
        blocking_item_id="P002",
    )
    state = _result([_piece("a", x=0, y=0)], operational_warnings=[warning])
    result = build_plan_validation_result(state, CONTAINER)
    assert result.status == "ready_with_warnings"
    assert result.valid is True
    assert result.error_count == 0
    assert result.warning_count == 1
    assert _category(result, "delivery_sequence").status == "warning"
    assert _category(result, "delivery_sequence").count == 1


# ---------------------------------------------------------------------------
# Seccion 39 del pedido: Unloaded Items
# ---------------------------------------------------------------------------


def test_one_unloaded_unit_is_ready_with_warnings_never_an_error():
    state = _result([_piece("a", x=0, y=0)], unloaded=[_unloaded()])
    result = build_plan_validation_result(state, CONTAINER)
    assert result.status == "ready_with_warnings"
    assert result.valid is True
    assert result.error_count == 0
    assert result.unloaded_count == 1
    unloaded_cat = _category(result, "unloaded_items")
    assert unloaded_cat.status == "warning"
    # Fase 6C, seccion 10: nunca se convierte en un error fisico -ni siquiera
    # cuando aparece junto a errores reales (ver test de combinacion abajo).
    assert not any(i.category == "unloaded_items" and i.severity == "error" for i in result.error_issues)


def test_multiple_unloaded_units_are_ready_with_warnings():
    state = _result([_piece("a", x=0, y=0)], unloaded=[_unloaded("U1"), _unloaded("U2"), _unloaded("U3")])
    result = build_plan_validation_result(state, CONTAINER)
    assert result.status == "ready_with_warnings"
    assert result.unloaded_count == 3
    assert "3 units" in result.warning_issues[-1].message


def test_unloaded_reason_text_is_untouched():
    """Fase 6C no reescribe reasons -solo agrega un resumen de conteo
    (seccion 10/39: 'unloaded reasons remain unchanged')."""
    item = _unloaded()
    state = _result([_piece("a", x=0, y=0)], unloaded=[item])
    build_plan_validation_result(state, CONTAINER)
    assert item.reason == "No space"
    assert item.reason_code == "NO_VALID_SPACE"


# ---------------------------------------------------------------------------
# Seccion 38 del pedido: NOT_READY por cada categoria de error representativa
# ---------------------------------------------------------------------------


def test_not_ready_due_to_containment():
    placed = [_piece("a", x=CONTAINER.length - 10, y=0)]  # dx=100 -> se sale
    result = build_plan_validation_result(_result(placed), CONTAINER)
    assert result.status == "not_ready"
    assert result.valid is False
    assert _category(result, "load_space").status == "error"


def test_not_ready_due_to_support():
    floating = _piece("a", x=0, y=0, z=500)  # nada debajo
    result = build_plan_validation_result(_result([floating]), CONTAINER)
    assert result.status == "not_ready"
    assert _category(result, "support_stability").status == "error"


def test_not_ready_due_to_collision():
    placed = [_piece("a", x=0, y=0), _piece("b", x=0, y=0)]
    result = build_plan_validation_result(_result(placed), CONTAINER)
    assert result.status == "not_ready"
    assert _category(result, "collision_clearance").status == "error"


def test_not_ready_due_to_road_weight():
    supports = [
        RoadSupport(id="A", name="Support A", position_x_mm=1000, max_load_kg=100),
        RoadSupport(id="B", name="Support B", position_x_mm=5000, max_load_kg=100),
    ]
    container = get_container("40ft_standard").model_copy(
        update={"road_weight_config": RoadWeightConfig(enabled=True, supports=supports)}
    )
    heavy = _piece("a", x=900, y=0, z=0, weight=50000)
    result = build_plan_validation_result(_result([heavy], container=container), container)
    assert result.status == "not_ready"
    assert _category(result, "road_weight").status == "error"


def test_not_ready_due_to_sequence_cycle():
    """No hay forma de producir un ciclo real con geometria valida (packer/
    manual_move ya lo evitan) -mismo monkeypatch minimo que
    test_final_validation.py/test_sequence.py."""
    import app.core.final_validation as final_validation_module

    placed = [_piece("a", x=0, y=0), _piece("b", x=200, y=0)]
    original = final_validation_module.detect_sequence_cycle
    try:
        final_validation_module.detect_sequence_cycle = lambda deps: ["a", "b"]
        result = build_plan_validation_result(_result(placed), CONTAINER)
    finally:
        final_validation_module.detect_sequence_cycle = original

    assert result.status == "not_ready"
    assert _category(result, "operational_sequence").status == "error"
    # Seccion 11/29 del pedido: nunca aparece TAMBIEN como warning.
    assert not any(i.category == "operational_sequence" for i in result.warning_issues)


# ---------------------------------------------------------------------------
# Seccion 38 del pedido: combinaciones
# ---------------------------------------------------------------------------


def test_hard_error_plus_warnings_is_not_ready():
    floating = _piece("a", x=0, y=0, z=300)  # flota (nada debajo), pero z+dz sigue dentro de la altura (solo 1 error)
    warning = OperationalWarning(
        type=OperationalWarningType.DELIVERY_SEQUENCE_CONFLICT, message="conflict", item_id="a", blocking_item_id="b"
    )
    state = _result([floating], unloaded=[_unloaded()], operational_warnings=[warning])
    result = build_plan_validation_result(state, CONTAINER)
    assert result.status == "not_ready"
    assert result.error_count == 1
    # Los warnings/unloaded siguen contabilizados aunque el status final sea
    # NOT_READY -el error no los "tapa".
    assert result.warning_count == 2


def test_warnings_plus_unloaded_is_ready_with_warnings():
    warning = OperationalWarning(
        type=OperationalWarningType.STACKING_SEQUENCE_CONFLICT, message="conflict", item_id="a", blocking_item_id="b"
    )
    state = _result([_piece("a", x=0, y=0)], unloaded=[_unloaded()], operational_warnings=[warning])
    result = build_plan_validation_result(state, CONTAINER)
    assert result.status == "ready_with_warnings"
    assert result.error_count == 0
    assert result.warning_count == 2


# ---------------------------------------------------------------------------
# Categorizacion / mapeo de OperationalWarningType -> ValidationCategory
# ---------------------------------------------------------------------------


def test_stacking_sequence_conflict_maps_to_delivery_sequence_category():
    warning = OperationalWarning(type=OperationalWarningType.STACKING_SEQUENCE_CONFLICT, message="x", item_id="a")
    result = build_plan_validation_result(_result([_piece("a", x=0, y=0)], operational_warnings=[warning]), CONTAINER)
    assert _category(result, "delivery_sequence").status == "warning"


def test_opening_configuration_limitation_maps_to_operational_sequence_category():
    warning = OperationalWarning(type=OperationalWarningType.OPENING_CONFIGURATION_LIMITATION, message="x", item_id="a")
    result = build_plan_validation_result(_result([_piece("a", x=0, y=0)], operational_warnings=[warning]), CONTAINER)
    cat = _category(result, "operational_sequence")
    assert cat.status == "warning"


def test_road_weight_is_not_applicable_when_not_configured():
    result = build_plan_validation_result(_result([_piece("a", x=0, y=0)]), CONTAINER)
    assert _category(result, "road_weight").status == "not_applicable"


def test_road_weight_passes_when_within_limits():
    supports = [
        RoadSupport(id="A", name="Support A", position_x_mm=1000, max_load_kg=100000),
        RoadSupport(id="B", name="Support B", position_x_mm=5000, max_load_kg=100000),
    ]
    container = get_container("40ft_standard").model_copy(
        update={"road_weight_config": RoadWeightConfig(enabled=True, supports=supports)}
    )
    light = _piece("a", x=3000, y=0, z=0, weight=50)  # entre los 2 supports (1000-5000): reaccion estable en ambos
    result = build_plan_validation_result(_result([light], container=container), container)
    assert _category(result, "road_weight").status == "pass"


# ---------------------------------------------------------------------------
# Seccion 42 del pedido: Excel sigue el mismo gate que los PDF
# ---------------------------------------------------------------------------

client = TestClient(app)


def _window(**overrides):
    defaults = dict(
        code="W1", description="Test", width=1200, height=2000, thickness=100, weight=45, quantity=1,
        item_type="panel", orientation_policy="fixed",
    )
    defaults.update(overrides)
    return defaults


def test_excel_export_succeeds_when_ready():
    r = client.post("/api/pack", json={"items": [_window(quantity=3)], "container_id": "40ft_standard"})
    assert r.status_code == 200
    excel_r = client.get("/api/export-excel")
    assert excel_r.status_code == 200


def test_excel_export_blocked_when_not_ready():
    """Antes de esta fase, Excel exportaba un plan invalido sin ningun
    chequeo (hallazgo de la auditoria 6C) -ahora usa el mismo
    _ensure_exportable que los 3 PDF."""
    r = client.post("/api/pack", json={"items": [_window(quantity=2)], "container_id": "40ft_standard"})
    assert r.status_code == 200
    placed = r.json()["best"]["placed"]
    a, b = placed[0], placed[1]

    state = routes._current_state["result"]
    piece_a = next(p for p in state.placed if p.id == a["id"])
    piece_b = next(p for p in state.placed if p.id == b["id"])
    piece_b.x, piece_b.y, piece_b.z = piece_a.x, piece_a.y, piece_a.z  # fuerza una colision

    validate_r = client.post("/api/report/validate")
    assert validate_r.json()["status"] == "not_ready"

    excel_r = client.get("/api/export-excel")
    assert excel_r.status_code == 422
    assert "errors" in excel_r.json()["detail"]


# ---------------------------------------------------------------------------
# Seccion 40 del pedido: reopen -> validacion sin repack, sin persistir nada
# ---------------------------------------------------------------------------


@pytest.fixture
def plan_client(tmp_path):
    repo = PlanRepository(tmp_path / "test_cubox.db")
    app.dependency_overrides[routes.get_plan_repository] = lambda: repo
    yield TestClient(app)
    app.dependency_overrides.pop(routes.get_plan_repository, None)


def test_reopen_runs_unified_validation_without_repack(plan_client):
    body = {
        "items": [_window(quantity=3)],
        "container_id": "40ft_standard",
        "name": "Validation Reopen Test",
    }
    created_r = plan_client.post("/api/plans", json=body)
    assert created_r.status_code == 200
    created = created_r.json()
    plan_id = created["plan_id"]
    original_positions = {p["id"]: (p["x"], p["y"], p["z"]) for p in created["best"]["placed"]}

    # Simula "Home -> reabrir": reemplaza _current_state por completo, nunca
    # llama a /pack ni al optimizador.
    reopen_r = plan_client.get(f"/api/plans/{plan_id}")
    assert reopen_r.status_code == 200
    reopened = reopen_r.json()
    reopened_positions = {p["id"]: (p["x"], p["y"], p["z"]) for p in reopened["result"]["placed"]}
    assert reopened_positions == original_positions  # geometria EXACTA, sin repack

    validate_r = plan_client.post("/api/report/validate")
    assert validate_r.status_code == 200
    validated = validate_r.json()
    assert validated["status"] == "ready"
    assert validated["valid"] is True

    # Seccion 19 del pedido: nada de esto se persistio -state_json no tiene
    # ningun campo de validacion/status/categorias.
    row = plan_client.app.dependency_overrides[routes.get_plan_repository]().get(plan_id)
    assert "status" not in row.state_json
    assert "categories" not in row.state_json
    assert "operational_warnings" not in row.state_json


# ---------------------------------------------------------------------------
# Seccion 43 del pedido: benchmark (informativo, sin threshold fragil)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("n", [50, 100, 200, 392])
def test_performance_build_plan_validation_result_on_a_floor_grid(n):
    """Seccion 17/43 del pedido: no busca un numero magico -solo confirma
    que build_plan_validation_result (que ahora corre proactivamente en el
    Workspace, no solo antes de exportar) no explota en tiempo con el
    enfoque O(n^2) heredado de validate_for_export. n=392 -> mismo orden de
    magnitud que el plan real usado durante Fase 6B para verificacion manual
    (algunas piezas quedan fuera de los limites del grid sintetico a partir
    de cierto n; no importa para este test, solo mide costo, no corrige
    geometria). Igual criterio que
    test_sequence.py::test_performance_operational_warnings_on_a_200_item_floor_grid."""
    pieces = [_piece(f"p{i}", x=(i % 20) * 200, y=(i // 20) * 200, dx=190, dy=190, dz=100) for i in range(n)]
    start = time.perf_counter()
    build_plan_validation_result(_result(pieces), CONTAINER)
    elapsed = time.perf_counter() - start
    print(f"\nbuild_plan_validation_result @ n={n}: {elapsed:.3f}s")
    assert elapsed < 10.0, f"build_plan_validation_result tardo {elapsed:.2f}s para {n} piezas"
