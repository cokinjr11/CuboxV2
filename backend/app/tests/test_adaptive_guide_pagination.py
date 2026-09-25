"""CUBOX 2.0 -- Adaptive Operational Guide Pagination.

Reemplaza el "2 Steps/pagina fijo" anterior (_batch_steps_for_pages,
eliminada junto con sus tests en test_pdf_export.py) por una clasificacion
de complejidad por Step (compact/standard/large) + empaquetado real por
altura medida (_pack_step_cards). Igual convencion que
test_operational_guide_redesign_v2.py: se inspeccionan los objetos
Flowable/PageBreak/KeepTogether que devuelven las funciones ANTES de
`doc.build()` (no hay pypdf/pdfminer en este entorno para parsear el PDF
final de forma confiable)."""

import base64
import io

from fastapi.testclient import TestClient
from reportlab.platypus import KeepTogether, PageBreak, Spacer

from app.core.pdf_export import (
    _classify_step_size,
    _GUIDE_CONTENT_HEIGHT,
    _GUIDE_CONTENT_WIDTH,
    _MAX_STEPS_PER_PAGE,
    _pack_step_cards,
    _STEP_SIZE_IMAGE_DIMS,
    build_loading_guide_pdf,
    build_unloading_guide_pdf,
)
from app.main import app
from app.models.schemas import UnloadStepDeliveryInfo

client = TestClient(app)


def _landscape_png_base64(width: int = 560, height: int = 215) -> str:
    """PNG panoramico real (mismo aspect ratio que GUIDE_CAPTURE_ASPECT del
    frontend, ~2.6) -a diferencia de un placeholder 1x1, esto ejercita
    _fit_image_dims con dimensiones realistas, para que el alto MEDIDO de
    cada Step Card en los tests de abajo sea representativo de un PDF real."""
    from PIL import Image as PILImage

    buf = io.BytesIO()
    PILImage.new("RGB", (width, height), color=(10, 10, 10)).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


_LANDSCAPE_PNG = _landscape_png_base64()


def _window(**overrides):
    defaults = dict(
        code="W1", description="Ventana test", width=1200, height=2000, thickness=100, weight=45,
        quantity=4, system="SysA", group="G1", stackable=True, priority=1,
    )
    defaults.update(overrides)
    return defaults


def _pack(items, container_id="40ft_standard"):
    r = client.post("/api/pack", json={"items": items, "container_id": container_id, "optimization_mode": "best_space"})
    assert r.status_code == 200
    return r.json()["best"]


# ==========================================================================
# Classification rule
# ==========================================================================


def test_classify_compact_few_rows_no_warnings():
    assert _classify_step_size(1, 0, False) == "compact"
    assert _classify_step_size(3, 0, False) == "compact"


def test_classify_standard_at_four_rows_boundary():
    assert _classify_step_size(4, 0, False) == "standard"
    assert _classify_step_size(5, 0, False) == "standard"


def test_classify_standard_with_one_or_two_warnings_even_if_rows_are_few():
    assert _classify_step_size(1, 1, False) == "standard"
    assert _classify_step_size(2, 2, False) == "standard"


def test_classify_large_above_five_rows():
    assert _classify_step_size(6, 0, False) == "large"
    assert _classify_step_size(20, 0, False) == "large"


def test_classify_large_with_three_or_more_warnings_even_if_rows_are_few():
    assert _classify_step_size(1, 3, False) == "large"


def test_classify_large_when_prerequisite_note_present_regardless_of_rows():
    """Una nota de prerequisito cruzado de Group (Group Guide) es siempre
    contenido "sustancial" (seccion "Complexity classification" del pedido)
    -aunque el step tenga una sola fila y cero warnings."""
    assert _classify_step_size(1, 0, True) == "large"


# ==========================================================================
# Compact / Standard / Large dimensions
# ==========================================================================


def test_image_heights_within_requested_ranges():
    _, compact_h = _STEP_SIZE_IMAGE_DIMS["compact"]
    _, standard_h = _STEP_SIZE_IMAGE_DIMS["standard"]
    _, large_h = _STEP_SIZE_IMAGE_DIMS["large"]
    assert 110 <= compact_h <= 125
    assert 170 <= standard_h <= 190
    assert 210 <= large_h <= 215


def test_image_sizes_preserve_the_guide_capture_aspect_ratio():
    """Cada tamano debe mantener el mismo aspect ratio panoramico (~2.6,
    GUIDE_CAPTURE_ASPECT del frontend) -reducir el ancho en una proporcion
    distinta al alto reintroduciria el bug de espacio en blanco lateral que
    la correccion de camara anterior ya resolvio (_fit_image_dims es
    aspect-preserving: un box con aspect distinto al nativo deja margenes)."""
    target_aspect = _STEP_SIZE_IMAGE_DIMS["large"][0] / _STEP_SIZE_IMAGE_DIMS["large"][1]
    for size, (w, h) in _STEP_SIZE_IMAGE_DIMS.items():
        assert abs((w / h) - target_aspect) < 0.02, f"{size} rompe el aspect ratio panoramico"


def test_large_image_dimensions_unchanged_from_camera_correction_task():
    """No camera/render work en esta tarea -"large" debe seguir siendo
    EXACTAMENTE 560x215, el mismo valor ya verificado empiricamente con la
    correccion de camara/encuadre de la tarea anterior."""
    assert _STEP_SIZE_IMAGE_DIMS["large"] == (560, 215)


def test_compact_image_smaller_than_standard_smaller_than_large():
    assert _STEP_SIZE_IMAGE_DIMS["compact"][1] < _STEP_SIZE_IMAGE_DIMS["standard"][1] < _STEP_SIZE_IMAGE_DIMS["large"][1]


# ==========================================================================
# _pack_step_cards -- packing architecture
# ==========================================================================


def _steps_per_page(story: list) -> list[int]:
    """Cuenta cuantos KeepTogether (= Step Cards completos) caen entre cada
    PageBreak -misma forma en que doc.build() los va a paginar, sin
    necesitar un parser de PDF."""
    pages: list[int] = [0]
    for flowable in story:
        if isinstance(flowable, PageBreak):
            pages.append(0)
        elif isinstance(flowable, KeepTogether):
            pages[-1] += 1
    return pages


def _meta(step_number, total_steps, n_rows, n_conflicts=0, has_prereq=False, direction="load"):
    """Un steps_meta minimo con exactamente `n_rows` piezas DISTINTAS (cada
    una su propio code, para que build_guide_step_rows cuente n_rows filas
    reales -nunca se le miente a la clasificacion) y, opcionalmente, un
    UnloadStepDeliveryInfo con `n_conflicts` mensajes."""
    from app.models.schemas import PlacedPiece

    step_ids = [f"s{step_number}-{i}" for i in range(n_rows)]
    pieces_by_id = {
        pid: PlacedPiece(
            id=pid, code=f"C{step_number}-{i}", description="d", weight=1, stackable=True, priority=1,
            x=0, y=0, z=0, dx=1, dy=1, dz=1, orientation_label="P1-a",
            source_width=1, source_height=1, source_thickness=1,
        )
        for i, pid in enumerate(step_ids)
    }
    delivery_info = None
    if direction == "unload":
        delivery_info = UnloadStepDeliveryInfo(
            delivery_sequences=[1], is_mixed=False,
            conflict_messages=[f"conflict {i}" for i in range(n_conflicts)],
            conflict_types=[],
        )
    return {
        "step_number": step_number, "total_steps": total_steps, "direction": direction,
        "step_ids": step_ids, "image": _LANDSCAPE_PNG, "pieces_by_id": pieces_by_id,
        "is_pallet_plan": False, "has_tilt": False, "delivery_info": delivery_info,
        "extra_notes": ["OPERATIONAL PREREQUISITE: ..."] if has_prereq else None,
    }


def test_pack_step_cards_fits_many_compact_steps_per_page_up_to_the_cap():
    """8 Steps compact (1 fila, sin warnings) -el pedido pide HASTA 4 por
    pagina para Steps compact -nunca 1 o 2 como el fijo anterior."""
    steps_meta = [_meta(i + 1, 8, n_rows=1) for i in range(8)]
    story = _pack_step_cards(steps_meta, __import__("reportlab.lib.styles", fromlist=["getSampleStyleSheet"]).getSampleStyleSheet(), _GUIDE_CONTENT_WIDTH, _GUIDE_CONTENT_HEIGHT)
    pages = _steps_per_page(story)
    assert sum(pages) == 8
    assert max(pages) <= _MAX_STEPS_PER_PAGE
    assert max(pages) > 2, f"steps compact deberian superar el viejo fijo de 2/pagina, dio {pages}"


def test_pack_step_cards_never_exceeds_four_per_page_even_if_more_would_fit():
    """Regla dura: "Never exceed 4 Steps per page" -aunque Steps triviales
    (1 sola fila) tecnicamente entrarian mas de 4 en una pagina A4."""
    from reportlab.lib.styles import getSampleStyleSheet

    steps_meta = [_meta(i + 1, 20, n_rows=1) for i in range(20)]
    story = _pack_step_cards(steps_meta, getSampleStyleSheet(), _GUIDE_CONTENT_WIDTH, _GUIDE_CONTENT_HEIGHT)
    pages = _steps_per_page(story)
    assert all(p <= 4 for p in pages), pages
    assert sum(pages) == 20


def test_pack_step_cards_large_steps_get_one_per_page():
    """Steps "large" (>5 filas) deben caer 1 por pagina -no se fuerza, pero
    su altura real (imagen 215pt + 6+ filas de tabla) no deja lugar a otro
    Step mas en la misma pagina A4."""
    from reportlab.lib.styles import getSampleStyleSheet

    steps_meta = [_meta(i + 1, 3, n_rows=8) for i in range(3)]
    story = _pack_step_cards(steps_meta, getSampleStyleSheet(), _GUIDE_CONTENT_WIDTH, _GUIDE_CONTENT_HEIGHT)
    pages = _steps_per_page(story)
    assert pages == [1, 1, 1], pages


def test_pack_step_cards_mixed_complexity_adapts_per_page():
    """Escenario mixto -2 compact, 1 large, 2 compact- debe dar mas de 1
    Step en las paginas compact y 1 solo en la pagina large, NUNCA fijo."""
    from reportlab.lib.styles import getSampleStyleSheet

    steps_meta = [
        _meta(1, 5, n_rows=1), _meta(2, 5, n_rows=2),
        _meta(3, 5, n_rows=10),
        _meta(4, 5, n_rows=1), _meta(5, 5, n_rows=1),
    ]
    story = _pack_step_cards(steps_meta, getSampleStyleSheet(), _GUIDE_CONTENT_WIDTH, _GUIDE_CONTENT_HEIGHT)
    pages = _steps_per_page(story)
    assert sum(pages) == 5
    assert any(p >= 2 for p in pages), f"deberia haber al menos una pagina con 2+ steps compact, dio {pages}"


def test_pack_step_cards_never_splits_a_step_across_pages():
    """Cada Step Card sigue envuelto en KeepTogether -atomicidad garantizada
    aunque el calculo de altura tuviera algun margen de error."""
    from reportlab.lib.styles import getSampleStyleSheet

    steps_meta = [_meta(i + 1, 6, n_rows=(i % 6) + 1) for i in range(6)]
    story = _pack_step_cards(steps_meta, getSampleStyleSheet(), _GUIDE_CONTENT_WIDTH, _GUIDE_CONTENT_HEIGHT)
    keep_togethers = [f for f in story if isinstance(f, KeepTogether)]
    assert len(keep_togethers) == 6


def test_pack_step_cards_preserves_original_step_order():
    from reportlab.lib.styles import getSampleStyleSheet

    steps_meta = [_meta(i + 1, 5, n_rows=1) for i in range(5)]
    story = _pack_step_cards(steps_meta, getSampleStyleSheet(), _GUIDE_CONTENT_WIDTH, _GUIDE_CONTENT_HEIGHT)
    keep_togethers = [f for f in story if isinstance(f, KeepTogether)]
    numbers = []
    for kt in keep_togethers:
        title = kt._content[0].text  # "STEP N OF 5"
        numbers.append(int(title.split()[1]))
    assert numbers == [1, 2, 3, 4, 5]


def test_pack_step_cards_empty_list_returns_empty_story():
    from reportlab.lib.styles import getSampleStyleSheet

    assert _pack_step_cards([], getSampleStyleSheet(), _GUIDE_CONTENT_WIDTH, _GUIDE_CONTENT_HEIGHT) == []


# ==========================================================================
# Real acceptance -- guia real via /api/pack + compute_*_steps reales
# ==========================================================================


def test_real_loading_guide_pdf_adaptive_pagination_end_to_end():
    """Plan real (20 paneles de altura variable -mismo tipo de escenario
    usado para verificar la correccion de camara), pasos reales via
    compute_load_steps, imagenes con el aspect ratio panoramico real -el
    documento debe seguir siendo un PDF valido y ninguna pagina debe
    exceder 4 Steps."""
    from app.api.routes import _get_active_state
    from app.core.sequence import compute_load_steps
    from app.models.schemas import ReportMetadata

    heights = [2400, 2300, 2200, 2000, 1900, 1600, 1200, 1000, 800, 600] * 2
    items = [
        _window(code=f"P{i}", height=h, item_type="panel", stackable=False, group="G1" if i % 2 == 0 else "G2")
        for i, h in enumerate(heights)
    ]
    _pack(items)
    state = _get_active_state()
    steps = compute_load_steps(state.placed, state.container)
    assert len(steps) > 0

    pdf_bytes = build_loading_guide_pdf(state, steps, [_LANDSCAPE_PNG] * len(steps), meta=ReportMetadata())
    assert pdf_bytes[:5] == b"%PDF-"


def test_real_unloading_guide_pdf_adaptive_pagination_end_to_end():
    from app.api.routes import _get_active_state
    from app.core.sequence import compute_unload_steps
    from app.models.schemas import ReportMetadata

    heights = [2400, 2300, 2200, 2000, 1900, 1600, 1200, 1000, 800, 600] * 2
    items = [
        _window(
            code=f"P{i}", height=h, item_type="panel", stackable=False,
            group="G1" if i % 2 == 0 else "G2", delivery_sequence=(i % 3) + 1,
        )
        for i, h in enumerate(heights)
    ]
    _pack(items)
    state = _get_active_state()
    steps = compute_unload_steps(state.placed)
    assert len(steps) > 0

    pdf_bytes = build_unloading_guide_pdf(state, steps, [_LANDSCAPE_PNG] * len(steps), meta=ReportMetadata())
    assert pdf_bytes[:5] == b"%PDF-"


def test_real_unloading_guide_by_group_still_works_with_adaptive_pagination():
    """Regresion explicita: By Group (con nota de prerequisito cruzado
    posible) sigue generando un PDF valido -ver Part 7 del pedido, 'Confirm
    unchanged: By Group PDFs/ZIP'."""
    from app.api.routes import _get_active_state
    from app.core.pdf_export import build_unloading_guide_pdf_for_group
    from app.core.sequence import compute_unload_steps
    from app.models.schemas import ReportMetadata

    items = [
        _window(code="A1", height=2400, item_type="panel", stackable=False, group="Ruta Norte"),
        _window(code="A2", height=600, item_type="panel", stackable=False, group="Ruta Sur"),
    ]
    _pack(items)
    state = _get_active_state()
    steps = compute_unload_steps(state.placed)

    pdf_bytes = build_unloading_guide_pdf_for_group(
        state, steps, [_LANDSCAPE_PNG] * len(steps), ReportMetadata(), None, "Ruta Norte",
    )
    assert pdf_bytes[:5] == b"%PDF-"
