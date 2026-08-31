"""Exportacion a PDF: Container Load Report, Loading Guide, Unloading Guide
(CUBOX V4, prioridades 2-5). Mismo patron que excel_export.py: funciones
puras que arman el documento y devuelven bytes via io.BytesIO(), sin tocar
disco ni el estado global de routes.py.
"""

import base64
import io
from datetime import date
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.models.schemas import (
    ContainerReportRequest,
    PackingResult,
    PlacedPiece,
    ReportMetadata,
    SortReportBy,
)

CONTAINER_REPORT_COLUMNS = ["Code", "Description", "Quantity", "System", "Group", "Width", "Height", "Thickness", "Weight"]

_LOGO_PATH = Path(__file__).resolve().parent.parent / "assets" / "cubox-logo.png"
_LOGO_WIDTH = 140
_LOGO_HEIGHT = 51  # mismo aspecto que el PNG original (1012x371)

_TABLE_STYLE = TableStyle(
    [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2a2a2d")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#999999")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f0f0f0")]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]
)


def _consolidate_placed(placed: list[PlacedPiece]) -> list[list]:
    """Agrupa piezas identicas (mismo code/description/dims/weight/system/
    group) sumando quantity -cada PlacedPiece individual es qty=1, asi que
    varias unidades del mismo producto quedan como filas separadas si no se
    consolidan aca."""
    groups: dict[tuple, int] = {}
    order: list[tuple] = []
    for p in placed:
        key = (p.code, p.description, p.source_width, p.source_height, p.source_thickness, p.weight, p.system, p.group)
        if key not in groups:
            groups[key] = 0
            order.append(key)
        groups[key] += 1

    rows = []
    for key in order:
        code, description, width, height, thickness, weight, system, group = key
        rows.append([code, description, groups[key], system, group, width, height, thickness, weight])
    return rows


def build_container_report_table_rows(state: PackingResult, sort_by: SortReportBy) -> list[list]:
    """Filas de datos (sin header) para la tabla del Container Load Report.
    Separada de build_container_report_pdf para poder testear el contenido
    sin tener que parsear el PDF renderizado."""
    rows = _consolidate_placed(state.placed)
    sort_index = 4 if sort_by == SortReportBy.GROUP else 3  # columnas: Group=4, System=3
    rows.sort(key=lambda r: (r[sort_index] or "", r[0]))
    return rows


def _decode_png(png_base64: str) -> io.BytesIO:
    """reportlab.platypus.Image necesita un objeto file-like real (con
    .read()) para reconocer que le estan pasando datos en memoria en vez de
    una ruta de archivo -pasarle un ImageReader ya envuelto lo confunde
    (intenta hacer os.path.splitext() sobre el objeto y explota)."""
    return io.BytesIO(base64.b64decode(png_base64))


def _header_paragraphs(title: str, container_name: str, meta: ReportMetadata, styles) -> list:
    story: list = []
    if _LOGO_PATH.exists():
        # Path real de archivo (no base64 en memoria): reportlab.Image lo
        # acepta directo, sin el workaround de BytesIO que hace falta para
        # los snapshots que llegan como base64 desde el frontend.
        story.append(Image(str(_LOGO_PATH), width=_LOGO_WIDTH, height=_LOGO_HEIGHT))
        story.append(Spacer(1, 6))
    story.append(Paragraph(title, styles["Title"]))
    if meta.project_name:
        story.append(Paragraph(f"Project: {meta.project_name}", styles["Normal"]))
    if meta.customer:
        story.append(Paragraph(f"Customer: {meta.customer}", styles["Normal"]))
    story.append(Paragraph(f"Date: {date.today().isoformat()}", styles["Normal"]))
    story.append(Paragraph(f"Container Type: {container_name}", styles["Normal"]))
    story.append(Spacer(1, 12))
    return story


def build_container_report_pdf(
    state: PackingResult,
    options: ContainerReportRequest,
) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, title="CUBOX - Container Load Report")
    styles = getSampleStyleSheet()
    story = _header_paragraphs("Container Load Report", state.container.name, options.meta, styles)

    total_volume_m3 = (state.container.length * state.container.width * state.container.height) / 1e9
    # Free Length: best-effort (no existe como concepto en PackingMetrics), el
    # tramo mas largo sin usar desde la puerta (x=0) hasta la pieza mas
    # adelantada -aproximacion, no un valor garantizado exacto.
    free_length_mm = max(0.0, state.container.length - max((p.x + p.dx for p in state.placed), default=0.0))

    summary_rows = [
        ["Internal Length (mm)", round(state.container.length)],
        ["Internal Width (mm)", round(state.container.width)],
        ["Internal Height (mm)", round(state.container.height)],
        ["Max Payload (kg)", state.metrics.max_payload],
        ["Total Loaded Weight (kg)", state.metrics.total_weight],
        ["Weight Utilization %", state.metrics.weight_utilization_pct],
        ["Total Volume (m3)", round(total_volume_m3, 2)],
        ["Volume Utilization %", state.metrics.used_volume_pct],
        ["Floor Utilization %", state.metrics.floor_utilization_pct],
        ["Loaded Pieces", state.metrics.loaded_pieces],
        ["Unloaded Pieces", state.metrics.unloaded_pieces],
        ["Free Length (mm, best-effort)", round(free_length_mm)],
    ]
    summary_table = Table(summary_rows, colWidths=[220, 200])
    summary_table.setStyle(
        TableStyle(
            [
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, colors.HexColor("#f0f0f0")]),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
            ]
        )
    )
    story.append(summary_table)
    story.append(Spacer(1, 12))

    if options.include_overview_image and options.overview_image_png_base64:
        img_reader = _decode_png(options.overview_image_png_base64)
        story.append(Image(img_reader, width=400, height=260))
        story.append(Spacer(1, 12))

    rows = build_container_report_table_rows(state, options.sort_by)
    table_data = [CONTAINER_REPORT_COLUMNS] + rows
    table = Table(table_data, repeatRows=1)
    table.setStyle(_TABLE_STYLE)
    story.append(table)

    doc.build(story)
    return buf.getvalue()


_STEP_TABLE_STYLE = TableStyle(
    [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#ff6b35")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#999999")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f0f0f0")]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]
)

_GRID_CARD_STYLE = TableStyle(
    [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
    ]
)

# Layout adaptable (CUBOX post-V4): por defecto hasta 4 steps/pagina (grid
# 2x2) en vez de 1 step = 1 pagina -mucho menos espacio en blanco. Si algun
# step del lote tiene mas filas de las que entran comodas, ese lote pasa a 2
# steps/pagina; el ultimo step suelto (cantidad impar de steps) se renderiza
# solo, a ancho completo, con una imagen mas grande.
_STEPS_PER_PAGE_DEFAULT = 4
_STEPS_PER_PAGE_SMALL = 2
_ROW_COUNT_THRESHOLD = 6

_GRID_COL_WIDTH = 260
_GRID_IMAGE_WIDTH = 230
_GRID_IMAGE_HEIGHT = 165
_FULL_IMAGE_WIDTH = 480
_FULL_IMAGE_HEIGHT = 330

_GUIDE_MARGIN = 36  # 0.5 in -mas espacio util que el default de reportlab, para que el grid de 2 columnas entre comodo


def build_guide_step_rows(pieces_by_id: dict[str, PlacedPiece], step_ids: list[str]) -> list[list]:
    """Filas [Code, Description, Quantity] para un paso de una guia -piezas
    identicas dentro del mismo paso se consolidan sumando quantity, igual
    criterio que el Container Load Report. Separada de build_*_guide_pdf para
    poder testear el contenido sin parsear el PDF renderizado."""
    groups: dict[tuple, int] = {}
    order: list[tuple] = []
    for pid in step_ids:
        p = pieces_by_id.get(pid)
        if p is None:
            continue
        key = (p.code, p.description)
        if key not in groups:
            groups[key] = 0
            order.append(key)
        groups[key] += 1
    return [[code, description, groups[(code, description)]] for code, description in order]


def _step_card(
    step_number: int,
    step_ids: list[str],
    image_b64: str | None,
    pieces_by_id: dict[str, PlacedPiece],
    styles,
    image_width: int,
    image_height: int,
) -> list:
    """Contenido de un "Step Card": titulo + imagen 3D + tabla Code/
    Description/Quantity. Se usa suelto (step final impar, a ancho completo)
    o como celda de la tabla exterior del grid 2x2."""
    card: list = [Paragraph(f"Step {step_number}", styles["Heading3"])]
    if image_b64:
        card.append(Image(_decode_png(image_b64), width=image_width, height=image_height))
        card.append(Spacer(1, 4))
    rows = build_guide_step_rows(pieces_by_id, step_ids)
    table = Table([["Code", "Description", "Qty"]] + rows, repeatRows=1)
    table.setStyle(_STEP_TABLE_STYLE)
    card.append(table)
    return card


def _batch_steps_for_pages(steps: list[list[str]], pieces_by_id: dict[str, PlacedPiece]) -> list[list[int]]:
    """Agrupa indices de step en "paginas" (listas de indices de `steps`).
    DEFAULT: hasta 4 steps/pagina. Si alguno de esos 4 steps tiene mas filas
    que _ROW_COUNT_THRESHOLD, ese lote pasa a 2 steps/pagina para que la
    tabla no quede ilegible. Un ultimo step suelto (cantidad impar) queda
    solo en su propia pagina."""
    pages: list[list[int]] = []
    i = 0
    n = len(steps)
    while i < n:
        remaining = n - i
        if remaining == 1:
            pages.append([i])
            break
        batch_size = min(_STEPS_PER_PAGE_DEFAULT, remaining)
        candidate = list(range(i, i + batch_size))
        max_rows = max(len(build_guide_step_rows(pieces_by_id, steps[idx])) for idx in candidate)
        if max_rows > _ROW_COUNT_THRESHOLD and batch_size > _STEPS_PER_PAGE_SMALL:
            batch_size = min(_STEPS_PER_PAGE_SMALL, remaining)
            candidate = list(range(i, i + batch_size))
        pages.append(candidate)
        i += batch_size
    return pages


def _build_guide_pdf(
    title: str,
    state: PackingResult,
    steps: list[list[str]],
    step_images_png_base64: list[str],
    meta: ReportMetadata,
) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        title=f"CUBOX - {title}",
        leftMargin=_GUIDE_MARGIN,
        rightMargin=_GUIDE_MARGIN,
        topMargin=_GUIDE_MARGIN,
        bottomMargin=_GUIDE_MARGIN,
    )
    styles = getSampleStyleSheet()
    story = _header_paragraphs(title, state.container.name, meta, styles)

    pieces_by_id = {p.id: p for p in state.placed}
    pages = _batch_steps_for_pages(steps, pieces_by_id)

    def image_for(i: int) -> str | None:
        return step_images_png_base64[i] if i < len(step_images_png_base64) else None

    for page_index, page_step_indices in enumerate(pages):
        if len(page_step_indices) == 1:
            idx = page_step_indices[0]
            story.extend(
                _step_card(idx + 1, steps[idx], image_for(idx), pieces_by_id, styles, _FULL_IMAGE_WIDTH, _FULL_IMAGE_HEIGHT)
            )
        else:
            cards = [
                _step_card(idx + 1, steps[idx], image_for(idx), pieces_by_id, styles, _GRID_IMAGE_WIDTH, _GRID_IMAGE_HEIGHT)
                for idx in page_step_indices
            ]
            rows = []
            for j in range(0, len(cards), 2):
                pair = cards[j : j + 2]
                if len(pair) == 1:
                    pair.append("")
                rows.append(pair)
            grid = Table(rows, colWidths=[_GRID_COL_WIDTH, _GRID_COL_WIDTH])
            grid.setStyle(_GRID_CARD_STYLE)
            story.append(grid)

        if page_index != len(pages) - 1:
            story.append(PageBreak())

    doc.build(story)
    return buf.getvalue()


def build_loading_guide_pdf(
    state: PackingResult,
    steps: list[list[str]],
    step_images_png_base64: list[str],
    meta: ReportMetadata,
) -> bytes:
    return _build_guide_pdf("Loading Guide", state, steps, step_images_png_base64, meta)


def build_unloading_guide_pdf(
    state: PackingResult,
    steps: list[list[str]],
    step_images_png_base64: list[str],
    meta: ReportMetadata,
) -> bytes:
    return _build_guide_pdf("Unloading Guide", state, steps, step_images_png_base64, meta)
