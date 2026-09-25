"""Endpoints de la API. MVP local: sin auth, sin base de datos.

El resultado de cubicaje mas reciente se guarda en memoria de proceso
(`_current_state`) para poder validar y aplicar ediciones manuales (mover,
rotar, quitar, insertar, lock) sin necesidad de una base de datos.
`_current_state` tambien guarda un historial de undo/redo (`core/history.py`)
y la configuracion activa (pasillo/clearance/optimization mode/weight
balance) para que la edicion manual y Optimize Remaining sigan respetando lo
que el usuario eligio en el ultimo Optimize.
"""

import io
import zipfile
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile

from app.core.excel_export import build_export_workbook
from app.core.excel_import import parse_excel
from app.core.final_validation import build_plan_validation_result
from app.core.geometry import Box, boxes_overlap, within_container
from app.core.history import EditHistory
from app.core.import_items import build_import_preview
from app.core.import_templates import build_import_template
from app.core.manual_move import validate_move, validate_placement, validate_tilt_change
from app.core.optimize import run_optimization
from app.core.orientation import get_valid_orientations, toggle_orientation, turn_orientation
from app.core.packer import compute_metrics
from app.core.pdf_export import (
    available_unload_groups,
    build_container_report_pdf,
    build_loading_guide_pdf,
    build_unloading_guide_pdf,
    build_unloading_guide_pdf_for_group,
)
from app.core.plan_service import build_plan_snapshot, default_plan_name, new_plan_id, parse_plan_state, utc_now_iso
from app.core.plan_store import (
    SCHEMA_VERSION,
    CorruptPlanStateError,
    PlanNotFoundError,
    PlanRepository,
    PlanRow,
    UnsupportedSchemaVersionError,
)
from app.core.reserved_zones import ReservedZone, central_aisle_zone
from app.core.road_weight import evaluate_road_weight, weight_point_from_placed
from app.core.sequence import (
    annotate_unload_steps_with_delivery,
    chunk_sequence,
    compute_load_sequence,
    compute_load_steps,
    compute_operational_warnings,
    compute_unload_dependencies,
    compute_unload_sequence,
    compute_unload_steps,
    group_unload_steps_into_delivery_sections,
)
from app.models.containers import build_custom_load_space, get_container, list_containers, list_load_spaces
from app.models.import_schemas import ImportDefaults, ImportPreview
from app.models.schemas import (
    ContainerReportRequest,
    ContainerSpec,
    CreatePlanRequest,
    CreatePlanResponse,
    CustomLoadSpaceRequest,
    DeletePlanResponse,
    InsertPieceRequest,
    ItemType,
    LoadingAnchor,
    LockPieceRequest,
    LoadSpaceSpec,
    MoveRequest,
    MoveValidationResult,
    OptimizationMode,
    OptimizeRemainingRequest,
    OptimizeResponse,
    OrientationPolicy,
    PackRequest,
    PackingResult,
    PlacedPiece,
    PlanDetailResponse,
    PlanHandlingRules,
    PlanSummary,
    PlanValidationStatus,
    GuideReportRequest,
    RemovePieceRequest,
    RenamePlanRequest,
    SavePlanRequest,
    ReportDirection,
    ReportStepsRequest,
    ReportStepsResponse,
    ReportValidationResponse,
    ReservedZoneOut,
    RotatePieceRequest,
    SetTiltRequest,
    StepMode,
    UnlockPieceRequest,
    WeightBalanceMode,
    WindowItem,
)
from app.services.piece_mapping import placed_to_item, placed_to_unloaded, unloaded_to_item, unloaded_to_placed
from app.services.plan_state import reserved_zones_to_state

router = APIRouter(prefix="/api")

TOL = 1e-6

_current_state: dict = {
    "result": None,
    "load_space": None,
    "history": EditHistory(),
    "reserved_zones": [],
    "clearance": 0.0,
    "optimization_mode": OptimizationMode.BEST_SPACE,
    "weight_balance_mode": WeightBalanceMode.NORMAL,
    "loading_anchor": LoadingAnchor.BACK_RIGHT,
    "plan_handling_rules": None,
    "plan_id": None,
}
"""Fase 5D: `plan_id` (nuevo) es un simple PUNTERO -que fila persistente
(ver core/plan_store.py) esta reflejando ahora mismo esta sesion en
memoria, si alguna. None = sesion sin persistir (p.ej. Open Legacy
Workspace, seccion 12 del pedido: fuera de alcance de esta fase). Nunca es
la fuente de verdad -eso sigue siendo la fila en SQLite; PUT
/api/plans/{id} lo usa solo para rechazar un autosave que apunte al plan
equivocado (seccion 35: aislamiento entre planes)."""

_plan_repository = PlanRepository()
"""Instancia unica de proceso -se crea (y crea el archivo/tabla SQLite si
hace falta) apenas se importa este modulo, es decir, al arrancar el backend
(seccion 5 del pedido). Los tests la reemplazan vía
`app.dependency_overrides[get_plan_repository]` (seccion 50: nunca contra
la base de datos real del usuario)."""


def get_plan_repository() -> PlanRepository:
    return _plan_repository


def _get_active_state() -> PackingResult:
    state: PackingResult | None = _current_state["result"]
    if state is None:
        raise HTTPException(400, "No hay un cubicaje activo. Ejecuta /api/pack primero.")
    return state


def _reserved_zones() -> list[ReservedZone]:
    return _current_state["reserved_zones"]


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


def _road_weight_for(load_space, placed: list[PlacedPiece]):
    """Metricas de distribucion de peso longitudinal (Fase 2B) -None si el
    LoadSpace no tiene RoadWeightConfig habilitado (Container/Truck/Trailer
    legacy, comportamiento Fase 2A intacto)."""
    return evaluate_road_weight(load_space.road_weight_config, (weight_point_from_placed(p) for p in placed))


def _apply_sequence_fields(result: PackingResult, container, anchor: LoadingAnchor) -> None:
    """Fase 6A: unico lugar que llena load_sequence/unload_sequence/
    operational_warnings/blocked_by -TODOS derivados, nunca persistidos (ver
    core/plan_service.py). load_sequence_warnings se mantiene como la
    proyeccion en texto plano de operational_warnings por compatibilidad con
    Excel y con cualquier consumidor que todavia lea el campo viejo."""
    result.load_sequence = compute_load_sequence(result.placed, container, anchor)
    result.unload_sequence = compute_unload_sequence(result.placed)
    result.operational_warnings = compute_operational_warnings(result.placed, container, anchor)
    result.load_sequence_warnings = [w.message for w in result.operational_warnings]
    result.blocked_by = compute_unload_dependencies(result.placed)


def _refresh_derived(state: PackingResult) -> None:
    load_space = _current_state["load_space"]
    state.metrics = compute_metrics(load_space, state.placed, state.unloaded)
    _apply_sequence_fields(state, load_space, _current_state["loading_anchor"])
    state.road_weight = _road_weight_for(load_space, state.placed)


@router.get("/containers", response_model=list[ContainerSpec])
def get_containers():
    return list_containers()


@router.get("/load-spaces", response_model=list[LoadSpaceSpec])
def get_load_spaces():
    """Generalizacion de /api/containers (CUBOX 2.0): hoy devuelve el mismo
    catalogo -Truck/Trailer todavia no tienen presets (ver
    models/containers.py)."""
    return list_load_spaces()


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


@router.post("/import-items-excel", response_model=ImportPreview)
async def import_items_excel(
    file: UploadFile = File(...),
    profile: ItemType = Form(...),
    default_orientation_policy: OrientationPolicy | None = Form(None),
    default_stackable: bool | None = Form(None),
):
    """Import CUBOX 2.0 profile-aware (Fase 3B): BOX/PALLET/PANEL/CUSTOM.
    Devuelve un preview -parsea y valida la hoja completa (todos los errores
    juntos, no se detiene en la primera fila invalida) pero NO empaqueta ni
    toca el estado activo de cubicaje. No reemplaza /api/import-excel
    (legacy), que sigue igual.

    default_orientation_policy/default_stackable (Fase 5): defaults del
    PLAN (Handling Rules del wizard) -solo se aplican fila por fila cuando
    la celda de Excel viene vacia; un valor explicito de Excel siempre gana
    (ver core/import_items.py:_parse_row). Tilt (Fase 5C-FINAL) NO tiene
    equivalente aca -es PLAN-LEVEL ONLY, sin columna de Excel ni default de
    import; se configura unicamente via plan_handling_rules en /api/pack."""
    if not file.filename.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(400, "El archivo debe ser .xlsx")
    content = await file.read()
    defaults = ImportDefaults(orientation_policy=default_orientation_policy, stackable=default_stackable)
    return build_import_preview(content, profile, defaults)


@router.get("/import-template/{profile}")
def get_import_template(profile: ItemType):
    workbook_bytes = build_import_template(profile)
    return Response(
        content=workbook_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="cubox-import-template-{profile.value}.xlsx"'},
    )


def _resolve_load_space(request: PackRequest) -> LoadSpaceSpec:
    """custom_load_space (Truck/Trailer/Container/Custom sin catalogo) tiene
    prioridad; si no vino, se resuelve container_id contra el catalogo
    existente -comportamiento identico al de antes de que custom_load_space
    existiera."""
    if request.custom_load_space is not None:
        c = request.custom_load_space
        return build_custom_load_space(c.name, c.load_space_type, c.length, c.width, c.height, c.max_weight, c.road_weight_config)
    if request.container_id is not None:
        try:
            return get_container(request.container_id)
        except KeyError as e:
            raise HTTPException(404, str(e))
    raise HTTPException(400, "Se requiere container_id o custom_load_space")


@router.post("/pack", response_model=OptimizeResponse)
def pack(request: PackRequest):
    container = _resolve_load_space(request)

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
        plan_handling_rules=request.plan_handling_rules,
    )

    zones_out = reserved_zones_to_state(zones)
    _apply_sequence_fields(best, container, request.loading_anchor)
    best.road_weight = _road_weight_for(container, best.placed)
    for alt in alternatives:
        _apply_sequence_fields(alt.result, container, request.loading_anchor)
        alt.result.reserved_zones = zones_out
        alt.result.road_weight = _road_weight_for(container, alt.result.placed)

    _current_state["result"] = best
    _current_state["load_space"] = container
    _current_state["plan_handling_rules"] = request.plan_handling_rules
    _current_state["reserved_zones"] = zones
    _current_state["clearance"] = request.clearance_mm
    _current_state["optimization_mode"] = request.optimization_mode
    _current_state["weight_balance_mode"] = request.weight_balance_mode
    _current_state["loading_anchor"] = request.loading_anchor
    # Fase 6A.1 FIX (seccion 7 del pedido): NO tocar plan_id aca. Antes se
    # forzaba a None en CADA pack, asumiendo que /api/pack siempre significa
    # "sesion nueva, sin plan asociado" -pero el frontend tambien llama a
    # este mismo endpoint para Re-optimize/Optimize sobre un plan YA
    # persistido (App.tsx:runOptimize, rama `else` cuando planId ya esta
    # seteado: cambiar Optimization Mode y volver a optimizar un plan
    # abierto). setPlanId() en el frontend NUNCA se resetea a null durante
    # la vida de la sesion (solo lo asigna create_plan/reopen), asi que esa
    # rama es SIEMPRE "re-optimizar el plan activo", nunca un pack
    # desconectado. Resetear plan_id aca rompia esa asociacion server-side
    # -el siguiente autosave (dispara porque `result` cambio) terminaba
    # pisando "El plan activo no coincide con el plan que se intenta
    # guardar" (409, save_plan linea ~1057) y la UI mostraba "Save failed"
    # (repro real: POST /api/plans -> POST /api/pack -> PUT /api/plans/{id}
    # 409). create_plan() (POST /api/plans) sigue funcionando igual: llama a
    # esta misma funcion y DESPUES pisa _current_state["plan_id"] con el id
    # recien creado (routes.py:create_plan), sin depender de este reset.
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
    container = _current_state["load_space"]

    if req.optimization_mode is not None:
        _current_state["optimization_mode"] = req.optimization_mode
    if req.weight_balance_mode is not None:
        _current_state["weight_balance_mode"] = req.weight_balance_mode
    if req.loading_anchor is not None:
        _current_state["loading_anchor"] = req.loading_anchor
    if req.plan_handling_rules is not None:
        _current_state["plan_handling_rules"] = req.plan_handling_rules

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

    remaining_items = [placed_to_item(p) for p in unlocked] + [
        unloaded_to_item(u) for u in state.unloaded
    ]

    best, alternatives = run_optimization(
        remaining_items,
        container,
        _current_state["optimization_mode"],
        _reserved_zones(),
        _clearance(),
        _current_state["weight_balance_mode"],
        preplaced=locked,
        plan_handling_rules=_current_state["plan_handling_rules"],
    )

    zones_out = reserved_zones_to_state(_reserved_zones())
    _apply_sequence_fields(best, container, _current_state["loading_anchor"])
    best.road_weight = _road_weight_for(container, best.placed)
    for alt in alternatives:
        _apply_sequence_fields(alt.result, container, _current_state["loading_anchor"])
        alt.result.reserved_zones = zones_out
        alt.result.road_weight = _road_weight_for(container, alt.result.placed)

    _current_state["history"].push(state.placed, state.unloaded)
    _current_state["result"] = best

    return OptimizeResponse(best=best, alternatives=alternatives)


@router.get("/export-excel")
def export_excel(allow_export_with_errors: bool = False):
    # Fase 6C, seccion 13 del pedido: mismo gate de errores duros que ya
    # usan los PDF (_resolve_export_validation) -antes Excel exportaba un
    # plan con colisiones/piezas flotando/ciclos de secuencia sin ningun
    # chequeo (inconsistencia que encontro la auditoria). Warnings
    # operacionales y unloaded items NUNCA bloquean -mismo criterio que los
    # PDF, seccion 13/25 del pedido. `allow_export_with_errors` (query param,
    # GET no tiene body) es el mismo Configurable Export Validation Override
    # de Part B -ver _resolve_export_validation.
    state = _get_active_state()
    validation = _resolve_export_validation(state, allow_export_with_errors)
    workbook_bytes = build_export_workbook(state, _export_override(validation))
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
        return compute_load_steps(state.placed, _current_state["load_space"], _current_state["loading_anchor"])
    return compute_unload_steps(state.placed)


@router.post("/report/steps", response_model=ReportStepsResponse)
def report_steps(req: ReportStepsRequest):
    """Calcula los pasos de carga/descarga -Automatic (dependencias + waves)
    o Manual (chunking fijo)- para que el frontend sepa cuantos snapshots
    tomar antes de generar una Loading/Unloading Guide, y -Fase 6B- para que
    la Guia interactiva del Workspace consuma exactamente los mismos pasos.

    Fase 6B, seccion 12/13: para UNLOAD se agrega anotacion de Delivery
    Sequence por paso (`unload_step_info`) y agrupacion en secciones
    contiguas (`delivery_sections`) -NUNCA se reordena `steps`, ver
    core/sequence.py:annotate_unload_steps_with_delivery. None para LOAD
    (Delivery Sequence es un concepto de descarga, seccion 2 de Fase 6A)."""
    state = _get_active_state()
    steps = _compute_report_steps(state, req.direction, req.step_mode, req.pieces_per_step)
    if req.direction != ReportDirection.UNLOAD:
        return ReportStepsResponse(steps=steps)
    unload_step_info = annotate_unload_steps_with_delivery(steps, state.placed, state.operational_warnings)
    delivery_sections = group_unload_steps_into_delivery_sections(unload_step_info)
    return ReportStepsResponse(steps=steps, unload_step_info=unload_step_info, delivery_sections=delivery_sections)


@router.post("/report/validate", response_model=ReportValidationResponse)
def report_validate():
    """Validacion final antes de exportar (seccion 16): re-corre todos los
    chequeos existentes (colision, orientacion, soporte, peso, clearance,
    zonas reservadas, ids duplicados) sobre el estado activo.

    Fase 6A, seccion 35/36: `warnings` expone los conflictos operacionales NO
    bloqueantes (Delivery/Stacking Sequence, limitacion de Loading Opening
    Type) -nunca impiden exportar, pero no deben quedar ocultos antes de
    generar un reporte. Un SEQUENCE_CYCLE, en cambio, ya viene incluido en
    `errors` (ver core/final_validation.py): es fisicamente imposible, no una
    preferencia de negocio.

    Fase 6C, seccion 34/35 del pedido: mismo endpoint, ahora respaldado por
    build_plan_validation_result() -UNICA funcion de agregacion, consumida
    tanto aca (preflight de reportes) como por el panel Plan Validation del
    Workspace (mismo llamado, App.tsx solo lo dispara con mas frecuencia).
    `valid`/`errors`/`warnings` mantienen exactamente el mismo significado
    que antes -los campos nuevos (`status`/`categories`/etc.) son aditivos."""
    state = _get_active_state()
    return build_plan_validation_result(state, _current_state["load_space"], _reserved_zones(), _clearance())


def _resolve_export_validation(state: PackingResult, allow_export_with_errors: bool) -> ReportValidationResponse:
    """Configurable Export Validation Override (Part B): defensa en
    profundidad -aunque el frontend ya haya llamado a /report/validate antes
    de capturar snapshots, cada endpoint de export revalida el estado el
    sabe. UNICA fuente de "listo para exportar": build_plan_validation_result
    (Fase 6C) -nunca se reimplementa una regla aca. NOT_READY (errores
    bloqueantes) sigue devolviendo 422 por default; `allow_export_with_errors`
    (Settings: "Block exports when validation errors exist" = OFF, reenviado
    por request, NUNCA un bypass solo-frontend) permite continuar igual -el
    resultado se devuelve siempre para que el caller pueda marcar el
    documento/celda como generado bajo override (nunca como aprobado, ver
    pdf_export.py/excel_export.py)."""
    validation = build_plan_validation_result(state, _current_state["load_space"], _reserved_zones(), _clearance())
    if validation.status == PlanValidationStatus.NOT_READY and not allow_export_with_errors:
        raise HTTPException(422, {"errors": validation.errors, "status": validation.status.value})
    return validation


def _export_override(validation: ReportValidationResponse) -> ReportValidationResponse | None:
    """None para un export normal (READY/READY_WITH_WARNINGS, o NOT_READY
    nunca llega aca sin override porque _resolve_export_validation ya
    hubiera lanzado 422) -los builders de pdf_export.py/excel_export.py usan
    esto para decidir si marcan el documento como 'EXPORTED WITH VALIDATION
    ERRORS' (seccion 44: nunca mostrarlo en un export normal)."""
    return validation if validation.status == PlanValidationStatus.NOT_READY else None


def _plan_name_and_type(repo: PlanRepository) -> tuple[str | None, str | None]:
    """Best-effort: el nombre/Load Type del plan solo existen en la fila
    persistida (PlanRow), _current_state no los cachea (ver docstring del
    modulo) -None si la sesion activa no esta asociada a un plan guardado
    (ej. Open Legacy Workspace) o si el plan fue borrado entretanto. Nunca
    debe hacer fallar la generacion de un PDF: es un dato cosmetico de
    portada, no una condicion de validacion."""
    plan_id = _current_state.get("plan_id")
    if not plan_id:
        return None, None
    try:
        row = repo.get(plan_id)
    except PlanNotFoundError:
        return None, None
    return row.name, row.load_type.upper() if row.load_type else None


@router.post("/report/container-pdf")
def export_container_report_pdf(req: ContainerReportRequest, repo: PlanRepository = Depends(get_plan_repository)):
    state = _get_active_state()
    validation = _resolve_export_validation(state, req.allow_export_with_errors)
    if req.include_overview_image and not req.overview_image_png_base64:
        raise HTTPException(400, "Falta la imagen de overview (overview_image_png_base64)")
    plan_name, load_type_label = _plan_name_and_type(repo)
    pdf_bytes = build_container_report_pdf(state, req, plan_name, load_type_label, _export_override(validation))
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="cubox-container-report.pdf"'},
    )


def _guide_pdf(
    direction: ReportDirection, req: GuideReportRequest, builder, filename: str, repo: PlanRepository
) -> Response:
    state = _get_active_state()
    validation = _resolve_export_validation(state, req.allow_export_with_errors)
    steps = _compute_report_steps(state, direction, req.step_mode, req.pieces_per_step)
    if len(req.step_images_png_base64) < len(steps):
        raise HTTPException(
            400,
            f"Faltan imagenes: se esperaban {len(steps)} snapshots (uno por paso) y llegaron {len(req.step_images_png_base64)}",
        )
    plan_name, load_type_label = _plan_name_and_type(repo)
    # Fase 6B.3, seccion 6 del pedido: la seccion resumen de Operational
    # Warnings del Unloading Guide PDF necesita el modo de optimizacion
    # activo (para el mismo mensaje contextual ya aprobado en
    # SequencePanel.tsx) -vive en _current_state (sibling de PackingResult,
    # nunca dentro de PackingResult -ver optimization_mode en
    # PlanDetailResponse/PackRequest), asi que se pasa explicito aca en vez
    # de intentar leerlo desde `state`. build_loading_guide_pdf lo ignora.
    # Seccion 6 del pedido: la portada SIEMPRE muestra el status de Fase 6C
    # (READY/READY_WITH_WARNINGS/NOT_READY) -se pasa `validation` completo,
    # no solo _export_override(validation) (ese seguiria siendo None salvo
    # NOT_READY, y la portada nunca mostraria "READY"/"READY WITH WARNINGS").
    # pdf_export.py decide internamente cuando agregar ademas la caja de
    # override + footer (solo si validation.status == NOT_READY).
    pdf_bytes = builder(
        state, steps, req.step_images_png_base64, req.meta, _current_state["optimization_mode"],
        plan_name, load_type_label, validation,
    )
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/report/loading-guide-pdf")
def export_loading_guide_pdf(req: GuideReportRequest, repo: PlanRepository = Depends(get_plan_repository)):
    return _guide_pdf(ReportDirection.LOAD, req, build_loading_guide_pdf, "cubox-loading-guide.pdf", repo)


@router.post("/report/unloading-guide-pdf")
def export_unloading_guide_pdf(req: GuideReportRequest, repo: PlanRepository = Depends(get_plan_repository)):
    return _guide_pdf(ReportDirection.UNLOAD, req, build_unloading_guide_pdf, "cubox-unloading-guide.pdf", repo)


@router.get("/report/unload-groups", response_model=list[str])
def list_unload_groups():
    """Groups distintos del plan activo, para el checklist "Unloading Guide
    by Group" del frontend -mismo criterio que ColorLegend.tsx (Set + sort,
    ignora "")."""
    state = _get_active_state()
    return available_unload_groups(state.placed)


def _content_disposition(filename: str) -> str:
    """`filename*` (RFC 5987, UTF-8) para nombres de Group con acentos u
    otros caracteres no-ASCII -`filename` ASCII-only queda como fallback
    para clientes viejos que no lo entiendan."""
    ascii_fallback = filename.encode("ascii", "ignore").decode("ascii") or "cubox-download"
    return f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(filename)}"


@router.post("/report/unloading-guide-pdf-by-group")
def export_unloading_guide_pdf_by_group(req: GuideReportRequest, repo: PlanRepository = Depends(get_plan_repository)):
    """Load Organization Model Cleanup, seccion 9-14: Group Unloading Guide
    -un PDF INDEPENDIENTE por Group seleccionado (nunca fusiona varios
    Groups en un documento falso). 1 Group -> un PDF suelto, igual que el
    Full Guide. 2+ Groups -> un ZIP con un PDF por Group (primer uso de
    zipfile en este backend, confirmado -no hay precedente de multi-archivo
    que reutilizar). El Full Unloading Guide (/report/unloading-guide-pdf)
    queda sin tocar -este es un camino nuevo, no un reemplazo."""
    if not req.groups:
        raise HTTPException(400, "Debe seleccionar al menos un Group para generar la Group Unloading Guide")

    state = _get_active_state()
    validation = _resolve_export_validation(state, req.allow_export_with_errors)
    steps = _compute_report_steps(state, ReportDirection.UNLOAD, req.step_mode, req.pieces_per_step)
    if len(req.step_images_png_base64) < len(steps):
        raise HTTPException(
            400,
            f"Faltan imagenes: se esperaban {len(steps)} snapshots (uno por paso) y llegaron {len(req.step_images_png_base64)}",
        )

    available = set(available_unload_groups(state.placed))
    unknown = [g for g in req.groups if g not in available]
    if unknown:
        raise HTTPException(400, f"Group(s) no encontrados en el plan activo: {unknown}")

    plan_name, load_type_label = _plan_name_and_type(repo)
    pdfs = {
        group: build_unloading_guide_pdf_for_group(
            state, steps, req.step_images_png_base64, req.meta, _current_state["optimization_mode"], group,
            plan_name, load_type_label, validation,
        )
        for group in req.groups
    }

    if len(pdfs) == 1:
        (group, pdf_bytes), = pdfs.items()
        filename = f"Cubox - Unloading - {group}.pdf"
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": _content_disposition(filename)},
        )

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for group, pdf_bytes in pdfs.items():
            zf.writestr(f"Cubox - Unloading - {group}.pdf", pdf_bytes)
    return Response(
        content=zip_buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": _content_disposition("Cubox - Unloading Guides.zip")},
    )


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
        _current_state["load_space"],
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
        _current_state["load_space"],
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
    state.unloaded.append(placed_to_unloaded(piece))
    _refresh_derived(state)

    return state


@router.post("/insert-piece", response_model=PackingResult)
def insert_piece(req: InsertPieceRequest):
    state = _get_active_state()

    item = next((u for u in state.unloaded if u.id == req.unloaded_id), None)
    if item is None:
        raise HTTPException(404, f"Pieza {req.unloaded_id} no encontrada en Unloaded Items")

    matching = next(
        (
            o
            for o in get_valid_orientations(
                item.dimensions,
                item.resolved_orientation_policy,
                allow_tilt=item.allow_tilt,
                max_tilt_angle=item.max_tilt_angle or 0.0,
            )
            if o.dx == req.dx and o.dy == req.dy and o.dz == req.dz
        ),
        None,
    )
    orientation_label = matching.label if matching else ""
    if matching is not None:
        insert_tilt_angle = matching.tilt_angle
        insert_tilt_axis = matching.tilt_axis
        insert_base_dx = matching.base_dx if matching.base_dx is not None else matching.dx
        insert_base_dy = matching.base_dy if matching.base_dy is not None else matching.dy
        insert_base_dz = matching.base_dz if matching.base_dz is not None else matching.dz
    else:
        insert_tilt_angle, insert_tilt_axis = 0.0, None
        insert_base_dx = insert_base_dy = insert_base_dz = None

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
        _current_state["load_space"],
        _reserved_zones(),
        _clearance(),
        item.resolved_orientation_policy,
        allow_tilt=item.allow_tilt,
        max_tilt_angle=item.max_tilt_angle or 0.0,
        tilt_angle=insert_tilt_angle,
        item_type=item.item_type,
        tilt_axis=insert_tilt_axis,
        base_dx=insert_base_dx,
        base_dy=insert_base_dy,
        base_dz=insert_base_dz,
    )
    if not valid:
        raise HTTPException(409, reason)

    _current_state["history"].push(state.placed, state.unloaded)
    state.unloaded = [u for u in state.unloaded if u.id != item.id]
    state.placed.append(
        unloaded_to_placed(
            item,
            x=req.x,
            y=req.y,
            z=req.z,
            dx=req.dx,
            dy=req.dy,
            dz=req.dz,
            orientation_label=orientation_label,
            tilt_angle=insert_tilt_angle,
            tilt_axis=insert_tilt_axis,
            base_dx=insert_base_dx,
            base_dy=insert_base_dy,
            base_dz=insert_base_dz,
        )
    )
    _refresh_derived(state)

    return state


@router.post("/set-tilt", response_model=PackingResult)
def set_tilt(req: SetTiltRequest):
    """Fase 5C: cambiar el angulo de Tilt de una pieza ya colocada. Rechaza
    limpiamente (409) si la pieza no admite Tilt, el angulo excede el
    maximo efectivo, o la nueva geometria deja de ser valida (orientacion,
    limites, colision, soporte, clearance, road weight) -ver
    core/manual_move.py:validate_tilt_change, la unica fuente de verdad."""
    state = _get_active_state()
    piece = _find_placed(state, req.piece_id)
    _ensure_unlocked(piece)

    valid, reason, new_dims = validate_tilt_change(
        piece,
        req.tilt_angle,
        state.placed,
        _current_state["load_space"],
        _reserved_zones(),
        _clearance(),
    )
    if not valid:
        raise HTTPException(409, reason)

    _current_state["history"].push(state.placed, state.unloaded)
    piece.x, piece.y, piece.z, piece.dx, piece.dy, piece.dz = new_dims
    piece.tilt_angle = req.tilt_angle
    _refresh_derived(state)

    return state


def _change_orientation(req: RotatePieceRequest, orientation_fn, action: str) -> PackingResult:
    state = _get_active_state()
    piece = _find_placed(state, req.piece_id)
    _ensure_unlocked(piece)

    policy = piece.resolved_orientation_policy
    target = orientation_fn(piece.source_dimensions, piece.dx, piece.dy, piece.dz, policy)
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
        _current_state["load_space"],
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


# ---------------------------------------------------------------------------
# Fase 5D: Recent Plans & Persistence.
#
# `_current_state` (arriba) sigue siendo el estado de sesion EN MEMORIA -se
# resetea con /api/pack, no sobrevive un reinicio del proceso. Estos 5
# endpoints son el UNICO puente hacia el almacenamiento PERSISTENTE
# (SQLite, ver core/plan_store.py + core/plan_service.py):
#
#   POST   /api/plans          crear (corre /api/pack + persiste el resultado)
#   GET    /api/plans          listar Recent Plans (liviano, para el Home)
#   GET    /api/plans/{id}     abrir -reconstruye _current_state SIN reoptimizar
#   PUT    /api/plans/{id}     autosave -vuelca _current_state tal cual esta
#   PATCH  /api/plans/{id}     renombrar
#   DELETE /api/plans/{id}     borrar (requiere confirmacion en el frontend)
#
# Crear/abrir un plan REEMPLAZA _current_state por completo (seccion 35 del
# pedido: aislamiento entre planes, A -> B -> A nunca mezcla estado).
# ---------------------------------------------------------------------------


def _load_space_to_config(load_space: LoadSpaceSpec) -> tuple[str | None, CustomLoadSpaceRequest | None]:
    """Distingue catalogo vs custom por la MISMA convencion que ya usa
    models/containers.py:build_custom_load_space (id="custom-{...}") -el
    modelo no trae un flag booleano propio (seccion 18/19 del pedido)."""
    if load_space.id.startswith("custom-"):
        custom = CustomLoadSpaceRequest(
            name=load_space.name,
            load_space_type=load_space.load_space_type,
            length=load_space.length,
            width=load_space.width,
            height=load_space.height,
            max_weight=load_space.max_weight,
            road_weight_config=load_space.road_weight_config,
        )
        return None, custom
    return load_space.id, None


def _central_aisle_config(zones: list[ReservedZone]) -> tuple[bool, float]:
    """El pasillo central no se guarda como un booleano/ancho aparte -ya
    esta implicito en reserved_zones (la unica zona que hoy produce
    central_aisle_zone). Se deriva de vuelta para que el checkbox/input del
    frontend puedan mostrar el estado correcto al reabrir."""
    aisle = next((z for z in zones if z.label == "central_aisle"), None)
    if aisle is None:
        return False, 500.0
    return True, aisle.width


def _plan_summary(row) -> PlanSummary:
    return PlanSummary(
        plan_id=row.plan_id,
        name=row.name,
        load_type=row.load_type,
        load_space_name=row.load_space_name,
        total_items=row.total_items,
        loaded_items=row.loaded_items,
        unloaded_items=row.unloaded_items,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _snapshot_current_state():
    return build_plan_snapshot(
        result=_current_state["result"],
        load_space=_current_state["load_space"],
        plan_handling_rules=_current_state["plan_handling_rules"],
        clearance=_current_state["clearance"],
        optimization_mode=_current_state["optimization_mode"],
        weight_balance_mode=_current_state["weight_balance_mode"],
        loading_anchor=_current_state["loading_anchor"],
        reserved_zones=_current_state["reserved_zones"],
    )


@router.post("/plans", response_model=CreatePlanResponse)
def create_plan(request: CreatePlanRequest, repo: PlanRepository = Depends(get_plan_repository)):
    """Seccion 12 del pedido: un Load Plan se vuelve persistente cuando el
    usuario hace clic en Create Load Plan -no antes (sesiones de wizard
    incompletas nunca se persisten)."""
    optimize_response = pack(request)  # CreatePlanRequest ES-A PackRequest; corre la MISMA logica que /api/pack

    plan_id = new_plan_id()
    created_at = utc_now_iso()
    name = request.name or default_plan_name()
    snapshot = _snapshot_current_state()

    repo.create(
        PlanRow(
            plan_id=plan_id,
            name=name,
            created_at=created_at,
            updated_at=created_at,
            schema_version=SCHEMA_VERSION,
            load_type=snapshot.load_type,
            load_space_name=snapshot.load_space_name,
            total_items=snapshot.total_items,
            loaded_items=snapshot.loaded_items,
            unloaded_items=snapshot.unloaded_items,
            state_json=snapshot.state_json,
        )
    )
    _current_state["plan_id"] = plan_id

    return CreatePlanResponse(plan_id=plan_id, name=name, best=optimize_response.best, alternatives=optimize_response.alternatives)


@router.get("/plans", response_model=list[PlanSummary])
def list_recent_plans(limit: int = 10, repo: PlanRepository = Depends(get_plan_repository)):
    """Seccion 10/38 del pedido: ordenado por updated_at DESC, forma
    liviana -nunca placed/unloaded completos solo para pintar el Home."""
    return [_plan_summary(row) for row in repo.list_recent(limit)]


@router.get("/plans/{plan_id}", response_model=PlanDetailResponse)
def get_plan(plan_id: str, repo: PlanRepository = Depends(get_plan_repository)):
    """Seccion 13/14 del pedido: abrir un plan reconstruye _current_state
    COMPLETO desde la fila persistida -placed/unloaded/locked/overrides tal
    cual se guardaron, NUNCA se vuelve a correr el optimizador."""
    try:
        row = repo.get(plan_id)
    except PlanNotFoundError:
        raise HTTPException(404, f"Load Plan {plan_id} no encontrado")

    try:
        restored = parse_plan_state(row)
    except UnsupportedSchemaVersionError:
        raise HTTPException(409, "This Load Plan was created with an unsupported Cubox data version.")
    except CorruptPlanStateError as e:
        raise HTTPException(422, str(e))

    metrics = compute_metrics(restored.load_space, restored.placed, restored.unloaded)
    result = PackingResult(container=restored.load_space, placed=restored.placed, unloaded=restored.unloaded, metrics=metrics)
    result.reserved_zones = reserved_zones_to_state(restored.reserved_zones)

    # Reemplaza _current_state POR COMPLETO (seccion 35/36 del pedido: nunca
    # mezclar con lo que hubiera de un plan anterior) y arranca un historial
    # de undo/redo vacio -mismo criterio que un /api/pack fresco.
    _current_state["result"] = result
    _current_state["load_space"] = restored.load_space
    _current_state["plan_handling_rules"] = restored.plan_handling_rules
    _current_state["reserved_zones"] = restored.reserved_zones
    _current_state["clearance"] = restored.clearance
    _current_state["optimization_mode"] = restored.optimization_mode
    _current_state["weight_balance_mode"] = restored.weight_balance_mode
    _current_state["loading_anchor"] = restored.loading_anchor
    _current_state["plan_id"] = plan_id
    _current_state["history"] = EditHistory()

    _refresh_derived(result)  # metrics/sequences/warnings/road_weight SIEMPRE recalculados frescos, nunca desde cache

    container_id, custom_load_space = _load_space_to_config(restored.load_space)
    enable_central_aisle, aisle_width_mm = _central_aisle_config(restored.reserved_zones)

    return PlanDetailResponse(
        plan_id=row.plan_id,
        name=row.name,
        created_at=row.created_at,
        updated_at=row.updated_at,
        load_type=row.load_type,
        result=result,
        plan_handling_rules=restored.plan_handling_rules,
        optimization_mode=restored.optimization_mode,
        weight_balance_mode=restored.weight_balance_mode,
        loading_anchor=restored.loading_anchor,
        clearance_mm=restored.clearance,
        enable_central_aisle=enable_central_aisle,
        aisle_width_mm=aisle_width_mm,
        container_id=container_id,
        custom_load_space=custom_load_space,
    )


@router.put("/plans/{plan_id}", response_model=PlanSummary)
def save_plan(plan_id: str, req: SavePlanRequest | None = None, repo: PlanRepository = Depends(get_plan_repository)):
    """Autosave (seccion 24/25 del pedido). Dos disparadores distintos,
    misma escritura persistente:

      - sin body (el autosave "normal", dispara con cualquier cambio de
        `result` -ver App.tsx): vuelca _current_state tal cual esta.
      - con body (Fase 5D correccion final, seccion 1/2 del pedido): el
        autosave de Plan Handling Rules. `req.plan_handling_rules` se
        aplica a _current_state ANTES de armar el snapshot -asi la
        configuracion del plan se persiste aunque el usuario nunca vuelva
        a correr Optimize. Ninguno de los 2 casos toca placed/unloaded ni
        llama al optimizador -guardar configuracion nunca recalcula la
        colocacion (seccion 2 del pedido).

    Rechaza limpiamente si la sesion activa no es realmente este plan
    (evita que un cliente desincronizado pise el estado de otro plan,
    seccion 35)."""
    if _current_state.get("plan_id") != plan_id or _current_state["result"] is None:
        raise HTTPException(409, "El plan activo no coincide con el plan que se intenta guardar.")

    if req is not None:
        _current_state["plan_handling_rules"] = req.plan_handling_rules

    try:
        existing = repo.get(plan_id)
    except PlanNotFoundError:
        raise HTTPException(404, f"Load Plan {plan_id} no encontrado")

    snapshot = _snapshot_current_state()
    updated_at = utc_now_iso()
    try:
        repo.update_state(
            plan_id,
            updated_at=updated_at,
            schema_version=SCHEMA_VERSION,
            # Fase 5D correccion final, seccion 5/6 del pedido: load_type es
            # metadata del PLAN, fijada una sola vez al crearlo (ver
            # create_plan) -nunca se re-infiere de los items actuales en
            # cada autosave, para que un plan sin piezas colocadas (o con
            # todo Unloaded) no pierda su Load Type original.
            load_type=existing.load_type,
            load_space_name=snapshot.load_space_name,
            total_items=snapshot.total_items,
            loaded_items=snapshot.loaded_items,
            unloaded_items=snapshot.unloaded_items,
            state_json=snapshot.state_json,
        )
        row = repo.get(plan_id)
    except PlanNotFoundError:
        raise HTTPException(404, f"Load Plan {plan_id} no encontrado")

    return _plan_summary(row)


@router.patch("/plans/{plan_id}", response_model=PlanSummary)
def rename_plan(plan_id: str, req: RenamePlanRequest, repo: PlanRepository = Depends(get_plan_repository)):
    """Seccion 28 del pedido: renombrar actualiza updated_at (y por lo tanto
    el orden en Recent Plans) igual que cualquier otro cambio del plan."""
    try:
        repo.rename(plan_id, req.name, utc_now_iso())
        row = repo.get(plan_id)
    except PlanNotFoundError:
        raise HTTPException(404, f"Load Plan {plan_id} no encontrado")
    return _plan_summary(row)


@router.delete("/plans/{plan_id}", response_model=DeletePlanResponse)
def delete_plan(plan_id: str, repo: PlanRepository = Depends(get_plan_repository)):
    """Seccion 29/49 del pedido: borrado permanente -la confirmacion vive en
    el frontend (esta llamada ya asume que el usuario confirmo)."""
    try:
        repo.delete(plan_id)
    except PlanNotFoundError:
        raise HTTPException(404, f"Load Plan {plan_id} no encontrado")
    return DeletePlanResponse(deleted=True, plan_id=plan_id)
