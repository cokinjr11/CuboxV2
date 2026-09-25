"""Exportacion a PDF: Container Load Report, Loading Guide, Unloading Guide
(CUBOX V4, prioridades 2-5; Operational Guide Print Redesign v2). Mismo
patron que excel_export.py: funciones puras que arman el documento y
devuelven bytes via io.BytesIO(), sin tocar disco ni el estado global de
routes.py.

Redesign v2 (Part A): cada guia arranca con una portada operacional real
(Page 1 sin ningun Step -Step 1 empieza en Page 2), con Plan Information/
Operational Metrics/Groups/Plan Validation/Color Legend. Cada Step Card
ahora tiene "STEP X OF N" + accion explicita, imagen grande, y una fila de 2
columnas (Step Content / Step Notes) en vez de solo una tabla chica centrada.
Al final de cada guia se agrega un Load Composition Summary (Group -> System)
y un Final Load Summary -nunca reemplazan al Packing List de Excel, son
puramente informativos.

Part B (Configurable Export Validation Override): `validation` (un
ReportValidationResponse ya calculado por
core/final_validation.py:build_plan_validation_result, NUNCA recalculado
aca) se acepta opcionalmente en cada builder. Si `validation.status ==
NOT_READY` (solo posible si el caller en routes.py permitio el export pese a
errores bloqueantes -ver _resolve_export_validation), el documento se marca
de forma explicita: portada con caja de WARNING + desglose de errores
bloqueantes, y un footer recurrente en cada pagina de Step. Nunca se
recalculan reglas de validacion aca -solo se lee `validation.status`/
`error_issues`/`warning_issues`, ya agregados por Fase 6C."""

import base64
import io
from datetime import date
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.core.sequence import annotate_unload_steps_with_delivery
from app.models.schemas import (
    ContainerReportRequest,
    ItemType,
    OperationalWarning,
    OperationalWarningType,
    OptimizationMode,
    PackingResult,
    PlacedPiece,
    PlanValidationStatus,
    ReportMetadata,
    ReportValidationResponse,
    SortReportBy,
    UnloadStepDeliveryInfo,
    ValidationCategory,
    ValidationIssue,
)

CONTAINER_REPORT_COLUMNS = [
    "Code", "Description", "Quantity", "System", "Group", "Width", "Height", "Thickness", "Weight", "Boxes Inside",
]

_LOGO_PATH = Path(__file__).resolve().parent.parent / "assets" / "cubox-logo.png"
_LOGO_WIDTH = 140
_LOGO_HEIGHT = 51  # mismo aspecto que el PNG original (1012x371)

# Redesign v2: azul-grisaceo canonico para "pieza ya manejada/contexto" -el
# mismo valor se usa aca (leyenda de portada) y en
# frontend/PieceMesh.tsx:GRAY_PAST_COLOR, para que el PDF describa
# EXACTAMENTE el color que el operador va a ver en la imagen 3D capturada,
# nunca una aproximacion.
BLUE_GRAY_HEX = "#6c7f8f"
_CURRENT_STEP_HEX = "#ff6b35"

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

_INFO_TABLE_STYLE = TableStyle(
    [
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, colors.HexColor("#f0f0f0")]),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
    ]
)


def _is_palletized_plan(placed: list[PlacedPiece]) -> bool:
    """Boxes Inside (Fase 6.2) solo tiene sentido para Palletized Load -ver
    seccion 3 del pedido de Fase 6.1 ("adapt to the active Load Type", "do
    not globally... if shared by other Load Types"). Un plan es siempre
    homogeneo en item_type (un solo perfil de import por plan), asi que
    alcanza con mirar la primera pieza."""
    return bool(placed) and placed[0].item_type == ItemType.PALLET


def _has_tilt_capable_pieces(placed: list[PlacedPiece]) -> bool:
    """Fase 5C, seccion 37 del pedido: no agregar la columna Tilt si ningun
    item del plan la soporta. Se agrega SIEMPRE como columna final (nunca
    se toca CONTAINER_REPORT_COLUMNS/build_guide_step_rows, cuyo shape ya
    esta testeado) -ver _tilt_label_by_code/_tilt_label_by_group mas abajo."""
    return any(p.allow_tilt for p in placed)


def _format_signed_tilt(tilt_angle: float) -> str:
    """Fase 5C-FINAL, seccion 36 del pedido: el signo NUNCA se descarta -
    +12°/-12° son direcciones de inclinacion distintas y operacionalmente
    relevantes. "" para 0 (sin inclinar)."""
    return f"{tilt_angle:+g}°" if tilt_angle else ""


def _tilt_label_by_code(placed: list[PlacedPiece]) -> dict[str, str]:
    """Angulo de Tilt a mostrar por code en el Container Load Report.
    _consolidate_placed (abajo) no incluye tilt_angle en su clave de
    agrupamiento a proposito -no se toca ese contrato ya testeado-; si
    varias unidades del mismo code terminan con angulos distintos (packer
    corner-heuristic, sin garantia de uniformidad), se muestra el de la
    PRIMERA unidad encontrada -simplificacion aceptable para un reporte
    consolidado por code, no por pieza individual."""
    labels: dict[str, str] = {}
    for p in placed:
        if p.code not in labels:
            labels[p.code] = _format_signed_tilt(p.tilt_angle)
    return labels


def _tilt_label_by_group(pieces_by_id: dict[str, PlacedPiece], step_ids: list[str]) -> dict[tuple, str]:
    """Equivalente a _tilt_label_by_code, pero para un step de la Loading/
    Unloading Guide (build_guide_step_rows agrupa por code+description
    dentro del step, no por code solo -mismo criterio aca)."""
    labels: dict[tuple, str] = {}
    for pid in step_ids:
        p = pieces_by_id.get(pid)
        if p is None:
            continue
        key = (p.code, p.description)
        if key not in labels:
            labels[key] = _format_signed_tilt(p.tilt_angle)
    return labels


def _consolidate_placed(placed: list[PlacedPiece]) -> list[list]:
    """Agrupa piezas identicas (mismo code/description/dims/weight/system/
    group/boxes_inside) sumando quantity -cada PlacedPiece individual es
    qty=1, asi que varias unidades del mismo producto quedan como filas
    separadas si no se consolidan aca. boxes_inside entra a la clave porque
    es informacion propia del producto (no deberia variar entre unidades del
    mismo code, pero si lo hiciera no querriamos mezclarlas silenciosamente)."""
    groups: dict[tuple, int] = {}
    order: list[tuple] = []
    for p in placed:
        key = (
            p.code, p.description, p.source_width, p.source_height, p.source_thickness, p.weight, p.system, p.group,
            p.boxes_inside,
        )
        if key not in groups:
            groups[key] = 0
            order.append(key)
        groups[key] += 1

    rows = []
    for key in order:
        code, description, width, height, thickness, weight, system, group, boxes_inside = key
        rows.append([code, description, groups[key], system, group, width, height, thickness, weight,
                     boxes_inside if boxes_inside is not None else ""])
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


def _fit_image_dims(png_base64: str, max_width: float, max_height: float) -> tuple[float, float]:
    """PDF image proportion fix: el PNG capturado por el navegador (Scene3D)
    puede tener CUALQUIER proporcion -depende del tamano/layout real de la
    ventana del usuario al momento de capturar, nunca es un 460x200 o
    540x230 fijo. `Image(width=X, height=Y)` de reportlab ESTIRA la imagen a
    esa caja exacta sin respetar su proporcion nativa si no coinciden -eso
    es lo que causaba el aplastamiento vertical reportado (ej. un canvas
    capturado en proporcion ~0.78 (mas alto que ancho) forzado dentro de una
    caja 540x230 de proporcion ~2.35 se ve completamente distorsionado).
    Esta funcion calcula el mayor tamano que entra en (max_width, max_height)
    -como "object-fit: contain"- preservando la proporcion nativa exacta del
    PNG, usando reportlab.lib.utils.ImageReader (ya una dependencia, sin
    libreria nueva) solo para leer el tamano nativo, nunca para dibujar."""
    from reportlab.lib.utils import ImageReader

    reader = ImageReader(_decode_png(png_base64))
    src_w, src_h = reader.getSize()
    if not src_w or not src_h:
        return max_width, max_height
    scale = min(max_width / src_w, max_height / src_h)
    return src_w * scale, src_h * scale


def _header_paragraphs(title: str, container_name: str, meta: ReportMetadata, styles) -> list:
    """Header simple (logo + titulo + Project/Customer/Date/Container) -
    sigue siendo lo unico que usa el Container Load Report (que no tiene
    portada dedicada, Redesign v2 Part A solo pide portada para Loading/
    Unloading Guide). Las guias usan `_cover_title`/`_operational_cover_page`
    en su lugar."""
    story: list = []
    if _LOGO_PATH.exists():
        story.append(Image(str(_LOGO_PATH), width=_LOGO_WIDTH, height=_LOGO_HEIGHT))
        story.append(Spacer(1, 6))
    story.append(Paragraph(title, styles["Title"]))
    if meta.project_name:
        story.append(Paragraph(f"Project: {meta.project_name}", styles["Normal"]))
    if meta.customer:
        story.append(Paragraph(f"Customer: {meta.customer}", styles["Normal"]))
    story.append(Paragraph(f"Date: {date.today().isoformat()}", styles["Normal"]))
    story.append(Paragraph(f"Load Space: {container_name}", styles["Normal"]))
    story.append(Spacer(1, 12))
    return story


# ==========================================================================
# Part B -Configurable Export Validation Override: helpers compartidos por
# los 5 tipos de export (Container/Loading/Unloading/Unloading-by-Group/
# Excel). `validation` es SIEMPRE el mismo ReportValidationResponse que ya
# calculo core/final_validation.py:build_plan_validation_result -nunca se
# reinterpreta ni se recalcula una regla aca, solo se lee su status/
# error_issues/warning_issues ya agregados.
# ==========================================================================

_PLAN_STATUS_LABELS: dict[PlanValidationStatus, str] = {
    PlanValidationStatus.READY: "READY",
    PlanValidationStatus.READY_WITH_WARNINGS: "READY WITH WARNINGS",
    PlanValidationStatus.NOT_READY: "✕ NOT READY",
}
_PLAN_STATUS_COLORS: dict[PlanValidationStatus, colors.Color] = {
    PlanValidationStatus.READY: colors.HexColor("#2e7d32"),
    PlanValidationStatus.READY_WITH_WARNINGS: colors.HexColor("#b8860b"),
    PlanValidationStatus.NOT_READY: colors.HexColor("#c0392b"),
}

# Fase 6C -mismas categorias/etiquetas/orden que CATEGORY_LABELS/
# CATEGORY_ORDER en frontend/src/components/PlanValidationPanel.tsx (copia
# deliberada, no una taxonomia nueva -seccion 34 del pedido: "do not dump
# hundreds of messages", pero tampoco inventar una segunda forma de nombrar
# las mismas categorias que el Workspace ya usa).
_VALIDATION_CATEGORY_LABELS: dict[ValidationCategory, str] = {
    ValidationCategory.DATA_INTEGRITY: "Data Integrity",
    ValidationCategory.LOAD_SPACE: "Load Space",
    ValidationCategory.COLLISION_CLEARANCE: "Collision & Clearance",
    ValidationCategory.SUPPORT_STABILITY: "Support / Stability",
    ValidationCategory.ORIENTATION: "Orientation",
    ValidationCategory.STACK_WEIGHT: "Stack Weight",
    ValidationCategory.PAYLOAD: "Payload",
    ValidationCategory.ROAD_WEIGHT: "Road Weight",
    ValidationCategory.OPERATIONAL_SEQUENCE: "Operational Sequence",
    ValidationCategory.DELIVERY_SEQUENCE: "Delivery Sequence",
    ValidationCategory.UNLOADED_ITEMS: "Unloaded Items",
}
_VALIDATION_CATEGORY_ORDER: list[ValidationCategory] = [
    ValidationCategory.LOAD_SPACE,
    ValidationCategory.COLLISION_CLEARANCE,
    ValidationCategory.SUPPORT_STABILITY,
    ValidationCategory.ORIENTATION,
    ValidationCategory.STACK_WEIGHT,
    ValidationCategory.PAYLOAD,
    ValidationCategory.ROAD_WEIGHT,
    ValidationCategory.OPERATIONAL_SEQUENCE,
    ValidationCategory.DELIVERY_SEQUENCE,
    ValidationCategory.UNLOADED_ITEMS,
    ValidationCategory.DATA_INTEGRITY,
]


def _summarize_issues_by_category(issues: list[ValidationIssue]) -> list[tuple[str, int]]:
    """Cuenta issues (error_issues o warning_issues, YA clasificados por
    Fase 6C) por categoria -nunca parsea `message`, solo agrupa por
    `.category` (mismo criterio que _summarize_warnings_by_category para
    OperationalWarningType, mas abajo)."""
    counts: dict[ValidationCategory, int] = {}
    for issue in issues:
        counts[issue.category] = counts.get(issue.category, 0) + 1
    return [(_VALIDATION_CATEGORY_LABELS[c], counts[c]) for c in _VALIDATION_CATEGORY_ORDER if c in counts]


def _plan_validation_block(validation: ReportValidationResponse, styles) -> list:
    """Seccion "PLAN VALIDATION" de portada (seccion 6 del pedido): status
    autoritativo de Fase 6C tal cual -esta funcion NUNCA decide READY/
    NOT_READY, solo lo muestra. READY_WITH_WARNINGS -> desglose de
    categorias de warning_issues (conteos, nunca el texto de cada mensaje).
    NOT_READY (solo llega aca si el export fue permitido con override, ver
    _resolve_export_validation en routes.py) -> aviso corto; el desglose de
    ERRORES bloqueantes vive en _export_override_box, seccion separada."""
    story: list = [Paragraph("PLAN VALIDATION", styles["Heading4"])]
    status_style = ParagraphStyle(
        f"Status-{validation.status.value}", fontName="Helvetica-Bold", fontSize=12,
        textColor=_PLAN_STATUS_COLORS[validation.status],
    )
    story.append(Paragraph(_PLAN_STATUS_LABELS[validation.status], status_style))
    if validation.status == PlanValidationStatus.READY_WITH_WARNINGS:
        for label, count in _summarize_issues_by_category(validation.warning_issues):
            story.append(Paragraph(f"{count} {label}{'s' if count != 1 else ''}", styles["Normal"]))
    elif validation.status == PlanValidationStatus.NOT_READY:
        story.append(
            Paragraph(
                "EXPORTED WITH VALIDATION ERRORS",
                ParagraphStyle("OverrideNotice", fontName="Helvetica-Bold", fontSize=10, textColor=_PLAN_STATUS_COLORS[PlanValidationStatus.NOT_READY]),
            )
        )
    story.append(Spacer(1, 8))
    return story


_WARNING_BOX_STYLE = TableStyle(
    [
        ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#c0392b")),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fdecea")),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]
)


def _export_override_box(validation: ReportValidationResponse, styles) -> list:
    """Seccion 33/34 del pedido: caja de WARNING visualmente clara -NUNCA
    implica aprobacion operacional- + desglose de errores bloqueantes por
    categoria (conteos, nunca los cientos de mensajes individuales -esos
    siguen disponibles en el Workspace/Plan Validation panel). Solo se
    llama cuando validation.status == NOT_READY (es decir, el caller en
    routes.py ya confirmo que el export fue permitido pese a errores)."""
    inner = [
        Paragraph(
            "⚠ WARNING",
            ParagraphStyle("WarnTitle", fontName="Helvetica-Bold", fontSize=10, textColor=colors.HexColor("#c0392b")),
        ),
        Paragraph(
            "This load plan contains unresolved blocking validation errors. Export was manually allowed through "
            "the Cubox export settings.",
            styles["Normal"],
        ),
        Spacer(1, 4),
        Paragraph("BLOCKING VALIDATION ERRORS", ParagraphStyle("BlockingTitle", fontName="Helvetica-Bold", fontSize=9)),
    ]
    for label, count in _summarize_issues_by_category(validation.error_issues):
        inner.append(Paragraph(f"{count} {label}{'s' if count != 1 else ''}", styles["Normal"]))
    box = Table([[inner]], colWidths=[460])
    box.setStyle(_WARNING_BOX_STYLE)
    return [Spacer(1, 6), box, Spacer(1, 10)]


def _make_override_footer(validation: "ReportValidationResponse | None"):
    """Seccion 35 del pedido: indicador recurrente y discreto (footer de
    texto, NUNCA una marca de agua sobre la imagen 3D) en cada pagina -None
    si el documento no fue exportado bajo override (regresion, seccion 44:
    un reporte READY/READY_WITH_WARNINGS normal nunca debe mostrar esto)."""
    if validation is None or validation.status != PlanValidationStatus.NOT_READY:
        return None

    def _footer(canvas, _doc) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica-Bold", 7.5)
        canvas.setFillColor(colors.HexColor("#b34700"))
        canvas.drawCentredString(letter[0] / 2, 16, "EXPORTED WITH VALIDATION ERRORS — PLAN NOT READY")
        canvas.restoreState()

    return _footer


def _blank_canvas(_canvas, _doc) -> None:
    return None


def build_container_report_pdf(
    state: PackingResult,
    options: ContainerReportRequest,
    plan_name: str | None = None,
    load_type_label: str | None = None,
    validation: "ReportValidationResponse | None" = None,
) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, title="CUBOX - Container Load Report")
    styles = getSampleStyleSheet()
    story = _header_paragraphs("Container Load Report", state.container.name, options.meta, styles)

    if validation is not None and validation.status == PlanValidationStatus.NOT_READY:
        # Seccion 33: el Container Load Report no tiene portada dedicada
        # (Part A solo la exige para Loading/Unloading Guide), asi que la
        # caja de override va justo despues del header, antes de cualquier
        # otro contenido -nunca escondida mas abajo en el documento.
        story.extend(_export_override_box(validation, styles))

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
        [
            "Operational Sequence",
            "Valid" if not state.operational_warnings else f"{len(state.operational_warnings)} Warning(s)",
        ],
    ]
    summary_table = Table(summary_rows, colWidths=[220, 200])
    summary_table.setStyle(_INFO_TABLE_STYLE)
    story.append(summary_table)
    story.append(Spacer(1, 12))

    if state.operational_warnings:
        # Fase 6A, seccion 35: nunca exportar una secuencia "perfecta" en
        # silencio si hay conflictos operacionales detectados -no rediseña la
        # apariencia del reporte, solo agrega el listado de mensajes ya
        # generados por compute_operational_warnings.
        story.append(Paragraph("Operational Sequence Warnings", styles["Heading3"]))
        for w in state.operational_warnings:
            story.append(Paragraph(f"- {w.message}", styles["Normal"]))
        story.append(Spacer(1, 12))

    if options.include_overview_image and options.overview_image_png_base64:
        img_reader = _decode_png(options.overview_image_png_base64)
        story.append(Image(img_reader, width=400, height=260))
        story.append(Spacer(1, 12))

    rows = build_container_report_table_rows(state, options.sort_by)
    is_pallet_plan = _is_palletized_plan(state.placed)
    columns = CONTAINER_REPORT_COLUMNS if is_pallet_plan else CONTAINER_REPORT_COLUMNS[:-1]
    if not is_pallet_plan:
        rows = [row[:-1] for row in rows]
    if _has_tilt_capable_pieces(state.placed):
        tilt_labels = _tilt_label_by_code(state.placed)
        columns = columns + ["Tilt"]
        rows = [row + [tilt_labels.get(row[0], "")] for row in rows]
    table_data = [columns] + rows
    table = Table(table_data, repeatRows=1)
    table.setStyle(_TABLE_STYLE)
    story.append(table)

    footer = _make_override_footer(validation)
    doc.build(story, onFirstPage=footer or _blank_canvas, onLaterPages=footer or _blank_canvas)
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

# Adaptive Operational Guide Pagination: reemplaza el "2/pagina fijo"
# anterior (_STEPS_PER_PAGE/_batch_steps_for_pages, eliminados). Un Step
# simple (pocas filas, sin warnings) desperdicia la mayor parte de la
# imagen grande de un Step complejo -esta correccion elige, POR STEP, un
# tamano de presentacion (compact/standard/large -ver
# _classify_step_size/_STEP_SIZE_IMAGE_DIMS) y empaqueta Step Cards ya
# armadas y MEDIDAS (alto real via Flowable.wrap(), nunca una estimacion)
# en el presupuesto vertical real de cada pagina -ver _pack_step_cards. La
# tabla de Step Content y las notas NUNCA se truncan ni cambian de tamano
# de fuente -solo la imagen y el respiro del header se ajustan.
_MAX_STEPS_PER_PAGE = 4
_STEP_CARD_GAP = 6  # mismo valor que el Spacer(1, 6) de siempre entre Steps

_GUIDE_MARGIN = 16  # 0.22 in (antes 24/0.33in)
_GUIDE_CONTENT_WIDTH = letter[0] - 2 * _GUIDE_MARGIN
_GUIDE_CONTENT_HEIGHT = letter[1] - 2 * _GUIDE_MARGIN

# "large" (el tamano de siempre, 560x215) queda SIN CAMBIOS -es el mismo
# valor ya verificado con la correccion de camara/encuadre de la tarea
# anterior, nunca tocado aca (el pedido de esta tarea es solo paginacion).
# compact/standard escalan la ALTURA (ver seccion "Compact layout" del
# pedido) manteniendo el mismo aspect ratio panoramico (560/215, igual al
# GUIDE_CAPTURE_ASPECT que usa el frontend para capturar) -reducir el ancho
# en una proporcion distinta dejaria un box mas angosto que la imagen
# nativa y _fit_image_dims (aspect-preserving) volveria a dejar margenes
# vacios, el mismo bug que la correccion de camara ya resolvio.
_STEP_IMAGE_WIDTH = 560
_STEP_IMAGE_HEIGHT = 215
_GUIDE_IMAGE_ASPECT = _STEP_IMAGE_WIDTH / _STEP_IMAGE_HEIGHT

_STEP_SIZE_IMAGE_HEIGHTS = {"compact": 120, "standard": 180, "large": _STEP_IMAGE_HEIGHT}
_STEP_SIZE_IMAGE_DIMS = {
    size: (round(height * _GUIDE_IMAGE_ASPECT), height) for size, height in _STEP_SIZE_IMAGE_HEIGHTS.items()
}

_STEP_CONTENT_WIDTH = 340
_STEP_NOTES_WIDTH = 220

_STEP_TITLE_STYLE = ParagraphStyle("StepTitle", fontName="Helvetica-Bold", fontSize=12, leading=13, spaceBefore=0, spaceAfter=0)
_STEP_ACTION_STYLE = ParagraphStyle(
    "StepAction", fontName="Helvetica-Bold", fontSize=9, leading=10.5, textColor=colors.HexColor(_CURRENT_STEP_HEX),
    spaceBefore=0, spaceAfter=0,
)
_NOTES_STYLE = ParagraphStyle("StepNotes", fontName="Helvetica", fontSize=8, leading=9.5, spaceBefore=0, spaceAfter=1)
_NOTES_BOLD_STYLE = ParagraphStyle("StepNotesBold", fontName="Helvetica-Bold", fontSize=8, leading=9.5, spaceBefore=0, spaceAfter=0)
_NOTES_HEADING_STYLE = ParagraphStyle("StepNotesHeading", fontName="Helvetica-Bold", fontSize=9, leading=10.5, spaceBefore=0, spaceAfter=2)


def build_guide_step_rows(pieces_by_id: dict[str, PlacedPiece], step_ids: list[str]) -> list[list]:
    """Filas [Code, Description, Quantity, Boxes Inside] para un paso de una
    guia -piezas identicas dentro del mismo paso se consolidan sumando
    quantity, igual criterio que el Container Load Report. Boxes Inside es
    el valor POR UNIDAD (no se multiplica por Quantity) -puramente
    informativo para trazabilidad/logistica al momento del despacho, ver
    LoadItem.boxes_inside en schemas.py. Separada de build_*_guide_pdf para
    poder testear el contenido sin parsear el PDF renderizado."""
    groups: dict[tuple, int] = {}
    order: list[tuple] = []
    for pid in step_ids:
        p = pieces_by_id.get(pid)
        if p is None:
            continue
        key = (p.code, p.description, p.boxes_inside)
        if key not in groups:
            groups[key] = 0
            order.append(key)
        groups[key] += 1
    return [
        [code, description, groups[(code, description, boxes_inside)], boxes_inside if boxes_inside is not None else ""]
        for code, description, boxes_inside in order
    ]


def _classify_step_size(n_content_rows: int, n_conflict_messages: int, has_prerequisite_note: bool) -> str:
    """Regla de clasificacion de complejidad de un Step -datos ESTRUCTURADOS
    (cuenta de filas/warnings reales), nunca longitud de texto:

    - "large": mas de 5 filas de Step Content, o 3+ operational warnings, o
      una nota de prerequisito cruzado de Group presente (una nota de
      prerequisito -ver _cross_group_prerequisite_note- es siempre una
      oracion operacional completa con uno o mas codigos/Groups, contenido
      "sustancial" por definicion, sea cual sea su longitud exacta).
    - "standard": 4-5 filas, o 1-2 operational warnings.
    - "compact": cualquier otro caso (1-3 filas, sin warnings, sin nota de
      prerequisito) -el Step simple tipico.

    Nunca decide el CONTENIDO de un Step (que filas/warnings mostrar, eso
    sigue siendo build_guide_step_rows/_step_notes_flowables sin cambios)
    -solo elige que tan grande se imprime la imagen y cuanto respiro tiene
    el header (ver _STEP_SIZE_IMAGE_DIMS/_step_card), para que mas Steps
    simples entren por pagina sin tocar tabla ni notas."""
    if n_content_rows > 5 or n_conflict_messages >= 3 or has_prerequisite_note:
        return "large"
    if n_content_rows >= 4 or n_conflict_messages >= 1:
        return "standard"
    return "compact"


def _delivery_step_label(info: "UnloadStepDeliveryInfo") -> str | None:
    """Fase 6B, seccion 13: "Delivery Sequence: X" (o "Delivery Sequences:
    a, b" si el paso mezcla mas de un valor). None si el paso no tiene
    Delivery Sequence definida en absoluto (seccion 34: nunca se inventa un
    numero)."""
    if not info.delivery_sequences:
        return None
    if info.is_mixed:
        return f"Delivery Sequences: {', '.join(str(v) for v in info.delivery_sequences)}"
    return f"Delivery Sequence: {info.delivery_sequences[0]}"


# Fase 6B.3, seccion 4/6 del pedido: mismas etiquetas/orden que
# WARNING_CATEGORY_LABELS/WARNING_CATEGORY_ORDER en
# frontend/src/components/SequencePanel.tsx (copia deliberada, no una nueva
# taxonomia) -orden FIJO por severidad, no por conteo, para que "Sequence
# cycle" (imposibilidad fisica) nunca quede escondido detras de categorias
# mas numerosas pero menos graves.
_WARNING_CATEGORY_LABELS: dict[OperationalWarningType, str] = {
    OperationalWarningType.SEQUENCE_CYCLE: "Sequence cycle",
    OperationalWarningType.DELIVERY_SEQUENCE_CONFLICT: "Delivery blocking conflict",
    OperationalWarningType.STACKING_SEQUENCE_CONFLICT: "Stacking conflict",
    OperationalWarningType.OPENING_CONFIGURATION_LIMITATION: "Opening configuration limitation",
    OperationalWarningType.OPERATIONAL_LOADABILITY_WARNING: "Loadability warning",
}
_WARNING_CATEGORY_ORDER: list[OperationalWarningType] = [
    OperationalWarningType.SEQUENCE_CYCLE,
    OperationalWarningType.DELIVERY_SEQUENCE_CONFLICT,
    OperationalWarningType.STACKING_SEQUENCE_CONFLICT,
    OperationalWarningType.OPENING_CONFIGURATION_LIMITATION,
    OperationalWarningType.OPERATIONAL_LOADABILITY_WARNING,
]

_OPTIMIZATION_MODE_LABELS: dict[OptimizationMode, str] = {
    OptimizationMode.BEST_SPACE: "Best Space Utilization",
    OptimizationMode.KEEP_GROUPS: "Keep Groups Together",
    OptimizationMode.KEEP_SYSTEMS: "Keep Systems Together",
    OptimizationMode.PRIORITIZE_DELIVERY: "Prioritize Delivery Sequence",
}


def _summarize_warnings_by_category(warnings: list[OperationalWarning]) -> list[tuple[str, int]]:
    """Puerto directo de summarizeByCategory() en SequencePanel.tsx -mismo
    conteo, mismas etiquetas, mismo orden. No detecta ni modifica ningun
    warning, solo los agrupa para mostrar un total legible."""
    counts: dict[OperationalWarningType, int] = {}
    for w in warnings:
        counts[w.type] = counts.get(w.type, 0) + 1
    return [(_WARNING_CATEGORY_LABELS[t], counts[t]) for t in _WARNING_CATEGORY_ORDER if t in counts]


def _operational_warnings_summary(
    warnings: list[OperationalWarning],
    optimization_mode: OptimizationMode | None,
    styles,
) -> list:
    """Fase 6B.3, seccion 6 del pedido: seccion UNICA y compacta al inicio
    del Unloading Guide PDF -total + desglose por categoria (nunca los 381
    mensajes individuales, eso sigue existiendo solo internamente en
    PackingResult.operational_warnings). Mismo mensaje contextual que ya
    esta aprobado en el Workspace (SequencePanel.tsx) segun el modo de
    optimizacion, para no inventar una redaccion nueva."""
    story: list = [Paragraph("Operational Warnings", styles["Heading2"])]
    if not warnings:
        story.append(Paragraph("Operational Sequence: Valid (no warnings).", styles["Normal"]))
        story.append(Spacer(1, 12))
        return story

    story.append(Paragraph(f"Total: {len(warnings)}", styles["Normal"]))
    for label, count in _summarize_warnings_by_category(warnings):
        story.append(Paragraph(f"{count} {label}{'s' if count != 1 else ''}", styles["Normal"]))

    has_delivery_conflicts = any(w.type == OperationalWarningType.DELIVERY_SEQUENCE_CONFLICT for w in warnings)
    if optimization_mode == OptimizationMode.PRIORITIZE_DELIVERY:
        story.append(Spacer(1, 4))
        story.append(Paragraph("Cubox prioritized delivery order, but some conflicts could not be avoided.", styles["Italic"]))
    elif has_delivery_conflicts and optimization_mode is not None:
        story.append(Spacer(1, 4))
        story.append(Paragraph(f"Optimization Mode: {_OPTIMIZATION_MODE_LABELS[optimization_mode]}", styles["Normal"]))
        story.append(
            Paragraph(
                "Delivery order was analyzed but was not used as the packing objective. "
                'Use "Prioritize Delivery Sequence" if unloading order is important.',
                styles["Italic"],
            )
        )
    story.append(Spacer(1, 12))
    return story


def _compact_conflict_summary(delivery_info: "UnloadStepDeliveryInfo", styles) -> list:
    """Fase 6B.3, seccion 4/5/8 del pedido: 0 conflictos -> nada (ver
    _step_notes_flowables, no llama a esto). 1-2 -> el llamador imprime las
    lineas completas tal cual (seccion 5, sin pasar por aca). 3+ -> UNA sola
    linea compacta por categoria en vez de docenas de lineas repetidas
    -seccion 22 del pedido de Redesign v2 (antes seccion 8): "no dejar que el
    texto de warnings sea mas grande que la instruccion del paso en si". La
    lista completa NUNCA se pierde -sigue intacta en
    UnloadStepDeliveryInfo.conflict_messages/GET /report/steps para quien la
    necesite (ej. el panel interactivo del Workspace, sin cambios)."""
    n = len(delivery_info.conflict_messages)
    counts: dict[OperationalWarningType, int] = {}
    for t in delivery_info.conflict_types:
        counts[t] = counts.get(t, 0) + 1
    parts = [
        f"{counts[t]} {_WARNING_CATEGORY_LABELS[t]}{'s' if counts[t] != 1 else ''}"
        for t in _WARNING_CATEGORY_ORDER
        if t in counts
    ]
    breakdown = ", ".join(parts)
    return [
        Paragraph(
            f"⚠ {n} conflicts ({breakdown}) — see Operational Warnings summary at the start of this guide.",
            styles["Italic"],
        )
    ]


def _step_content_col_widths(n_cols: int) -> list[float] | None:
    """Anchos fijos para la tabla Step Content (Redesign v2, seccion 19)
    -ahora vive en una columna angosta (_STEP_CONTENT_WIDTH) en vez de casi
    toda la pagina, asi que el auto-sizing de reportlab (colWidths=None) ya
    no es seguro -podria desbordar la celda contenedora."""
    widths = {
        3: [65, 205, 35],  # Code, Description, Qty
        4: [55, 165, 30, 55],  # + Boxes Inside o + Tilt
        5: [50, 140, 28, 48, 40],  # + Boxes Inside + Tilt
    }
    return widths.get(n_cols)


def _step_content_table(pieces_by_id: dict[str, PlacedPiece], step_ids: list[str], is_pallet_plan: bool, has_tilt: bool) -> Table:
    """Tabla Code/Description/Qty(/Boxes Inside)(/Tilt) de un Step -extraida
    de _step_card para poder ubicarla dentro de la columna izquierda de la
    fila Step Content/Step Notes (seccion 18-19 del pedido)."""
    rows = build_guide_step_rows(pieces_by_id, step_ids)
    header = ["Code", "Description", "Qty", "Boxes Inside"]
    if not is_pallet_plan:
        header = header[:-1]
        rows = [row[:-1] for row in rows]
    if has_tilt:
        tilt_labels = _tilt_label_by_group(pieces_by_id, step_ids)
        header = header + ["Tilt"]
        rows = [row + [tilt_labels.get((row[0], row[1]), "")] for row in rows]
    table = Table([header] + rows, repeatRows=1, colWidths=_step_content_col_widths(len(header)))
    table.setStyle(_STEP_TABLE_STYLE)
    return table


def _step_notes_flowables(
    direction: str,
    delivery_info: "UnloadStepDeliveryInfo | None",
    extra_notes: list[str] | None,
    styles,
) -> list:
    """Columna derecha de la fila Step Content/Step Notes (seccion 18/20/21
    del pedido). LOADING: no hay Delivery Sequence (concepto de descarga,
    Fase 6A) ni warnings por-step calculados hoy -se muestra "Loading Side:
    Rear" (comportamiento REAR-only del producto, dato real y constante, no
    inventado) y "Warnings: None" (honesto: no hay deteccion de warnings
    por-step de carga en el motor actual). UNLOADING: Delivery Sequence del
    paso + warnings YA calculados por Fase 6A (nunca una deteccion nueva),
    mas la nota de prerequisito cruzado de Group si aplica (Group Guide).
    Seccion 21: 0 warnings -> "No operational warnings" explicito, nunca un
    panel vacio sin explicar."""
    flows: list = [Paragraph("STEP NOTES", _NOTES_HEADING_STYLE)]
    if direction == "load":
        flows.append(Paragraph("Loading Side:", _NOTES_BOLD_STYLE))
        flows.append(Paragraph("Rear", _NOTES_STYLE))
        flows.append(Spacer(1, 3))
        flows.append(Paragraph("Warnings: None", _NOTES_STYLE))
        return flows

    label = _delivery_step_label(delivery_info) if delivery_info is not None else None
    if label:
        key, _, value = label.partition(":")
        flows.append(Paragraph(f"{key}:", _NOTES_BOLD_STYLE))
        flows.append(Paragraph(value.strip(), _NOTES_STYLE))
    else:
        flows.append(Paragraph("Delivery Sequence:", _NOTES_BOLD_STYLE))
        flows.append(Paragraph("Not specified", _NOTES_STYLE))
    flows.append(Spacer(1, 3))

    conflict_messages = delivery_info.conflict_messages if delivery_info is not None else []
    if not conflict_messages and not extra_notes:
        flows.append(Paragraph("✓ No operational warnings", _NOTES_STYLE))
        return flows

    flows.append(Paragraph("Warnings:", _NOTES_BOLD_STYLE))
    if conflict_messages:
        if len(conflict_messages) <= 2:
            for message in conflict_messages:
                flows.append(Paragraph(f"⚠ {message}", _NOTES_STYLE))
        else:
            flows.extend(_compact_conflict_summary(delivery_info, styles))
    for note in extra_notes or []:
        flows.append(Paragraph(f"⚠ {note}", _NOTES_STYLE))
    return flows


def _step_card(
    step_number: int,
    total_steps: int,
    direction: str,
    step_ids: list[str],
    image_b64: str | None,
    pieces_by_id: dict[str, PlacedPiece],
    styles,
    image_width: int,
    image_height: int,
    is_pallet_plan: bool,
    has_tilt: bool = False,
    delivery_info: "UnloadStepDeliveryInfo | None" = None,
    extra_notes: list[str] | None = None,
    compact_header: bool = False,
) -> list:
    """Contenido de un "Step Card" (Redesign v2, secciones 9-22): "STEP X OF
    N" + accion explicita ("LOADING: LOAD THESE ITEMS"/"UNLOADING: REMOVE
    THESE ITEMS") + imagen 3D + fila de 2 columnas (Step Content / Step
    Notes) en vez de una tabla chica centrada sola. `direction` ("load"/
    "unload") reemplaza la inferencia implicita anterior via
    `delivery_info is not None` -mas explicito, y necesario porque ahora
    tambien decide la accion/Step Notes de Loading.

    `compact_header` (Adaptive Pagination): SOLO reduce los Spacer entre
    titulo/imagen/tabla -nunca la fuente (serian ilegibles) ni el contenido
    de la tabla/notas. El tamano de la imagen en si lo decide el CALLER via
    `image_width`/`image_height` (ver _STEP_SIZE_IMAGE_DIMS/
    _classify_step_size en _pack_step_cards)."""
    title_gap = 1 if compact_header else 2
    image_gap = 2 if compact_header else 3
    card: list = [
        Paragraph(f"STEP {step_number} OF {total_steps}", _STEP_TITLE_STYLE),
        Paragraph("LOADING: LOAD THESE ITEMS" if direction == "load" else "UNLOADING: REMOVE THESE ITEMS", _STEP_ACTION_STYLE),
        Spacer(1, title_gap),
    ]
    if image_b64:
        # PDF image proportion fix: NUNCA forzar (image_width, image_height)
        # tal cual -eso estira/aplasta la imagen si su proporcion nativa no
        # coincide (ver _fit_image_dims). Se calcula el mayor tamano que
        # entra en esa caja preservando la proporcion real capturada por el
        # navegador, y se centra horizontalmente (hAlign) para que no quede
        # pegada a la izquierda cuando es mas angosta que la caja completa.
        fitted_width, fitted_height = _fit_image_dims(image_b64, image_width, image_height)
        img = Image(_decode_png(image_b64), width=fitted_width, height=fitted_height)
        img.hAlign = "CENTER"
        card.append(img)
        card.append(Spacer(1, image_gap))

    content_col = [Paragraph("STEP CONTENT", _NOTES_HEADING_STYLE), _step_content_table(pieces_by_id, step_ids, is_pallet_plan, has_tilt)]
    notes_col = _step_notes_flowables(direction, delivery_info, extra_notes, styles)
    info_row = Table([[content_col, notes_col]], colWidths=[_STEP_CONTENT_WIDTH, _STEP_NOTES_WIDTH])
    info_row.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (0, 0), 0),
                ("LEFTPADDING", (1, 0), (1, 0), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    card.append(info_row)
    return card


def _measure_flowables_height(flowables: list, avail_width: float) -> float:
    """Alto real de una lista de Flowables ya armada (Paragraph/Table/Image/
    Spacer), sumando Flowable.wrap(avail_width, altura_enorme)[1] -el mismo
    mecanismo que SimpleDocTemplate usa internamente para decidir si algo
    entra en lo que queda de una pagina (Table.wrap() ya calcula el alto de
    fila mas alto entre celdas con listas de Flowables anidadas -info_row
    en _step_card- igual que lo hace al dibujar de verdad). Se llama sobre
    los MISMOS objetos que terminan en el story final (ver
    _pack_step_cards, nunca se reconstruye el Step Card despues de medirlo)
    para que la decision de paginacion sea exacta, no una estimacion."""
    total = 0.0
    for flowable in flowables:
        _, height = flowable.wrap(avail_width, 0xFFFF)
        total += height
    return total


def _pack_step_cards(steps_meta: list[dict], styles, avail_width: float, avail_height: float) -> list:
    """Arma TODAS las Step Cards (clasificando su tamano con
    _classify_step_size y armando/midiendo su alto real con
    _measure_flowables_height) y las empaqueta en paginas -reemplaza el
    "2/pagina fijo" anterior (_batch_steps_for_pages, eliminada).

    Sigue el orden original de `steps_meta` siempre (JAMAS reordena),
    acumula Cards completas mientras entren en lo que queda de la pagina
    actual, respetando ademas el limite duro de _MAX_STEPS_PER_PAGE (4,
    "Never exceed 4 Steps per page" del pedido) aunque tecnicamente
    entrarian mas, e inserta un PageBreak() ENTERO antes de la primera Card
    que no entre -nunca la encoge, nunca la parte. Cada Card sigue envuelta
    en KeepTogether ademas de la decision de altura ya medida -garantia de
    ultima linea si el calculo tuviera algun margen de error de redondeo
    entre el wrap() de medicion y el wrap() real de doc.build().

    Cada elemento de `steps_meta` es un dict con: step_number, total_steps,
    direction, step_ids, image, pieces_by_id, is_pallet_plan, has_tilt,
    delivery_info (None para Loading), extra_notes (None salvo prerequisito
    cruzado de Group)."""
    story: list = []
    used_height = 0.0
    count_on_page = 0

    for meta in steps_meta:
        n_rows = len(build_guide_step_rows(meta["pieces_by_id"], meta["step_ids"]))
        delivery_info = meta.get("delivery_info")
        n_conflicts = len(delivery_info.conflict_messages) if delivery_info is not None else 0
        has_prerequisite = bool(meta.get("extra_notes"))
        size = _classify_step_size(n_rows, n_conflicts, has_prerequisite)
        image_width, image_height = _STEP_SIZE_IMAGE_DIMS[size]

        card = _step_card(
            meta["step_number"], meta["total_steps"], meta["direction"], meta["step_ids"], meta["image"],
            meta["pieces_by_id"], styles, image_width, image_height, meta["is_pallet_plan"], meta["has_tilt"],
            delivery_info, meta.get("extra_notes"), compact_header=(size == "compact"),
        )
        card_height = _measure_flowables_height(card, avail_width)

        needs_new_page = count_on_page > 0 and (
            count_on_page >= _MAX_STEPS_PER_PAGE or used_height + _STEP_CARD_GAP + card_height > avail_height
        )
        if needs_new_page:
            story.append(PageBreak())
            used_height = 0.0
            count_on_page = 0

        if count_on_page > 0:
            story.append(Spacer(1, _STEP_CARD_GAP))
            used_height += _STEP_CARD_GAP
        story.append(KeepTogether(card))
        used_height += card_height
        count_on_page += 1

    return story


# ==========================================================================
# Redesign v2, Part A -portada operacional real (Plan Information/
# Operational Metrics/Groups/Plan Validation/Color Legend), compartida por
# Loading Guide y Full Unloading Guide. El Group Unloading Guide tiene su
# propia variante (_group_cover_page, mas abajo) que agrega "DELIVERY FOR
# <Group>" y metricas acotadas al Group -pero reusa los mismos sub-helpers
# (_plan_info_table/_color_legend_block/etc.), nunca duplica la logica.
# ==========================================================================


def _cover_title(lines: list[str], styles) -> list:
    story: list = []
    if _LOGO_PATH.exists():
        story.append(Image(str(_LOGO_PATH), width=_LOGO_WIDTH, height=_LOGO_HEIGHT))
        story.append(Spacer(1, 8))
    for line in lines:
        story.append(Paragraph(line, styles["Title"]))
    story.append(Spacer(1, 10))
    return story


def _plan_info_table(plan_name: str | None, load_type_label: str | None, load_space_name: str, meta: ReportMetadata) -> Table:
    """Seccion 3 del pedido: "Load Space", NUNCA "Container Type", para no
    llamar "container" a un Load Space custom que no lo es (ej. "Load
    Space: Raquel Zaki"). plan_name/load_type_label son best-effort (ver
    routes.py:_plan_name_and_type) -None se omite en vez de mostrar un
    placeholder feo."""
    rows: list[list] = []
    if plan_name:
        rows.append(["Plan Name", plan_name])
    if load_type_label:
        rows.append(["Load Type", load_type_label])
    rows.append(["Load Space", load_space_name])
    if meta.project_name:
        rows.append(["Project Name", meta.project_name])
    if meta.customer:
        rows.append(["Customer", meta.customer])
    rows.append(["Generated Date", date.today().isoformat()])
    table = Table(rows, colWidths=[150, 330])
    table.setStyle(_INFO_TABLE_STYLE)
    return table


def _volume_m3(pieces: list[PlacedPiece]) -> float:
    return sum(p.dx * p.dy * p.dz for p in pieces) / 1e9


def _operational_metrics_table(state: PackingResult, step_count: int) -> Table:
    """Seccion 4 del pedido: SOLO metricas canonicas ya disponibles en
    PackingMetrics/placed -nunca se inventa una metrica nueva (ej. no existe
    "Loaded Volume" como campo propio de PackingMetrics, se deriva de
    placed[].dx*dy*dz, igual criterio que _group_metrics)."""
    metrics = state.metrics
    loaded_volume_m3 = round(_volume_m3(state.placed), 2)
    rows = [
        ["Total Units", metrics.total_pieces],
        ["Loaded Units", metrics.loaded_pieces],
        ["Unloaded Units", metrics.unloaded_pieces],
        ["Total Weight (kg)", metrics.total_weight],
        ["Loaded Volume (m3)", loaded_volume_m3],
        ["Space Utilization", f"{metrics.used_volume_pct}%"],
        ["Number of Groups", metrics.number_of_groups],
        ["Number of Systems", metrics.number_of_systems],
        ["Number of Steps", step_count],
    ]
    table = Table(rows, colWidths=[150, 330])
    table.setStyle(_INFO_TABLE_STYLE)
    return table


# Seccion 7 del pedido: mismo texto/orden para Loading vs Unloading (solo
# cambia el verbo -"Load"/"Remove"- y el ultimo estado -"Not Yet Loaded"/
# "Already Removed"). El swatch de "Blue-gray translucent" usa BLUE_GRAY_HEX
# -el MISMO valor que frontend/PieceMesh.tsx:GRAY_PAST_COLOR, para describir
# exactamente lo que el operador ve en la imagen, nunca una aproximacion.
_LOAD_LEGEND: list[tuple[str | None, str]] = [
    (_CURRENT_STEP_HEX, "Current Step — Load Now"),
    (BLUE_GRAY_HEX, "Already Loaded"),
    (None, "Not Yet Loaded (hidden)"),
]
_UNLOAD_LEGEND: list[tuple[str | None, str]] = [
    (_CURRENT_STEP_HEX, "Current Step — Remove Now"),
    (BLUE_GRAY_HEX, "Remains Inside"),
    (None, "Already Removed (hidden)"),
]


def _color_legend_block(direction: str, styles) -> list:
    story: list = [Paragraph("COLOR LEGEND", styles["Heading4"])]
    legend = _LOAD_LEGEND if direction == "load" else _UNLOAD_LEGEND
    rows = []
    for hex_color, label in legend:
        swatch_color = hex_color or "#bbbbbb"
        swatch = Paragraph(f'<font color="{swatch_color}">■</font>', styles["Normal"])
        rows.append([swatch, label])
    table = Table(rows, colWidths=[20, 300])
    table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("FONTSIZE", (0, 0), (-1, -1), 9)]))
    story.append(table)
    story.append(Spacer(1, 10))
    return story


def available_unload_groups(placed: list[PlacedPiece]) -> list[str]:
    """Groups distintos presentes en el plan, para poblar el checklist del
    frontend -mismo criterio que ColorLegend.tsx (Set + sort, ignora "").
    Redesign v2: tambien se reusa para la seccion GROUPS de la portada del
    Full Unloading Guide y para el Load Composition Summary."""
    return sorted({p.group for p in placed if p.group})


def _operational_cover_page(
    direction: str,
    state: PackingResult,
    meta: ReportMetadata,
    plan_name: str | None,
    load_type_label: str | None,
    step_count: int,
    validation: "ReportValidationResponse | None",
    styles,
) -> list:
    """Portada real de Loading Guide / Full Unloading Guide (seccion 1 del
    pedido: Page 1 NUNCA tiene ningun Step -Step 1 arranca en Page 2, ver el
    PageBreak() al final de esta funcion)."""
    # Correccion, cover cleanup (seccion 12): el logo YA incluye el wordmark
    # "Cubox" -repetirlo como titulo aparte era visualmente redundante
    # ("Cubox" bajo el logo, y otra vez "CUBOX" como titulo). Solo el tipo
    # de guia queda como titulo.
    story = _cover_title(["LOADING GUIDE" if direction == "load" else "UNLOADING GUIDE"], styles)

    story.append(Paragraph("PLAN INFORMATION", styles["Heading4"]))
    story.append(_plan_info_table(plan_name, load_type_label, state.container.name, meta))
    story.append(Spacer(1, 10))

    story.append(Paragraph("OPERATIONAL METRICS", styles["Heading4"]))
    story.append(_operational_metrics_table(state, step_count))
    story.append(Spacer(1, 10))

    if direction == "unload":
        groups = available_unload_groups(state.placed)
        if groups:
            # Seccion 5 del pedido: lista compacta de nombres, NUNCA el
            # listado completo de items por Group (eso vive en el Load
            # Composition Summary, al final del documento).
            story.append(Paragraph("GROUPS", styles["Heading4"]))
            for group in groups:
                story.append(Paragraph(group, styles["Normal"]))
            story.append(Spacer(1, 10))

    if validation is not None:
        story.extend(_plan_validation_block(validation, styles))
        if validation.status == PlanValidationStatus.NOT_READY:
            story.extend(_export_override_box(validation, styles))

    story.extend(_color_legend_block(direction, styles))
    story.append(PageBreak())
    return story


def _group_piece_ids(placed: list[PlacedPiece], group: str) -> set[str]:
    return {p.id for p in placed if p.group == group}


def _filter_unload_steps_by_group(
    steps: list[list[str]], pieces_by_id: dict[str, PlacedPiece], group: str
) -> tuple[list[list[str]], list[int]]:
    """Filtra los steps YA RESUELTOS (mismo orden fisico, sin recalcular
    ninguna dependencia) a solo las piezas del Group pedido -un step se
    descarta por completo si ninguna de sus piezas pertenece al Group, pero
    NUNCA se agregan piezas de otro Group a una card de esta guia. Devuelve
    tambien el indice ORIGINAL de cada step conservado, para poder ubicar
    la imagen (un PNG por paso del plan COMPLETO, capturado por el
    frontend) que le corresponde a ese paso."""
    filtered: list[list[str]] = []
    original_indices: list[int] = []
    for i, step_ids in enumerate(steps):
        group_ids = [pid for pid in step_ids if pieces_by_id.get(pid) is not None and pieces_by_id[pid].group == group]
        if group_ids:
            filtered.append(group_ids)
            original_indices.append(i)
    return filtered, original_indices


def _cross_group_prerequisite_note(
    state: PackingResult, group_step_ids: list[str], group: str, pieces_by_id: dict[str, PlacedPiece]
) -> str | None:
    """Si alguna pieza de este paso (ya filtrado a `group`) esta fisicamente
    bloqueada -`state.blocked_by`, la MISMA fuente que Fase 6A, nunca un
    segundo algoritmo de bloqueo- por una pieza de OTRO Group, esa pieza
    bloqueadora jamas va a aparecer en esta guia (esta filtrada afuera) -hay
    que avisarlo explicito en vez de omitirlo en silencio. Un bloqueador del
    MISMO Group no genera nota: ya aparece en un paso anterior de esta misma
    guia, en el mismo orden fisico (nunca se reordena)."""
    foreign: dict[str, str] = {}
    for pid in group_step_ids:
        for blocker_id in state.blocked_by.get(pid, []):
            blocker = pieces_by_id.get(blocker_id)
            if blocker is not None and blocker.group != group:
                foreign[blocker.code] = blocker.group or "(no group)"
    if not foreign:
        return None
    parts = [f"{code}, Group {grp}" for code, grp in sorted(foreign.items())]
    return "OPERATIONAL PREREQUISITE: before unloading this step, temporarily remove: " + "; ".join(parts)


def _group_metrics(placed: list[PlacedPiece], group: str) -> dict:
    pieces = [p for p in placed if p.group == group]
    return {
        "units": len(pieces),
        "weight": round(sum(p.weight for p in pieces), 2),
        "volume_m3": round(_volume_m3(pieces), 3),
        "systems": sorted({p.system for p in pieces if p.system}),
        "delivery_sequences": sorted({p.delivery_sequence for p in pieces if p.delivery_sequence is not None}),
    }


def _group_operational_status(state: PackingResult, group_piece_ids: set[str]) -> str:
    """Igual criterio que el "Operational Sequence" del Full Guide/Container
    Report, pero acotado a warnings que involucran a alguna pieza de este
    Group (item_id o blocking_item_id) -nunca recalcula ni inventa un
    warning nuevo, solo filtra los que ya calculo Fase 6A. Fallback usado
    solo cuando `validation` (Fase 6C) no fue provisto -ver _group_cover_page."""
    relevant = [w for w in state.operational_warnings if w.item_id in group_piece_ids or w.blocking_item_id in group_piece_ids]
    return "Valid" if not relevant else f"{len(relevant)} Warning(s)"


def _group_cover_page(
    state: PackingResult,
    group: str,
    group_step_count: int,
    meta: ReportMetadata,
    plan_name: str | None,
    load_type_label: str | None,
    validation: "ReportValidationResponse | None",
    styles,
) -> list:
    """Portada del Group Unloading Guide (seccion 2/24 del pedido): "CUBOX /
    UNLOADING GUIDE" + "DELIVERY FOR / <Group>", Plan Information, Delivery
    Sequence(s) del Group, Group Metrics, Plan Validation + Color Legend
    -mismos sub-helpers que _operational_cover_page, nunca una segunda
    implementacion de portada."""
    metrics = _group_metrics(state.placed, group)
    group_piece_ids = _group_piece_ids(state.placed, group)

    story = _cover_title(["UNLOADING GUIDE"], styles)  # ver comentario en _operational_cover_page (cover cleanup, seccion 12)
    story.append(Paragraph("DELIVERY FOR", styles["Heading4"]))
    story.append(Paragraph(group, styles["Heading2"]))
    story.append(Spacer(1, 8))

    story.append(Paragraph("PLAN INFORMATION", styles["Heading4"]))
    story.append(_plan_info_table(plan_name, load_type_label, state.container.name, meta))
    story.append(Spacer(1, 10))

    if not metrics["delivery_sequences"]:
        story.append(Paragraph("Delivery Sequence: Not specified", styles["Normal"]))
    elif len(metrics["delivery_sequences"]) == 1:
        story.append(Paragraph(f"Delivery Sequence: {metrics['delivery_sequences'][0]}", styles["Normal"]))
    else:
        story.append(
            Paragraph(f"Delivery Sequences: {', '.join(str(v) for v in metrics['delivery_sequences'])}", styles["Normal"])
        )
        story.append(Paragraph("Note: this Group spans multiple Delivery Sequences.", styles["Italic"]))
    story.append(Spacer(1, 8))

    story.append(Paragraph("GROUP METRICS", styles["Heading4"]))
    rows = [
        ["Units", metrics["units"]],
        ["Weight (kg)", metrics["weight"]],
        ["Volume (m3)", metrics["volume_m3"]],
        ["Systems", ", ".join(metrics["systems"]) if metrics["systems"] else "-"],
        ["Unload Steps", group_step_count],
    ]
    table = Table(rows, colWidths=[150, 330])
    table.setStyle(_INFO_TABLE_STYLE)
    story.append(table)
    story.append(Spacer(1, 10))

    if validation is not None:
        story.extend(_plan_validation_block(validation, styles))
        if validation.status == PlanValidationStatus.NOT_READY:
            story.extend(_export_override_box(validation, styles))
    else:
        story.append(Paragraph(f"Operational Status: {_group_operational_status(state, group_piece_ids)}", styles["Normal"]))
        story.append(Spacer(1, 8))

    story.extend(_color_legend_block("unload", styles))
    story.append(PageBreak())
    return story


# ==========================================================================
# Redesign v2, secciones 25-28: Load Composition Summary (Group -> System)
# + Final Load Summary, al final de cada guia. El Full Packing List sigue
# siendo un reporte SEPARADO (Excel) -esto es puramente informativo, nunca
# reemplaza ni embebe esa lista completa.
# ==========================================================================

_COMPOSITION_TABLE_HEADER = ["Group / System", "Units", "Weight (kg)", "Volume (m3)", "% Loaded Vol", "% Container Vol"]


def _composition_summary(state: PackingResult, styles, only_group: str | None = None) -> list:
    """Full guide: Plan -> Group -> System. Group Guide (`only_group` seteado):
    Selected Group -> System. Un plan sin ningun Group definido colapsa a un
    unico bucket "Plan Total" -> System (seccion 27: "omit breakdown if
    cleaner" en vez de forzar un nivel de Group vacio)."""
    story: list = [Paragraph("LOAD COMPOSITION SUMMARY", styles["Heading3"])]
    total_loaded_volume = _volume_m3(state.placed)
    container_volume = (state.container.length * state.container.width * state.container.height) / 1e9

    def pct(volume: float) -> tuple[float, float]:
        loaded_pct = round((volume / total_loaded_volume) * 100, 1) if total_loaded_volume else 0.0
        container_pct = round((volume / container_volume) * 100, 1) if container_volume else 0.0
        return loaded_pct, container_pct

    if only_group:
        buckets: list[str | None] = [only_group]
    else:
        buckets = available_unload_groups(state.placed) or [None]

    rows: list[list] = [_COMPOSITION_TABLE_HEADER]
    bold_rows: list[int] = []
    for bucket in buckets:
        bucket_pieces = [p for p in state.placed if p.group == bucket] if bucket is not None else state.placed
        units = len(bucket_pieces)
        weight = round(sum(p.weight for p in bucket_pieces), 2)
        volume = _volume_m3(bucket_pieces)
        loaded_pct, container_pct = pct(volume)
        label = bucket if bucket is not None else "Plan Total"
        rows.append([label, units, weight, round(volume, 3), f"{loaded_pct}%", f"{container_pct}%"])
        bold_rows.append(len(rows) - 1)

        systems = sorted({p.system or "Unspecified" for p in bucket_pieces})
        for system in systems:
            system_pieces = [p for p in bucket_pieces if (p.system or "Unspecified") == system]
            s_units = len(system_pieces)
            s_weight = round(sum(p.weight for p in system_pieces), 2)
            s_volume = _volume_m3(system_pieces)
            s_loaded_pct, s_container_pct = pct(s_volume)
            rows.append([f"    {system}", s_units, s_weight, round(s_volume, 3), f"{s_loaded_pct}%", f"{s_container_pct}%"])

    table = Table(rows, colWidths=[170, 55, 80, 80, 75, 90], repeatRows=1)
    style_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2a2a2d")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#999999")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]
    for r in bold_rows:
        style_cmds.append(("FONTNAME", (0, r), (0, r), "Helvetica-Bold"))
        style_cmds.append(("BACKGROUND", (0, r), (-1, r), colors.HexColor("#e8e8e8")))
    table.setStyle(TableStyle(style_cmds))
    story.append(table)
    story.append(Spacer(1, 12))
    return story


def _final_load_summary(state: PackingResult, step_count: int, validation: "ReportValidationResponse | None", styles) -> list:
    """Seccion 28 del pedido: mismo tipo de metricas que la portada, al
    cierre del documento -Plan Status usa el mismo `validation.status` que
    ya se muestra en la portada (Fase 6C autoritativo), "N/A" si el caller
    no lo proveyo (compatibilidad hacia atras con builders llamados sin
    `validation`)."""
    status_label = _PLAN_STATUS_LABELS[validation.status] if validation is not None else "N/A"
    metrics = state.metrics
    loaded_volume_m3 = round(_volume_m3(state.placed), 2)
    rows = [
        ["Total Units", metrics.total_pieces],
        ["Loaded Units", metrics.loaded_pieces],
        ["Unloaded Units", metrics.unloaded_pieces],
        ["Total Weight (kg)", metrics.total_weight],
        ["Loaded Volume (m3)", loaded_volume_m3],
        ["Space Utilization", f"{metrics.used_volume_pct}%"],
        ["Groups", metrics.number_of_groups],
        ["Systems", metrics.number_of_systems],
        ["Steps", step_count],
        ["Plan Status", status_label],
    ]
    table = Table(rows, colWidths=[160, 260])
    table.setStyle(_INFO_TABLE_STYLE)
    return [Paragraph("FINAL LOAD SUMMARY", styles["Heading3"]), table]


def _build_guide_pdf(
    title: str,
    state: PackingResult,
    steps: list[list[str]],
    step_images_png_base64: list[str],
    meta: ReportMetadata,
    unload_step_info: list["UnloadStepDeliveryInfo"] | None = None,
    optimization_mode: OptimizationMode | None = None,
    plan_name: str | None = None,
    load_type_label: str | None = None,
    validation: "ReportValidationResponse | None" = None,
) -> bytes:
    direction = "unload" if unload_step_info is not None else "load"
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
    # Redesign v2, seccion 1: Page 1 es SIEMPRE la portada -nunca comparte
    # pagina con Step 1 (PageBreak() al final de _operational_cover_page).
    story = _operational_cover_page(direction, state, meta, plan_name, load_type_label, len(steps), validation, styles)

    # Fase 6B.3, seccion 6/9 del pedido: la seccion resumen de Operational
    # Warnings es EXCLUSIVA del Unloading Guide (unload_step_info is not
    # None marca eso) -el Loading Guide no la recibe. En su propia pagina
    # (PageBreak) para no compartir espacio a medias con el primer Step Card.
    if unload_step_info is not None:
        story.extend(_operational_warnings_summary(state.operational_warnings, optimization_mode, styles))
        story.append(PageBreak())

    pieces_by_id = {p.id: p for p in state.placed}
    is_pallet_plan = _is_palletized_plan(state.placed)
    has_tilt = _has_tilt_capable_pieces(state.placed)
    total_steps = len(steps)

    def image_for(i: int) -> str | None:
        return step_images_png_base64[i] if i < len(step_images_png_base64) else None

    def delivery_info_for(i: int) -> "UnloadStepDeliveryInfo | None":
        return unload_step_info[i] if unload_step_info is not None else None

    # Adaptive Operational Guide Pagination: cada Step Card se clasifica
    # (compact/standard/large, ver _classify_step_size) y se empaqueta segun
    # su alto REAL medido -ver _pack_step_cards, que sigue envolviendo cada
    # Card en KeepTogether (nunca se agrega suelta, nunca se parte a la
    # mitad entre paginas).
    steps_meta = [
        {
            "step_number": idx + 1,
            "total_steps": total_steps,
            "direction": direction,
            "step_ids": step_ids,
            "image": image_for(idx),
            "pieces_by_id": pieces_by_id,
            "is_pallet_plan": is_pallet_plan,
            "has_tilt": has_tilt,
            "delivery_info": delivery_info_for(idx),
            "extra_notes": None,
        }
        for idx, step_ids in enumerate(steps)
    ]
    story.extend(_pack_step_cards(steps_meta, styles, _GUIDE_CONTENT_WIDTH, _GUIDE_CONTENT_HEIGHT))

    # Secciones 25-28: Load Composition Summary + Final Load Summary, en su
    # propia pagina -nunca comparten espacio a medias con el ultimo Step.
    story.append(PageBreak())
    story.extend(_composition_summary(state, styles))
    story.extend(_final_load_summary(state, total_steps, validation, styles))

    footer = _make_override_footer(validation)
    doc.build(story, onFirstPage=_blank_canvas, onLaterPages=footer or _blank_canvas)
    return buf.getvalue()


def build_loading_guide_pdf(
    state: PackingResult,
    steps: list[list[str]],
    step_images_png_base64: list[str],
    meta: ReportMetadata,
    optimization_mode: OptimizationMode | None = None,
    plan_name: str | None = None,
    load_type_label: str | None = None,
    validation: "ReportValidationResponse | None" = None,
) -> bytes:
    # Fase 6B.3: `optimization_mode` se acepta solo para que _guide_pdf en
    # routes.py pueda llamar a build_loading_guide_pdf/
    # build_unloading_guide_pdf con la misma firma (refactor compartido
    # inofensivo, seccion 9 del pedido) -el Loading Guide no lo usa, no
    # tiene seccion de Operational Warnings (exclusiva del Unloading Guide).
    return _build_guide_pdf(
        "Loading Guide", state, steps, step_images_png_base64, meta,
        plan_name=plan_name, load_type_label=load_type_label, validation=validation,
    )


def build_unloading_guide_pdf(
    state: PackingResult,
    steps: list[list[str]],
    step_images_png_base64: list[str],
    meta: ReportMetadata,
    optimization_mode: OptimizationMode | None = None,
    plan_name: str | None = None,
    load_type_label: str | None = None,
    validation: "ReportValidationResponse | None" = None,
) -> bytes:
    # Fase 6B: usa los mismos operational_warnings YA calculados por Fase
    # 6A (state.operational_warnings) -nunca recalcula el motor de
    # secuencia/warnings aca. `steps` NUNCA se reordena.
    unload_step_info = annotate_unload_steps_with_delivery(steps, state.placed, state.operational_warnings)
    return _build_guide_pdf(
        "Unloading Guide", state, steps, step_images_png_base64, meta, unload_step_info, optimization_mode,
        plan_name=plan_name, load_type_label=load_type_label, validation=validation,
    )


# ==========================================================================
# Load Organization Model Cleanup -Group Unloading Guide: un PDF
# independiente por Group, generado a partir de los MISMOS `steps` ya
# resueltos por compute_unload_steps (nunca se recalculan dependencias ni
# se reordena nada aca). Full Unloading Guide (build_unloading_guide_pdf,
# arriba) queda intacto -esto es un camino nuevo, no un reemplazo.
# ==========================================================================


def build_unloading_guide_pdf_for_group(
    state: PackingResult,
    steps: list[list[str]],
    step_images_png_base64: list[str],
    meta: ReportMetadata,
    optimization_mode: OptimizationMode | None,
    group: str,
    plan_name: str | None = None,
    load_type_label: str | None = None,
    validation: "ReportValidationResponse | None" = None,
) -> bytes:
    """Group Unloading Guide (una sola PDF, para UN Group): filtra los
    steps ya resueltos por `steps` (compute_unload_steps/chunk_sequence, sin
    tocar) a las piezas de `group`, en el MISMO orden fisico -nunca vuelve a
    correr el packer ni recalcula dependencias/warnings (Fase 6A). Portada
    con Plan Information + metricas del Group + Delivery Sequence(s)
    presentes + Plan Validation (Fase 6C), seguida de step cards -mismo
    estilo que el Full Guide, con una nota "OPERATIONAL PREREQUISITE" cuando
    un paso depende de una pieza de otro Group que quedo fuera de esta guia."""
    pieces_by_id = {p.id: p for p in state.placed}
    group_steps, original_indices = _filter_unload_steps_by_group(steps, pieces_by_id, group)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        title=f"CUBOX - Unloading Guide - {group}",
        leftMargin=_GUIDE_MARGIN,
        rightMargin=_GUIDE_MARGIN,
        topMargin=_GUIDE_MARGIN,
        bottomMargin=_GUIDE_MARGIN,
    )
    styles = getSampleStyleSheet()
    story = _group_cover_page(state, group, len(group_steps), meta, plan_name, load_type_label, validation, styles)

    unload_step_info = annotate_unload_steps_with_delivery(group_steps, state.placed, state.operational_warnings)
    is_pallet_plan = _is_palletized_plan(state.placed)
    has_tilt = _has_tilt_capable_pieces(state.placed)
    total_steps = len(group_steps)

    def image_for(original_index: int) -> str | None:
        return step_images_png_base64[original_index] if original_index < len(step_images_png_base64) else None

    # Adaptive Operational Guide Pagination: ver comentario equivalente en
    # _build_guide_pdf, misma logica (_pack_step_cards -clasificacion +
    # empaquetado por alto real- en vez de "2 por pagina" fijo).
    steps_meta = []
    for pos, step_ids in enumerate(group_steps):
        note = _cross_group_prerequisite_note(state, step_ids, group, pieces_by_id)
        steps_meta.append(
            {
                "step_number": pos + 1,
                "total_steps": total_steps,
                "direction": "unload",
                "step_ids": step_ids,
                "image": image_for(original_indices[pos]),
                "pieces_by_id": pieces_by_id,
                "is_pallet_plan": is_pallet_plan,
                "has_tilt": has_tilt,
                "delivery_info": unload_step_info[pos],
                "extra_notes": [note] if note else None,
            }
        )
    story.extend(_pack_step_cards(steps_meta, styles, _GUIDE_CONTENT_WIDTH, _GUIDE_CONTENT_HEIGHT))

    story.append(PageBreak())
    story.extend(_composition_summary(state, styles, only_group=group))
    story.extend(_final_load_summary(state, total_steps, validation, styles))

    footer = _make_override_footer(validation)
    doc.build(story, onFirstPage=_blank_canvas, onLaterPages=footer or _blank_canvas)
    return buf.getvalue()
