"""Endpoints de la API. MVP local: sin auth, sin base de datos.

El resultado de cubicaje mas reciente se guarda en memoria de proceso
(`_current_state`) para poder validar y aplicar ediciones manuales (mover,
rotar, quitar, insertar, lock) sin necesidad de una base de datos.
`_current_state` tambien guarda un historial de undo/redo (`core/history.py`)
y la configuracion activa (pasillo/clearance/optimization mode/weight
balance) para que la edicion manual y Optimize Remaining sigan respetando lo
que el usuario eligio en el ultimo Optimize.
"""

from fastapi import APIRouter, File, HTTPException, Response, UploadFile

from app.core.excel_export import build_export_workbook
from app.core.excel_import import parse_excel
from app.core.final_validation import validate_for_export
from app.core.geometry import Box, boxes_overlap, within_container
from app.core.history import EditHistory
from app.core.manual_move import validate_move, validate_placement
from app.core.optimize import run_optimization
from app.core.orientation import get_valid_orientations, toggle_orientation, turn_orientation
from app.core.packer import compute_metrics
from app.core.pdf_export import build_container_report_pdf, build_loading_guide_pdf, build_unloading_guide_pdf
from app.core.reasons import UnloadedReason
from app.core.reserved_zones import ReservedZone, central_aisle_zone
from app.core.sequence import (
    chunk_sequence,
    compute_load_sequence,
    compute_load_steps,
    compute_unload_sequence,
    compute_unload_steps,
    detect_operational_warnings,
)
from app.models.containers import get_container, list_containers
from app.models.schemas import (
    ContainerReportRequest,
    ContainerSpec,
    InsertPieceRequest,
    LoadingAnchor,
    LockPieceRequest,
    MoveRequest,
    MoveValidationResult,
    OptimizationMode,
    OptimizeRemainingRequest,
    OptimizeResponse,
    PackRequest,
    PackingResult,
    PlacedPiece,
    GuideReportRequest,
    RemovePieceRequest,
    ReportDirection,
    ReportStepsRequest,
    ReportStepsResponse,
    ReportValidationResponse,
    ReservedZoneOut,
    RotatePieceRequest,
    StepMode,
    UnloadedItem,
    UnlockPieceRequest,
    WeightBalanceMode,
    WindowItem,
)

router = APIRouter(prefix="/api")

TOL = 1e-6

_current_state: dict = {
    "result": None,
    "container": None,
    "history": EditHistory(),
    "reserved_zones": [],
    "clearance": 0.0,
    "optimization_mode": OptimizationMode.BEST_SPACE,
    "weight_balance_mode": WeightBalanceMode.NORMAL,
    "loading_anchor": LoadingAnchor.BACK_RIGHT,
}


def _get_active_state() -> PackingResult:
    state: PackingResult | None = _current_state["result"]
    if state is None:
        raise HTTPException(400, "No hay un cubicaje activo. Ejecuta /api/pack primero.")
    return state


def _reserved_zones() -> list[ReservedZone]:
    return _current_state["reserved_zones"]


def _reserved_zones_out(zones: list[ReservedZone]) -> list[ReservedZoneOut]:
    """Version serializable de las zonas reservadas para exponer en
    PackingResult -asi el frontend puede dibujar el pasillo central
    exactamente donde el backend lo calculo, sin recalcular el centrado por
    su cuenta."""
    return [ReservedZoneOut(x=z.x, y=z.y, z=z.z, length=z.length, width=z.width, height=z.height, label=z.label) for z in zones]


def _clearance() -> float:
    return _current_state["clearance"]


def _find_placed(state: PackingResult, piece_id: str) -> PlacedPiece:
    piece = next((p for p in state.placed if p.id == piece_id), None)
    if piece is None:
        raise HTTPException(404, f"Pieza {piece_id} no encontrada en el cubicaje actual")
    return piece


def _ensure_unlocked(piece: PlacedPiece) -> None:
    if piece.locked:
        raise HTTPException(409, f"La pieza {piece.id} esta bloqueada (Locked). Desbloqueala primero.")


def _refresh_derived(state: PackingResult) -> None:
    state.metrics = compute_metrics(_current_state["container"], state.placed, state.unloaded)
    state.load_sequence = compute_load_sequence(state.placed, _current_state["container"], _current_state["loading_anchor"])
    state.unload_sequence = compute_unload_sequence(state.placed)
    state.load_sequence_warnings = detect_operational_warnings(
        state.placed, _current_state["container"], _current_state["loading_anchor"]
    )


def _window_item_from_placed(p: PlacedPiece) -> WindowItem:
    return WindowItem(
        code=p.code,
        description=p.description,
        width=p.source_width,
        height=p.source_height,
        thickness=p.source_thickness,
        weight=p.weight,
        quantity=1,
        system=p.system,
        group=p.group,
        stackable=p.stackable,
        priority=p.priority,
        max_stack_weight=p.max_stack_weight,
        delivery_sequence=p.delivery_sequence,
        item_type=p.item_type,
        orientation_policy=p.orientation_policy,
    )


def _window_item_from_unloaded(u: UnloadedItem) -> WindowItem:
    return WindowItem(
        code=u.code,
        description=u.description,
        width=u.width,
        height=u.height,
        thickness=u.thickness,
        weight=u.weight,
        quantity=1,
        system=u.system,
        group=u.group,
        stackable=u.stackable,
        priority=u.priority,
        max_stack_weight=u.max_stack_weight,
        delivery_sequence=u.delivery_sequence,
        item_type=u.item_type,
        orientation_policy=u.orientation_policy,
    )


@router.get("/containers", response_model=list[ContainerSpec])
def get_containers():
    return list_containers()


@router.get("/state", response_model=PackingResult)
def get_state():
    return _get_active_state()


@router.post("/import-excel", response_model=list[WindowItem])
async def import_excel(file: UploadFile = File(...)):
    if not file.filename.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(400, "El archivo debe ser .xlsx")
    content = await file.read()
    try:
        return parse_excel(content)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/pack", response_model=OptimizeResponse)
def pack(request: PackRequest):
    try:
        container = get_container(request.container_id)
    except KeyError as e:
        raise HTTPException(404, str(e))

    zones: list[ReservedZone] = []
    if request.enable_central_aisle:
        zones.append(central_aisle_zone(container, request.aisle_width_mm))

    best, alternatives = run_optimization(
        request.items,
        container,
        request.optimization_mode,
        zones,
        request.clearance_mm,
        request.weight_balance_mode,
    )

    zones_out = _reserved_zones_out(zones)
    for alt in alternatives:
        alt.result.load_sequence = compute_load_sequence(alt.result.placed, container, request.loading_anchor)
        alt.result.unload_sequence = compute_unload_sequence(alt.result.placed)
        alt.result.load_sequence_warnings = detect_operational_warnings(alt.result.placed, container, request.loading_anchor)
        alt.result.reserved_zones = zones_out

    _current_state["result"] = best
    _current_state["container"] = container
    _current_state["reserved_zones"] = zones
    _current_state["clearance"] = request.clearance_mm
    _current_state["optimization_mode"] = request.optimization_mode
    _current_state["weight_balance_mode"] = request.weight_balance_mode
    _current_state["loading_anchor"] = request.loading_anchor
    _current_state["history"].reset()

    return OptimizeResponse(best=best, alternatives=alternatives)


@router.post("/optimize-remaining", response_model=OptimizeResponse)
def optimize_remaining(req: OptimizeRemainingRequest = OptimizeRemainingRequest()):
    """Reoptimiza todo lo que NO esta Locked; las piezas bloqueadas quedan
    exactamente donde estan (posicion y orientacion intactas).

    `req.optimization_mode`/`req.weight_balance_mode` son opcionales: si se
    mandan, reemplazan lo guardado en `_current_state` ANTES de reoptimizar
    (asi Keep Groups/Keep Systems/Weight Balance elegidos en la UI realmente
    se respetan aunque no se haya vuelto a correr /api/pack). Si se omiten,
    se reusa lo ultimo guardado, igual que antes."""
    state = _get_active_state()
    container = _current_state["container"]

    if req.optimization_mode is not None:
        _current_state["optimization_mode"] = req.optimization_mode
    if req.weight_balance_mode is not None:
        _current_state["weight_balance_mode"] = req.weight_balance_mode
    if req.loading_anchor is not None:
        _current_state["loading_anchor"] = req.loading_anchor

    locked = [p for p in state.placed if p.locked]
    unlocked = [p for p in state.placed if not p.locked]

    locked_boxes = [
        Box(p.id, p.x, p.y, p.z, p.dx, p.dy, p.dz, p.stackable, p.max_stack_weight) for p in locked
    ]
    for i, a in enumerate(locked_boxes):
        if not within_container(a, container.length, container.width, container.height):
            raise HTTPException(409, f"La pieza bloqueada {a.id} esta fuera de los limites del contenedor")
        for b in locked_boxes[i + 1 :]:
            if boxes_overlap(a, b):
                raise HTTPException(409, f"Las piezas bloqueadas {a.id} y {b.id} colisionan entre si")

    locked_weight = sum(p.weight for p in locked)
    if locked_weight > container.max_weight + TOL:
        raise HTTPException(409, "Las piezas bloqueadas ya exceden el peso maximo del contenedor")

    remaining_items = [_window_item_from_placed(p) for p in unlocked] + [
        _window_item_from_unloaded(u) for u in state.unloaded
    ]

    best, alternatives = run_optimization(
        remaining_items,
        container,
        _current_state["optimization_mode"],
        _reserved_zones(),
        _clearance(),
        _current_state["weight_balance_mode"],
        preplaced=locked,
    )

    zones_out = _reserved_zones_out(_reserved_zones())
    for alt in alternatives:
        alt.result.load_sequence = compute_load_sequence(alt.result.placed, container, _current_state["loading_anchor"])
        alt.result.unload_sequence = compute_unload_sequence(alt.result.placed)
        alt.result.load_sequence_warnings = detect_operational_warnings(
            alt.result.placed, container, _current_state["loading_anchor"]
        )
        alt.result.reserved_zones = zones_out

    _current_state["history"].push(state.placed, state.unloaded)
    _current_state["result"] = best

    return OptimizeResponse(best=best, alternatives=alternatives)


@router.get("/export-excel")
def export_excel():
    state = _get_active_state()
    workbook_bytes = build_export_workbook(state)
    return Response(
        content=workbook_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="cubox-cubicaje.xlsx"'},
    )


def _compute_report_steps(
    state: PackingResult, direction: ReportDirection, step_mode: StepMode, pieces_per_step: int | None
) -> list[list[str]]:
    """Compartido entre /report/steps y los 2 endpoints de guia PDF -misma
    logica Automatic (dependencias + waves) / Manual (chunking fijo) en un
    solo lugar, sea cual sea el consumidor."""
    if step_mode == StepMode.MANUAL:
        if not pieces_per_step or pieces_per_step <= 0:
            raise HTTPException(400, "pieces_per_step es requerido y debe ser mayor a 0 en modo Manual")
        base_sequence = state.load_sequence if direction == ReportDirection.LOAD else state.unload_sequence
        return chunk_sequence(base_sequence, pieces_per_step)
    if direction == ReportDirection.LOAD:
        return compute_load_steps(state.placed, _current_state["container"], _current_state["loading_anchor"])
    return compute_unload_steps(state.placed)


@router.post("/report/steps", response_model=ReportStepsResponse)
def report_steps(req: ReportStepsRequest):
    """Calcula los pasos de carga/descarga -Automatic (dependencias + waves)
    o Manual (chunking fijo)- para que el frontend sepa cuantos snapshots
    tomar antes de generar una Loading/Unloading Guide."""
    state = _get_active_state()
    steps = _compute_report_steps(state, req.direction, req.step_mode, req.pieces_per_step)
    return ReportStepsResponse(steps=steps)


@router.post("/report/validate", response_model=ReportValidationResponse)
def report_validate():
    """Validacion final antes de exportar (seccion 16): re-corre todos los
    chequeos existentes (colision, orientacion, soporte, peso, clearance,
    zonas reservadas, ids duplicados) sobre el estado activo."""
    state = _get_active_state()
    errors = validate_for_export(state, _current_state["container"], _reserved_zones(), _clearance())
    return ReportValidationResponse(valid=len(errors) == 0, errors=errors)


def _ensure_exportable(state: PackingResult) -> None:
    """Defensa en profundidad: aunque el frontend ya haya llamado a
    /report/validate antes de capturar snapshots, cada endpoint de PDF
    revalida el estado el sabe -nunca se genera un PDF de un cubicaje
    invalido, aunque alguien llame a la API directamente."""
    errors = validate_for_export(state, _current_state["container"], _reserved_zones(), _clearance())
    if errors:
        raise HTTPException(422, {"errors": errors})


@router.post("/report/container-pdf")
def export_container_report_pdf(req: ContainerReportRequest):
    state = _get_active_state()
    _ensure_exportable(state)
    if req.include_overview_image and not req.overview_image_png_base64:
        raise HTTPException(400, "Falta la imagen de overview (overview_image_png_base64)")
    pdf_bytes = build_container_report_pdf(state, req)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="cubox-container-report.pdf"'},
    )


def _guide_pdf(direction: ReportDirection, req: GuideReportRequest, builder, filename: str) -> Response:
    state = _get_active_state()
    _ensure_exportable(state)
    steps = _compute_report_steps(state, direction, req.step_mode, req.pieces_per_step)
    if len(req.step_images_png_base64) < len(steps):
        raise HTTPException(
            400,
            f"Faltan imagenes: se esperaban {len(steps)} snapshots (uno por paso) y llegaron {len(req.step_images_png_base64)}",
        )
    pdf_bytes = builder(state, steps, req.step_images_png_base64, req.meta)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/report/loading-guide-pdf")
def export_loading_guide_pdf(req: GuideReportRequest):
    return _guide_pdf(ReportDirection.LOAD, req, build_loading_guide_pdf, "cubox-loading-guide.pdf")


@router.post("/report/unloading-guide-pdf")
def export_unloading_guide_pdf(req: GuideReportRequest):
    return _guide_pdf(ReportDirection.UNLOAD, req, build_unloading_guide_pdf, "cubox-unloading-guide.pdf")


@router.post("/validate-move", response_model=MoveValidationResult)
def validate_move_endpoint(move: MoveRequest):
    state = _get_active_state()
    piece = _find_placed(state, move.piece_id)

    valid, reason = validate_move(
        piece,
        move.x,
        move.y,
        move.z,
        move.dx,
        move.dy,
        move.dz,
        state.placed,
        _current_state["container"],
        _reserved_zones(),
        _clearance(),
    )
    return MoveValidationResult(valid=valid, reason=reason)


@router.post("/apply-move", response_model=PackingResult)
def apply_move(move: MoveRequest):
    state = _get_active_state()
    piece = _find_placed(state, move.piece_id)
    _ensure_unlocked(piece)

    valid, reason = validate_move(
        piece,
        move.x,
        move.y,
        move.z,
        move.dx,
        move.dy,
        move.dz,
        state.placed,
        _current_state["container"],
        _reserved_zones(),
        _clearance(),
    )
    if not valid:
        raise HTTPException(409, reason)

    _current_state["history"].push(state.placed, state.unloaded)
    piece.x, piece.y, piece.z = move.x, move.y, move.z
    piece.dx, piece.dy, piece.dz = move.dx, move.dy, move.dz
    _refresh_derived(state)

    return state


@router.post("/remove-piece", response_model=PackingResult)
def remove_piece(req: RemovePieceRequest):
    state = _get_active_state()
    piece = _find_placed(state, req.piece_id)
    _ensure_unlocked(piece)

    _current_state["history"].push(state.placed, state.unloaded)
    state.placed = [p for p in state.placed if p.id != piece.id]
    state.unloaded.append(
        UnloadedItem(
            id=piece.id,
            code=piece.code,
            description=piece.description,
            width=piece.source_width,
            height=piece.source_height,
            thickness=piece.source_thickness,
            weight=piece.weight,
            system=piece.system,
            group=piece.group,
            stackable=piece.stackable,
            priority=piece.priority,
            max_stack_weight=piece.max_stack_weight,
            delivery_sequence=piece.delivery_sequence,
            reason="Removido manualmente",
            reason_code=UnloadedReason.MANUAL_REMOVE.value,
            item_type=piece.item_type,
            orientation_policy=piece.orientation_policy,
        )
    )
    _refresh_derived(state)

    return state


@router.post("/insert-piece", response_model=PackingResult)
def insert_piece(req: InsertPieceRequest):
    state = _get_active_state()

    item = next((u for u in state.unloaded if u.id == req.unloaded_id), None)
    if item is None:
        raise HTTPException(404, f"Pieza {req.unloaded_id} no encontrada en Unloaded Items")

    valid, reason = validate_placement(
        item.id,
        item.width,
        item.height,
        item.thickness,
        item.stackable,
        item.weight,
        item.max_stack_weight,
        req.x,
        req.y,
        req.z,
        req.dx,
        req.dy,
        req.dz,
        state.placed,
        _current_state["container"],
        _reserved_zones(),
        _clearance(),
        item.resolved_orientation_policy,
    )
    if not valid:
        raise HTTPException(409, reason)

    matching = next(
        (
            o
            for o in get_valid_orientations(item.width, item.height, item.thickness, item.resolved_orientation_policy)
            if o.dx == req.dx and o.dy == req.dy and o.dz == req.dz
        ),
        None,
    )
    orientation_label = matching.label if matching else ""

    _current_state["history"].push(state.placed, state.unloaded)
    state.unloaded = [u for u in state.unloaded if u.id != item.id]
    state.placed.append(
        PlacedPiece(
            id=item.id,
            code=item.code,
            description=item.description,
            system=item.system,
            group=item.group,
            weight=item.weight,
            stackable=item.stackable,
            priority=item.priority,
            max_stack_weight=item.max_stack_weight,
            delivery_sequence=item.delivery_sequence,
            x=req.x,
            y=req.y,
            z=req.z,
            dx=req.dx,
            dy=req.dy,
            dz=req.dz,
            orientation_label=orientation_label,
            source_width=item.width,
            source_height=item.height,
            source_thickness=item.thickness,
            item_type=item.item_type,
            orientation_policy=item.orientation_policy,
        )
    )
    _refresh_derived(state)

    return state


def _change_orientation(req: RotatePieceRequest, orientation_fn, action: str) -> PackingResult:
    state = _get_active_state()
    piece = _find_placed(state, req.piece_id)
    _ensure_unlocked(piece)

    policy = piece.resolved_orientation_policy
    target = orientation_fn(
        piece.source_width, piece.source_height, piece.source_thickness, piece.dx, piece.dy, piece.dz, policy
    )
    if target is None:
        raise HTTPException(409, f"Orientacion actual no reconocida, no se puede {action}")

    valid, reason = validate_placement(
        piece.id,
        piece.source_width,
        piece.source_height,
        piece.source_thickness,
        piece.stackable,
        piece.weight,
        piece.max_stack_weight,
        piece.x,
        piece.y,
        piece.z,
        target.dx,
        target.dy,
        target.dz,
        state.placed,
        _current_state["container"],
        _reserved_zones(),
        _clearance(),
        policy,
    )
    if not valid:
        raise HTTPException(409, reason)

    _current_state["history"].push(state.placed, state.unloaded)
    piece.dx, piece.dy, piece.dz = target.dx, target.dy, target.dz
    piece.orientation_label = target.label
    _refresh_derived(state)

    return state


@router.post("/rotate-piece", response_model=PackingResult)
def rotate_piece(req: RotatePieceRequest):
    return _change_orientation(req, toggle_orientation, "rotar")


@router.post("/turn-piece", response_model=PackingResult)
def turn_piece(req: RotatePieceRequest):
    return _change_orientation(req, turn_orientation, "girar")


@router.post("/lock-piece", response_model=PackingResult)
def lock_piece(req: LockPieceRequest):
    state = _get_active_state()
    piece = _find_placed(state, req.piece_id)
    _current_state["history"].push(state.placed, state.unloaded)
    piece.locked = True
    _refresh_derived(state)
    return state


@router.post("/unlock-piece", response_model=PackingResult)
def unlock_piece(req: UnlockPieceRequest):
    state = _get_active_state()
    piece = _find_placed(state, req.piece_id)
    _current_state["history"].push(state.placed, state.unloaded)
    piece.locked = False
    _refresh_derived(state)
    return state


@router.post("/undo", response_model=PackingResult)
def undo():
    state = _get_active_state()
    snapshot = _current_state["history"].undo(state.placed, state.unloaded)
    if snapshot is None:
        raise HTTPException(400, "No hay acciones para deshacer")

    state.placed, state.unloaded = snapshot
    _refresh_derived(state)
    return state


@router.post("/redo", response_model=PackingResult)
def redo():
    state = _get_active_state()
    snapshot = _current_state["history"].redo(state.placed, state.unloaded)
    if snapshot is None:
        raise HTTPException(400, "No hay acciones para rehacer")

    state.placed, state.unloaded = snapshot
    _refresh_derived(state)
    return state
