"""Container Load Report, Loading Guide y Unloading Guide PDF (CUBOX V4,
prioridades 2-6, 8-9)."""

from fastapi.testclient import TestClient

from app.core.pdf_export import (
    CONTAINER_REPORT_COLUMNS,
    _is_palletized_plan,
    build_container_report_pdf,
    build_container_report_table_rows,
    build_guide_step_rows,
    build_loading_guide_pdf,
    build_unloading_guide_pdf,
)
from app.main import app
from app.models.schemas import ContainerReportRequest, ItemType, PlacedPiece, SortReportBy

client = TestClient(app)

# PNG valido minimo (1x1 pixel), usado como placeholder de snapshot en tests.
_TINY_PNG_BASE64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="


def _window(**overrides):
    defaults = dict(
        code="W1",
        description="Ventana test",
        width=1200,
        height=2000,
        thickness=100,
        weight=45,
        quantity=6,
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


def test_container_report_columns_are_exact():
    assert CONTAINER_REPORT_COLUMNS == [
        "Code",
        "Description",
        "Quantity",
        "System",
        "Group",
        "Width",
        "Height",
        "Thickness",
        "Weight",
        "Boxes Inside",
    ]


def test_is_palletized_plan_true_only_for_pallet_item_type():
    pallet_piece = PlacedPiece(
        id="p1", code="P1", weight=780, stackable=True, priority=1, x=0, y=0, z=0, dx=1, dy=1, dz=1,
        orientation_label="P1-a", source_width=1, source_height=1, source_thickness=1, item_type=ItemType.PALLET,
    )
    box_piece = PlacedPiece(
        id="b1", code="B1", weight=10, stackable=True, priority=1, x=0, y=0, z=0, dx=1, dy=1, dz=1,
        orientation_label="P1-a", source_width=1, source_height=1, source_thickness=1, item_type=ItemType.BOX,
    )
    assert _is_palletized_plan([pallet_piece]) is True
    assert _is_palletized_plan([box_piece]) is False
    assert _is_palletized_plan([]) is False


def test_container_report_table_rows_consolidate_identical_pieces():
    from app.api.routes import _get_active_state

    _pack([_window(quantity=6)])
    state = _get_active_state()

    rows = build_container_report_table_rows(state, SortReportBy.GROUP)
    assert len(rows) == 1, "6 piezas identicas deben consolidarse en 1 sola fila"
    code, description, quantity, system, group, width, height, thickness, weight, boxes_inside = rows[0]
    assert code == "W1"
    assert quantity == 6
    assert system == "SysA"
    assert group == "G1"
    assert (width, height, thickness, weight) == (1200, 2000, 100, 45)
    assert boxes_inside == ""  # ventana legacy, sin Boxes Inside definido


def test_container_report_table_rows_sortable_by_group_or_system():
    from app.api.routes import _get_active_state

    _pack(
        [
            _window(code="A", quantity=1, system="SysB", group="G2"),
            _window(code="B", quantity=1, system="SysA", group="G1"),
        ]
    )
    state = _get_active_state()

    by_group = build_container_report_table_rows(state, SortReportBy.GROUP)
    assert [r[0] for r in by_group] == ["B", "A"]  # G1 < G2

    by_system = build_container_report_table_rows(state, SortReportBy.SYSTEM)
    assert [r[0] for r in by_system] == ["B", "A"]  # SysA < SysB


def test_build_container_report_pdf_generates_valid_pdf_bytes():
    from app.api.routes import _get_active_state

    _pack([_window(quantity=3)])
    state = _get_active_state()

    pdf_bytes = build_container_report_pdf(state, ContainerReportRequest(include_overview_image=False))
    assert pdf_bytes[:5] == b"%PDF-"


def test_build_container_report_pdf_includes_operational_sequence_warnings():
    """Fase 6A, seccion 35: no debe explotar (ni exportar en silencio) cuando
    hay warnings operacionales -solo se confirma que sigue generando un PDF
    valido, sin parsear el texto interno (igual criterio que el resto de
    estos tests)."""
    from app.api.routes import _get_active_state
    from app.models.schemas import OperationalWarning, OperationalWarningType

    _pack([_window(quantity=3)])
    state = _get_active_state()
    state.operational_warnings = [
        OperationalWarning(
            type=OperationalWarningType.DELIVERY_SEQUENCE_CONFLICT,
            message="P001 is scheduled for Delivery Sequence 1 but is blocked by P002 (Delivery Sequence 3).",
            item_id="P001",
            blocking_item_id="P002",
        )
    ]

    pdf_bytes = build_container_report_pdf(state, ContainerReportRequest(include_overview_image=False))
    assert pdf_bytes[:5] == b"%PDF-"


def test_export_container_report_pdf_endpoint():
    _pack([_window(quantity=3)])
    r = client.post("/api/report/container-pdf", json={"include_overview_image": False, "sort_by": "group"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content[:5] == b"%PDF-"


def test_export_container_report_pdf_requires_overview_image_when_requested():
    _pack([_window(quantity=3)])
    r = client.post("/api/report/container-pdf", json={"include_overview_image": True})
    assert r.status_code == 400


def test_report_steps_automatic_covers_every_placed_piece():
    result = _pack([_window(quantity=8)])
    r = client.post("/api/report/steps", json={"direction": "load", "step_mode": "automatic"})
    assert r.status_code == 200
    steps = r.json()["steps"]
    flat = [pid for step in steps for pid in step]
    assert sorted(flat) == sorted(p["id"] for p in result["placed"])


def test_report_steps_manual_uses_fixed_pieces_per_step():
    _pack([_window(quantity=7)])
    r = client.post("/api/report/steps", json={"direction": "load", "step_mode": "manual", "pieces_per_step": 3})
    assert r.status_code == 200
    steps = r.json()["steps"]
    assert [len(s) for s in steps] == [3, 3, 1]


def test_report_steps_manual_without_pieces_per_step_rejected():
    _pack([_window(quantity=3)])
    r = client.post("/api/report/steps", json={"direction": "load", "step_mode": "manual"})
    assert r.status_code == 400


def test_report_steps_unload_includes_delivery_annotation_load_does_not():
    """Fase 6B, seccion 2/12: unload_step_info/delivery_sections solo se
    calculan (y exponen) para direction=unload -Delivery Sequence es un
    concepto de descarga, no de carga."""
    _pack([_window(quantity=3, delivery_sequence=1)])
    load_r = client.post("/api/report/steps", json={"direction": "load", "step_mode": "automatic"})
    unload_r = client.post("/api/report/steps", json={"direction": "unload", "step_mode": "automatic"})
    assert load_r.json()["unload_step_info"] is None
    assert load_r.json()["delivery_sections"] is None
    unload_body = unload_r.json()
    assert unload_body["unload_step_info"] is not None
    assert len(unload_body["unload_step_info"]) == len(unload_body["steps"])
    assert unload_body["delivery_sections"] is not None
    assert unload_body["delivery_sections"][0]["label"] == "DELIVERY 1"


def test_report_steps_never_repacks():
    """Fase 6B, seccion 28: llamar a /report/steps repetidamente (Automatic
    o Manual, LOAD o UNLOAD) nunca debe alterar la geometria activa -mismos
    steps, mismas piezas, sin volver a correr /api/pack ni /api/optimize-
    remaining internamente."""
    packed = _pack([_window(quantity=6)])
    positions_before = {p["id"]: (p["x"], p["y"], p["z"]) for p in packed["placed"]}

    for _ in range(3):
        client.post("/api/report/steps", json={"direction": "load", "step_mode": "automatic"})
        client.post("/api/report/steps", json={"direction": "unload", "step_mode": "automatic"})

    from app.api.routes import _get_active_state

    state = _get_active_state()
    positions_after = {p.id: (p.x, p.y, p.z) for p in state.placed}
    assert positions_after == positions_before


def test_unloading_guide_pdf_includes_delivery_sequence_subtitle_without_reordering():
    """Fase 6B, seccion 13/19: el PDF de descarga genera bytes validos con
    items que tienen Delivery Sequence, y build_unloading_guide_pdf usa los
    MISMOS steps que /report/steps (no los reordena)."""
    from app.api.routes import _get_active_state
    from app.models.schemas import ReportMetadata

    _pack([_window(quantity=3, delivery_sequence=1)])
    state = _get_active_state()
    steps_before = client.post("/api/report/steps", json={"direction": "unload", "step_mode": "automatic"}).json()["steps"]

    pdf_bytes = build_unloading_guide_pdf(state, steps_before, [], ReportMetadata())
    assert pdf_bytes[:5] == b"%PDF-"

    steps_after = client.post("/api/report/steps", json={"direction": "unload", "step_mode": "automatic"}).json()["steps"]
    assert steps_after == steps_before


def test_build_guide_step_rows_consolidates_by_code_and_description():
    from app.api.routes import _get_active_state

    _pack([_window(quantity=4)])
    state = _get_active_state()
    pieces_by_id = {p.id: p for p in state.placed}
    step_ids = [p.id for p in state.placed[:4]]

    rows = build_guide_step_rows(pieces_by_id, step_ids)
    assert len(rows) == 1, "las 4 piezas son identicas (mismo code/description) -> 1 sola fila"
    code, description, quantity, boxes_inside = rows[0]
    assert code == "W1"
    assert quantity == 4
    assert boxes_inside == ""  # ventana legacy, sin Boxes Inside definido


def _placed(piece_id, code):
    return PlacedPiece(
        id=piece_id,
        code=code,
        description="d",
        weight=1,
        stackable=True,
        priority=1,
        x=0,
        y=0,
        z=0,
        dx=1,
        dy=1,
        dz=1,
        orientation_label="P1-a",
        source_width=1,
        source_height=1,
        source_thickness=1,
    )


def test_build_loading_guide_pdf_one_image_per_step():
    from app.api.routes import _get_active_state
    from app.core.sequence import compute_load_steps
    from app.models.schemas import ReportMetadata

    _pack([_window(quantity=8)])
    state = _get_active_state()
    steps = compute_load_steps(state.placed, state.container)

    pdf_bytes = build_loading_guide_pdf(state, steps, [_TINY_PNG_BASE64] * len(steps), meta=ReportMetadata())
    assert pdf_bytes[:5] == b"%PDF-"


def test_build_unloading_guide_pdf_one_image_per_step():
    from app.api.routes import _get_active_state
    from app.core.sequence import compute_unload_steps
    from app.models.schemas import ReportMetadata

    _pack([_window(quantity=8)])
    state = _get_active_state()
    steps = compute_unload_steps(state.placed)

    pdf_bytes = build_unloading_guide_pdf(state, steps, [_TINY_PNG_BASE64] * len(steps), meta=ReportMetadata())
    assert pdf_bytes[:5] == b"%PDF-"


def test_export_loading_guide_pdf_endpoint():
    result = _pack([_window(quantity=8)])
    steps_resp = client.post("/api/report/steps", json={"direction": "load", "step_mode": "automatic"})
    n_steps = len(steps_resp.json()["steps"])

    r = client.post(
        "/api/report/loading-guide-pdf",
        json={"step_mode": "automatic", "step_images_png_base64": [_TINY_PNG_BASE64] * n_steps},
    )
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content[:5] == b"%PDF-"
    assert result["metrics"]["loaded_pieces"] > 0  # sanity: hay piezas de verdad detras del PDF


def test_export_unloading_guide_pdf_endpoint():
    _pack([_window(quantity=8)])
    steps_resp = client.post("/api/report/steps", json={"direction": "unload", "step_mode": "automatic"})
    n_steps = len(steps_resp.json()["steps"])

    r = client.post(
        "/api/report/unloading-guide-pdf",
        json={"step_mode": "automatic", "step_images_png_base64": [_TINY_PNG_BASE64] * n_steps},
    )
    assert r.status_code == 200
    assert r.content[:5] == b"%PDF-"


def test_guide_pdf_rejects_when_missing_step_images():
    _pack([_window(quantity=8)])
    r = client.post(
        "/api/report/loading-guide-pdf",
        json={"step_mode": "automatic", "step_images_png_base64": [_TINY_PNG_BASE64]},  # muy pocas
    )
    assert r.status_code == 400


def test_report_validate_endpoint_valid_state():
    _pack([_window(quantity=3)])
    r = client.post("/api/report/validate")
    assert r.status_code == 200
    body = r.json()
    # Fase 6C: valid/errors/warnings mantienen el mismo significado de
    # siempre -los campos nuevos (status/categories/etc.) son aditivos, ver
    # test_final_validation.py para su cobertura dedicada.
    assert body["valid"] is True
    assert body["errors"] == []
    assert body["warnings"] == []
    assert body["status"] == "ready"


def test_pdf_export_rejects_when_state_is_invalid():
    """Defensa en profundidad (seccion 16): si el estado activo se vuelve
    invalido (2 piezas forzadas a colisionar), ningun endpoint de PDF debe
    generar el archivo -422 con la lista de errores en su lugar."""
    from app.api.routes import _current_state

    result = _pack([_window(quantity=2)])
    a, b = result["placed"][0], result["placed"][1]

    state = _current_state["result"]
    piece_a = next(p for p in state.placed if p.id == a["id"])
    piece_b = next(p for p in state.placed if p.id == b["id"])
    piece_b.x, piece_b.y, piece_b.z = piece_a.x, piece_a.y, piece_a.z

    validate_r = client.post("/api/report/validate")
    assert validate_r.status_code == 200
    body = validate_r.json()
    assert body["valid"] is False
    assert any("Colision" in e for e in body["errors"])

    pdf_r = client.post("/api/report/container-pdf", json={"include_overview_image": False})
    assert pdf_r.status_code == 422
    assert any("Colision" in e for e in pdf_r.json()["detail"]["errors"])


def test_loading_and_unloading_guides_are_never_combined():
    """Regresion explicita de la prioridad 5: son 2 endpoints/documentos
    separados, nunca un solo PDF con ambas direcciones."""
    _pack([_window(quantity=4)])
    load_steps = client.post("/api/report/steps", json={"direction": "load", "step_mode": "automatic"}).json()["steps"]
    unload_steps = client.post("/api/report/steps", json={"direction": "unload", "step_mode": "automatic"}).json()["steps"]

    r_load = client.post(
        "/api/report/loading-guide-pdf",
        json={"step_mode": "automatic", "step_images_png_base64": [_TINY_PNG_BASE64] * len(load_steps)},
    )
    r_unload = client.post(
        "/api/report/unloading-guide-pdf",
        json={"step_mode": "automatic", "step_images_png_base64": [_TINY_PNG_BASE64] * len(unload_steps)},
    )
    assert r_load.status_code == 200
    assert r_unload.status_code == 200
    assert r_load.headers["content-disposition"] != r_unload.headers["content-disposition"]


# ==========================================================================
# Load Organization Model Cleanup -Group Unloading Guide
# ==========================================================================

import io  # noqa: E402
import zipfile  # noqa: E402

from app.core.pdf_export import (  # noqa: E402
    _cross_group_prerequisite_note,
    _filter_unload_steps_by_group,
    _group_metrics,
    available_unload_groups,
    build_unloading_guide_pdf_for_group,
)
from app.core.sequence import compute_unload_dependencies, compute_unload_steps  # noqa: E402


def _two_group_blocking_state():
    """2 piezas apiladas lateral (no vertical): "blocker" (Group B) queda
    entre la puerta (x=0) y "blocked" (Group A) -bloqueo fisico real segun
    compute_blocking_pairs (misma funcion que usa Fase 6A, no una nueva).
    Se arma pidiendo 2 unidades reales al packer y despues reasignando
    Group + geometria a mano (mismo patron que
    test_pdf_export_rejects_when_state_is_invalid), y recalculando
    blocked_by con la MISMA compute_unload_dependencies -nunca un segundo
    algoritmo de bloqueo."""
    from app.api.routes import _current_state

    result = _pack([_window(quantity=2, group="")])
    state = _current_state["result"]
    blocked, blocker = state.placed[0], state.placed[1]

    blocker.group, blocked.group = "Group B", "Group A"
    blocker.x, blocker.y, blocker.z = 0.0, 0.0, 0.0
    blocked.x, blocked.y, blocked.z = blocker.dx, 0.0, 0.0
    blocked.dy, blocked.dz = blocker.dy, blocker.dz

    state.blocked_by = compute_unload_dependencies(state.placed)
    return state


def test_available_unload_groups_lists_distinct_groups_sorted():
    _pack([_window(quantity=1, group="Obra B", code="W1"), _window(quantity=1, group="Obra A", code="W2")])
    from app.api.routes import _get_active_state

    assert available_unload_groups(_get_active_state().placed) == ["Obra A", "Obra B"]


def test_available_unload_groups_ignores_items_without_group():
    _pack([_window(quantity=2, group="")])
    from app.api.routes import _get_active_state

    assert available_unload_groups(_get_active_state().placed) == []


def test_filter_unload_steps_by_group_keeps_only_that_group_and_original_indices():
    _pack([_window(quantity=2, group="Obra A", code="W1"), _window(quantity=2, group="Obra B", code="W2")])
    from app.api.routes import _get_active_state

    state = _get_active_state()
    pieces_by_id = {p.id: p for p in state.placed}
    steps = compute_unload_steps(state.placed)

    filtered, original_indices = _filter_unload_steps_by_group(steps, pieces_by_id, "Obra A")

    assert len(filtered) == len(original_indices)
    for step_ids, original_index in zip(filtered, original_indices):
        assert all(pieces_by_id[pid].group == "Obra A" for pid in step_ids)
        assert steps[original_index] is steps[original_index]  # el indice apunta al paso original correcto
    # nunca se agrega una pieza de Obra B a un step filtrado de Obra A
    all_filtered_ids = {pid for step in filtered for pid in step}
    assert all(pieces_by_id[pid].group == "Obra A" for pid in all_filtered_ids)


def test_filter_unload_steps_by_group_never_reorders_kept_steps():
    """El orden relativo de los steps conservados es exactamente el mismo
    que en `steps` -filtrar nunca reordena."""
    _pack([_window(quantity=3, group="Obra A", code="W1"), _window(quantity=3, group="Obra B", code="W2")])
    from app.api.routes import _get_active_state

    state = _get_active_state()
    pieces_by_id = {p.id: p for p in state.placed}
    steps = compute_unload_steps(state.placed)

    _, original_indices = _filter_unload_steps_by_group(steps, pieces_by_id, "Obra A")
    assert original_indices == sorted(original_indices)


def test_cross_group_prerequisite_note_flags_foreign_group_blocker():
    state = _two_group_blocking_state()
    pieces_by_id = {p.id: p for p in state.placed}
    blocked = next(p for p in state.placed if p.group == "Group A")

    note = _cross_group_prerequisite_note(state, [blocked.id], "Group A", pieces_by_id)

    assert note is not None
    assert "OPERATIONAL PREREQUISITE" in note
    blocker = next(p for p in state.placed if p.group == "Group B")
    assert blocker.code in note
    assert "Group B" in note


def test_cross_group_prerequisite_note_ignores_same_group_blocker():
    """Un bloqueador del MISMO Group no genera nota -ya aparece en un paso
    anterior de la misma guia, en el mismo orden fisico."""
    state = _two_group_blocking_state()
    for p in state.placed:
        p.group = "Group A"  # ambas piezas quedan en el mismo Group
    pieces_by_id = {p.id: p for p in state.placed}
    blocked = next(p for p in state.placed if p.x > 0)

    note = _cross_group_prerequisite_note(state, [blocked.id], "Group A", pieces_by_id)
    assert note is None


def test_group_metrics_units_weight_volume_systems_delivery_sequences():
    _pack(
        [
            _window(quantity=2, group="Obra A", code="W1", weight=10, system="SysX", delivery_sequence=1),
            _window(quantity=1, group="Obra A", code="W2", weight=20, system="SysY", delivery_sequence=3),
            _window(quantity=1, group="Obra B", code="W3", weight=999),
        ]
    )
    from app.api.routes import _get_active_state

    metrics = _group_metrics(_get_active_state().placed, "Obra A")
    assert metrics["units"] == 3
    assert metrics["weight"] == 40
    assert metrics["systems"] == ["SysX", "SysY"]
    assert metrics["delivery_sequences"] == [1, 3]
    assert metrics["volume_m3"] > 0


def test_group_metrics_no_delivery_sequence_is_empty_not_zero():
    _pack([_window(quantity=1, group="Obra A")])
    from app.api.routes import _get_active_state

    metrics = _group_metrics(_get_active_state().placed, "Obra A")
    assert metrics["delivery_sequences"] == []


def test_build_unloading_guide_pdf_for_group_generates_valid_pdf():
    from app.api.routes import _get_active_state
    from app.models.schemas import ReportMetadata

    _pack([_window(quantity=4, group="Obra A", code="W1"), _window(quantity=4, group="Obra B", code="W2")])
    state = _get_active_state()
    steps = compute_unload_steps(state.placed)

    pdf_bytes = build_unloading_guide_pdf_for_group(
        state, steps, [_TINY_PNG_BASE64] * len(steps), ReportMetadata(), None, "Obra A"
    )
    assert pdf_bytes[:5] == b"%PDF-"


def test_group_guide_never_repacks_or_moves_pieces():
    from app.api.routes import _get_active_state
    from app.models.schemas import ReportMetadata

    _pack([_window(quantity=4, group="Obra A", code="W1"), _window(quantity=4, group="Obra B", code="W2")])
    state = _get_active_state()
    positions_before = {p.id: (p.x, p.y, p.z) for p in state.placed}
    steps = compute_unload_steps(state.placed)

    build_unloading_guide_pdf_for_group(state, steps, [_TINY_PNG_BASE64] * len(steps), ReportMetadata(), None, "Obra A")

    state_after = _get_active_state()
    positions_after = {p.id: (p.x, p.y, p.z) for p in state_after.placed}
    assert positions_after == positions_before


def test_export_unloading_guide_pdf_by_group_endpoint_single_group_returns_pdf():
    _pack([_window(quantity=4, group="Obra A", code="W1"), _window(quantity=4, group="Obra B", code="W2")])
    steps_resp = client.post("/api/report/steps", json={"direction": "unload", "step_mode": "automatic"})
    n_steps = len(steps_resp.json()["steps"])

    r = client.post(
        "/api/report/unloading-guide-pdf-by-group",
        json={
            "step_mode": "automatic",
            "step_images_png_base64": [_TINY_PNG_BASE64] * n_steps,
            "groups": ["Obra A"],
        },
    )
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content[:5] == b"%PDF-"
    assert "Obra A" in r.headers["content-disposition"]


def test_export_unloading_guide_pdf_by_group_endpoint_multiple_groups_returns_zip():
    _pack([_window(quantity=4, group="Obra A", code="W1"), _window(quantity=4, group="Obra B", code="W2")])
    steps_resp = client.post("/api/report/steps", json={"direction": "unload", "step_mode": "automatic"})
    n_steps = len(steps_resp.json()["steps"])

    r = client.post(
        "/api/report/unloading-guide-pdf-by-group",
        json={
            "step_mode": "automatic",
            "step_images_png_base64": [_TINY_PNG_BASE64] * n_steps,
            "groups": ["Obra A", "Obra B"],
        },
    )
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"

    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = zf.namelist()
    assert len(names) == 2
    for name in names:
        assert zf.read(name)[:5] == b"%PDF-"
    # nunca se fusionan varios Groups en un solo documento -cada PDF es
    # independiente dentro del ZIP.
    assert any("Obra A" in n for n in names)
    assert any("Obra B" in n for n in names)


def test_export_unloading_guide_pdf_by_group_endpoint_requires_at_least_one_group():
    _pack([_window(quantity=2, group="Obra A")])
    r = client.post(
        "/api/report/unloading-guide-pdf-by-group",
        json={"step_mode": "automatic", "step_images_png_base64": [_TINY_PNG_BASE64] * 2, "groups": []},
    )
    assert r.status_code == 400


def test_export_unloading_guide_pdf_by_group_endpoint_rejects_unknown_group():
    _pack([_window(quantity=2, group="Obra A")])
    r = client.post(
        "/api/report/unloading-guide-pdf-by-group",
        json={
            "step_mode": "automatic",
            "step_images_png_base64": [_TINY_PNG_BASE64] * 2,
            "groups": ["Obra Inexistente"],
        },
    )
    assert r.status_code == 400


def test_list_unload_groups_endpoint():
    _pack([_window(quantity=1, group="Obra B", code="W1"), _window(quantity=1, group="Obra A", code="W2")])
    r = client.get("/api/report/unload-groups")
    assert r.status_code == 200
    assert r.json() == ["Obra A", "Obra B"]


def test_full_unloading_guide_endpoint_unaffected_by_group_guide_feature():
    """Regresion explicita: el Full Unloading Guide (sin `groups`) sigue
    generando exactamente el mismo documento de siempre, sin exigir ni
    aceptar el campo `groups`."""
    _pack([_window(quantity=4, group="Obra A", code="W1"), _window(quantity=4, group="Obra B", code="W2")])
    steps_resp = client.post("/api/report/steps", json={"direction": "unload", "step_mode": "automatic"})
    n_steps = len(steps_resp.json()["steps"])

    r = client.post(
        "/api/report/unloading-guide-pdf",
        json={"step_mode": "automatic", "step_images_png_base64": [_TINY_PNG_BASE64] * n_steps},
    )
    assert r.status_code == 200
    assert r.content[:5] == b"%PDF-"
