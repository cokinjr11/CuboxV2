"""Container Load Report, Loading Guide y Unloading Guide PDF (CUBOX V4,
prioridades 2-6, 8-9)."""

from fastapi.testclient import TestClient

from app.core.pdf_export import (
    CONTAINER_REPORT_COLUMNS,
    _batch_steps_for_pages,
    build_container_report_pdf,
    build_container_report_table_rows,
    build_guide_step_rows,
    build_loading_guide_pdf,
    build_unloading_guide_pdf,
)
from app.main import app
from app.models.schemas import ContainerReportRequest, PlacedPiece, SortReportBy

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
    ]


def test_container_report_table_rows_consolidate_identical_pieces():
    from app.api.routes import _get_active_state

    _pack([_window(quantity=6)])
    state = _get_active_state()

    rows = build_container_report_table_rows(state, SortReportBy.GROUP)
    assert len(rows) == 1, "6 piezas identicas deben consolidarse en 1 sola fila"
    code, description, quantity, system, group, width, height, thickness, weight = rows[0]
    assert code == "W1"
    assert quantity == 6
    assert system == "SysA"
    assert group == "G1"
    assert (width, height, thickness, weight) == (1200, 2000, 100, 45)


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


def test_build_guide_step_rows_consolidates_by_code_and_description():
    from app.api.routes import _get_active_state

    _pack([_window(quantity=4)])
    state = _get_active_state()
    pieces_by_id = {p.id: p for p in state.placed}
    step_ids = [p.id for p in state.placed[:4]]

    rows = build_guide_step_rows(pieces_by_id, step_ids)
    assert len(rows) == 1, "las 4 piezas son identicas (mismo code/description) -> 1 sola fila"
    code, description, quantity = rows[0]
    assert code == "W1"
    assert quantity == 4


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


def test_batch_steps_for_pages_groups_up_to_four_per_page():
    """Seccion 34: 8 steps NO deben producir 8 paginas -por defecto hasta 4
    steps/pagina (grid 2x2), asi que deberian caer en ~2 paginas."""
    steps = [[f"p{i}"] for i in range(8)]
    pages = _batch_steps_for_pages(steps, pieces_by_id={})
    assert len(pages) == 2
    assert pages == [[0, 1, 2, 3], [4, 5, 6, 7]]


def test_batch_steps_for_pages_keeps_the_odd_last_step_alone():
    steps = [[f"p{i}"] for i in range(5)]
    pages = _batch_steps_for_pages(steps, pieces_by_id={})
    assert pages[-1] == [4]


def test_batch_steps_for_pages_drops_to_two_per_page_when_rows_are_many():
    """Si un step del lote de 4 tiene demasiadas filas (piezas distintas),
    ese lote pasa a 2 steps/pagina en vez de 4 para no volverse ilegible."""
    many_ids = [f"id{i}" for i in range(10)]
    pieces_by_id = {pid: _placed(pid, f"C{i}") for i, pid in enumerate(many_ids)}
    steps = [many_ids, ["a"], ["b"], ["c"]]  # el primer step tiene 10 filas distintas
    pages = _batch_steps_for_pages(steps, pieces_by_id)
    assert len(pages[0]) == 2


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
    assert r.json() == {"valid": True, "errors": []}


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
