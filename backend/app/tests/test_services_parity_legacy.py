"""Integracion NAGSA, A2: PARIDAD entre services/ y los endpoints actuales de
/api (antes de que A3 haga que /api delegue en services/).

Prueba que la logica se MOVIO sin cambiar: la misma secuencia de operaciones
por HTTP (modo local, estado en `_current_state`) y por services/ (funciones
puras sobre PlanState) produce el mismo resultado y los mismos errores.

Temporal por diseno: despues de A3 /api usa services/ y esta comparacion
pasa a ser trivial; la paridad duradera es /api vs /api/v1 (A6). Se
reemplaza entonces (ver docs/CHECKLIST_INTEGRACION.md)."""

import pytest
from fastapi.testclient import TestClient

from app.api import routes
from app.main import app
from app.models.schemas import (
    GuideReportRequest,
    InsertPieceRequest,
    MoveRequest,
    OptimizationMode,
    PackRequest,
    ReportDirection,
    ReportStepsRequest,
    SetTiltRequest,
    StepMode,
    WeightBalanceMode,
)
from app.services import editing, packing, plan_config, reporting
from app.services.derivation import EvaluatedPlan, derive_result, evaluate
from app.services.errors import PlanOpError, PlanOpErrorKind
from app.services.plan_state import PlanState, reserved_zones_to_state

client = TestClient(app)

# PNG valido minimo (1x1), mismo placeholder que test_pdf_export.py.
_TINY_PNG_BASE64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="

_KIND_BY_STATUS = {
    400: PlanOpErrorKind.INVALID_INPUT,
    404: PlanOpErrorKind.NOT_FOUND,
    409: PlanOpErrorKind.CONFLICT,
    422: PlanOpErrorKind.NOT_EXPORTABLE,
}

_PANELS = {
    "items": [
        {"code": "W1", "width": 1200, "height": 2000, "thickness": 80, "weight": 40, "quantity": 6, "item_type": "panel", "group": "G1", "system": "Window Wall"},
        {"code": "W2", "width": 900, "height": 1500, "thickness": 100, "weight": 25, "quantity": 6, "item_type": "panel", "group": "G2", "system": "Picture Window", "delivery_sequence": 1},
        {"code": "W3", "width": 2500, "height": 2500, "thickness": 80, "weight": 60, "quantity": 1, "item_type": "panel", "group": "G3"},
    ],
    "container_id": "20ft_standard",
    "enable_central_aisle": True,
    "aisle_width_mm": 500,
    "optimization_mode": "best_space",
    "weight_balance_mode": "important",
    "loading_anchor": "back_left",
    "plan_handling_rules": {"default_stackable": True},
}

_BOXES = {
    "items": [
        {"code": "B1", "width": 600, "height": 400, "thickness": 400, "weight": 30, "quantity": 20, "item_type": "box", "group": "A"},
        {"code": "B2", "width": 1200, "height": 800, "thickness": 600, "weight": 90, "quantity": 8, "item_type": "box", "group": "B", "stackable": False},
    ],
    "custom_load_space": {"name": "Camion 6m", "load_space_type": "truck", "length": 6000, "width": 2400, "height": 2400, "max_weight": 10000},
    "optimization_mode": "best_space",
}

SCENARIOS = {"panels_aisle": _PANELS, "boxes_custom_truck": _BOXES}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _session_state() -> PlanState:
    """El estado local actual (`_current_state`) expresado como PlanState."""
    s = routes._current_state
    return PlanState(
        load_space=s["load_space"],
        plan_handling_rules=s["plan_handling_rules"],
        clearance=s["clearance"],
        optimization_mode=s["optimization_mode"],
        weight_balance_mode=s["weight_balance_mode"],
        loading_anchor=s["loading_anchor"],
        reserved_zones=reserved_zones_to_state(s["reserved_zones"]),
        placed=[p.model_copy(deep=True) for p in s["result"].placed],
        unloaded=[u.model_copy(deep=True) for u in s["result"].unloaded],
    )


def _dump(model) -> dict:
    return model.model_dump(mode="json")


def _legacy(method: str, url: str, body=None):
    r = client.request(method, f"/api{url}", json=body)
    return r.status_code, r.json() if r.headers.get("content-type", "").startswith("application/json") else r.content


def _assert_same_outcome(legacy, service_call):
    """Mismo exito (mismo JSON) o mismo error (mismo tipo, mismo detalle)."""
    status, body = legacy
    if status == 200:
        assert _dump(service_call()) == body
        return
    with pytest.raises(PlanOpError) as exc:
        service_call()
    assert exc.value.kind == _KIND_BY_STATUS[status]
    assert exc.value.detail == body["detail"]


@pytest.fixture(params=list(SCENARIOS), ids=list(SCENARIOS))
def scenario(request):
    body = SCENARIOS[request.param]
    r = client.post("/api/pack", json=body)
    assert r.status_code == 200, r.text
    return body, r.json()


# ---------------------------------------------------------------------------
# Cubicaje
# ---------------------------------------------------------------------------


def _without_custom_ids(value):
    """build_custom_load_space genera `custom-{uuid4()[:8]}` en CADA llamada
    (models/containers.py: solo debe ser unico dentro del mismo request) -dos
    packs identicos nunca comparten ese id, asi que se neutraliza."""
    if isinstance(value, dict):
        return {k: ("custom-*" if k == "id" and isinstance(v, str) and v.startswith("custom-") else _without_custom_ids(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_without_custom_ids(v) for v in value]
    return value


def test_pack_matches_legacy(scenario):
    body, legacy_response = scenario
    outcome = packing.pack(PackRequest.model_validate(body))
    assert _without_custom_ids(_dump(outcome.to_response())) == _without_custom_ids(legacy_response)
    assert _without_custom_ids(_dump(outcome.plan.state)) == _without_custom_ids(_dump(_session_state()))


def test_derive_result_matches_legacy_active_result(scenario):
    _body, legacy_response = scenario
    assert _dump(derive_result(_session_state())) == legacy_response["best"]


def test_optimize_remaining_with_overrides_matches_legacy(scenario):
    _body, legacy_response = scenario
    first = legacy_response["best"]["placed"][0]["id"]
    assert _legacy("POST", "/lock-piece", {"piece_id": first})[0] == 200
    state = _session_state()

    overrides = {"optimization_mode": "keep_systems", "weight_balance_mode": "ignore"}
    status, body = _legacy("POST", "/optimize-remaining", overrides)
    assert status == 200, body

    configured = plan_config.apply_overrides(
        state, optimization_mode=OptimizationMode.KEEP_SYSTEMS, weight_balance_mode=WeightBalanceMode.IGNORE
    )
    outcome = packing.optimize_remaining(configured)
    assert _dump(outcome.to_response()) == body
    assert _dump(outcome.plan.state) == _dump(_session_state())


@pytest.mark.parametrize("mode", ["keep_groups", "keep_systems"])
def test_pack_section_3d_modes_match_legacy(mode):
    """Keep Groups / Keep Systems (motor Section-First 3D) aparte y una sola
    vez cada uno: son los caminos caros (Keep Groups: select_complete_groups
    hace repacking acotado O(clusters^2), ~5 s por pack aun con pocos items,
    ver DOCUMENTACION.md seccion 13)."""
    body = {**_PANELS, "optimization_mode": mode}
    r = client.post("/api/pack", json=body)
    assert r.status_code == 200, r.text
    outcome = packing.pack(PackRequest.model_validate(body))
    assert _dump(outcome.to_response()) == r.json()
    assert _dump(outcome.plan.state) == _dump(_session_state())


def test_unknown_container_matches_legacy_error():
    body = {"items": _PANELS["items"], "container_id": "no-existe"}
    _assert_same_outcome(_legacy("POST", "/pack", body), lambda: packing.pack(PackRequest.model_validate(body)).to_response())
    body = {"items": _PANELS["items"]}
    _assert_same_outcome(_legacy("POST", "/pack", body), lambda: packing.pack(PackRequest.model_validate(body)).to_response())


# ---------------------------------------------------------------------------
# Edicion: cada operacion, exito y error, contra el endpoint legacy
# ---------------------------------------------------------------------------


def _edit_parity(url: str, body: dict, service_fn):
    before = _session_state()
    legacy = _legacy("POST", url, body)
    _assert_same_outcome(legacy, lambda: derive_result(service_fn(before)))
    if legacy[0] == 200:
        assert _dump(service_fn(before)) == _dump(_session_state())


def test_edit_operations_match_legacy(scenario):
    _body, legacy_response = scenario
    placed = legacy_response["best"]["placed"]
    p0, p1 = placed[0], placed[1]

    same_place = {"piece_id": p0["id"], **{k: p0[k] for k in ("x", "y", "z", "dx", "dy", "dz")}}
    onto_p1 = {**same_place, "x": p1["x"], "y": p1["y"], "z": p1["z"]}

    # validate-move no modifica nada: comparar la respuesta directa
    for move in (same_place, onto_p1):
        status, body = _legacy("POST", "/validate-move", move)
        assert status == 200
        assert _dump(editing.validate_move(_session_state(), MoveRequest(**move))) == body

    _edit_parity("/apply-move", same_place, lambda s: editing.move_piece(s, MoveRequest(**same_place)))
    _edit_parity("/apply-move", onto_p1, lambda s: editing.move_piece(s, MoveRequest(**onto_p1)))  # colision -> 409
    _edit_parity("/rotate-piece", {"piece_id": p0["id"]}, lambda s: editing.rotate_piece(s, p0["id"]))
    _edit_parity("/turn-piece", {"piece_id": p0["id"]}, lambda s: editing.turn_piece(s, p0["id"]))
    _edit_parity("/set-tilt", {"piece_id": p0["id"], "tilt_angle": 10}, lambda s: editing.set_tilt(s, SetTiltRequest(piece_id=p0["id"], tilt_angle=10)))
    _edit_parity("/lock-piece", {"piece_id": p1["id"]}, lambda s: editing.lock_piece(s, p1["id"]))
    _edit_parity("/remove-piece", {"piece_id": p1["id"]}, lambda s: editing.remove_piece(s, p1["id"]))  # locked -> 409
    _edit_parity("/unlock-piece", {"piece_id": p1["id"]}, lambda s: editing.unlock_piece(s, p1["id"]))
    _edit_parity("/remove-piece", {"piece_id": p1["id"]}, lambda s: editing.remove_piece(s, p1["id"]))
    _edit_parity("/remove-piece", {"piece_id": "no-existe"}, lambda s: editing.remove_piece(s, "no-existe"))  # 404

    back = {"unloaded_id": p1["id"], **{k: p1[k] for k in ("x", "y", "z", "dx", "dy", "dz")}}
    _edit_parity("/insert-piece", back, lambda s: editing.insert_piece(s, InsertPieceRequest(**back)))
    missing = {**back, "unloaded_id": "no-existe"}
    _edit_parity("/insert-piece", missing, lambda s: editing.insert_piece(s, InsertPieceRequest(**missing)))  # 404


def test_editing_never_mutates_its_input(scenario):
    _body, legacy_response = scenario
    p0 = legacy_response["best"]["placed"][0]
    state = _session_state()
    snapshot = _dump(state)
    editing.lock_piece(state, p0["id"])
    editing.remove_piece(state, p0["id"])
    editing.move_piece(state, MoveRequest(piece_id=p0["id"], **{k: p0[k] for k in ("x", "y", "z", "dx", "dy", "dz")}))
    assert _dump(state) == snapshot


# ---------------------------------------------------------------------------
# Validacion y pasos
# ---------------------------------------------------------------------------


def _session_plan() -> EvaluatedPlan:
    return EvaluatedPlan(state=_session_state(), result=routes._current_state["result"])


def test_validation_matches_legacy(scenario):
    status, body = _legacy("POST", "/report/validate")
    assert status == 200
    assert _dump(reporting.validate_plan(_session_plan())) == body
    assert _dump(reporting.validate_plan(evaluate(_session_state()))) == body


@pytest.mark.parametrize(
    "req",
    [
        {"direction": "load"},
        {"direction": "unload"},
        {"direction": "load", "step_mode": "manual", "pieces_per_step": 3},
        {"direction": "unload", "step_mode": "manual", "pieces_per_step": 2},
        {"direction": "load", "step_mode": "manual"},  # 400
    ],
)
def test_report_steps_match_legacy(scenario, req):
    _assert_same_outcome(_legacy("POST", "/report/steps", req), lambda: reporting.report_steps(_session_plan(), ReportStepsRequest(**req)))


def test_unload_groups_match_legacy(scenario):
    status, body = _legacy("GET", "/report/unload-groups")
    assert status == 200
    assert reporting.unload_groups(_session_plan()) == body


def test_export_gate_matches_legacy_for_not_ready_plan():
    """Un plan con colision (NOT_READY): el Excel legacy responde 422 y el
    gate de services levanta NOT_EXPORTABLE con el mismo detalle."""
    r = client.post("/api/pack", json=_BOXES)
    placed = r.json()["best"]["placed"]
    a, b = placed[0], placed[1]
    routes._current_state["result"].placed[1].x = a["x"]  # forzar colision sin pasar por validacion
    routes._current_state["result"].placed[1].y = a["y"]
    routes._current_state["result"].placed[1].z = a["z"]

    r = client.get("/api/export-excel")
    assert r.status_code == 422
    with pytest.raises(PlanOpError) as exc:
        reporting.build_excel(_session_plan(), allow_export_with_errors=False)
    assert exc.value.kind == PlanOpErrorKind.NOT_EXPORTABLE
    assert exc.value.detail == r.json()["detail"]

    assert client.get("/api/export-excel?allow_export_with_errors=true").status_code == 200
    assert reporting.build_excel(_session_plan(), allow_export_with_errors=True)[:2] == b"PK"


def test_guide_preconditions_match_legacy(scenario):
    no_images = {"step_mode": "automatic", "step_images_png_base64": []}
    status, body = _legacy("POST", "/report/loading-guide-pdf", no_images)
    with pytest.raises(PlanOpError) as exc:
        reporting.build_loading_guide(_session_plan(), GuideReportRequest(**no_images))
    assert (_KIND_BY_STATUS[status], body["detail"]) == (exc.value.kind, exc.value.detail)

    no_groups = {"groups": [], "step_images_png_base64": []}
    status, body = _legacy("POST", "/report/unloading-guide-pdf-by-group", no_groups)
    with pytest.raises(PlanOpError) as exc:
        reporting.build_unloading_guides_by_group(_session_plan(), GuideReportRequest(**no_groups))
    assert (_KIND_BY_STATUS[status], body["detail"]) == (exc.value.kind, exc.value.detail)


def test_guide_pdfs_are_produced_from_the_same_steps(scenario):
    plan = _session_plan()
    steps = reporting.compute_steps(plan, ReportDirection.LOAD, StepMode.AUTOMATIC, None)
    images = [_TINY_PNG_BASE64] * len(steps)
    req = GuideReportRequest(step_images_png_base64=images)
    legacy = client.post("/api/report/loading-guide-pdf", json=req.model_dump(mode="json"))
    assert legacy.status_code == 200
    assert legacy.content[:4] == b"%PDF"
    assert reporting.build_loading_guide(plan, req)[:4] == b"%PDF"
