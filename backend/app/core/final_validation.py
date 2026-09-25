"""Validacion final antes de exportar cualquier PDF (CUBOX V4, prioridad 16).

No reimplementa ninguna regla nueva -reutiliza exactamente los mismos checks
que ya usan el packer y la edicion manual (geometry.py/orientation.py/
reserved_zones.py/road_weight.py) y los corre todos juntos sobre el estado
activo, juntando una lista de errores legible. Si la lista viene vacia, el
estado es exportable.

Tilt (Fase 5C-FINAL): se valida de forma independiente y explicita, no solo
confiando en que el packer/manual move ya lo hicieron bien:
  - rango SIGNED: abs(p.tilt_angle) <= p.max_tilt_angle (chequeo directo,
    seccion 6/7/31 del pedido);
  - `is_valid_orientation` (con allow_tilt/max_tilt_angle) sigue cubriendo
    que la envolvente dx/dy/dz corresponda a una orientacion valida bajo
    PANEL_EDGE_ONLY -incluyendo Tilt=False pero pieza inclinada (ninguna
    orientacion regenerada matchea esa terna);
  - colision PRECISA (OBB/SAT, no solo AABB) via tilt_collision.py -evita
    tanto falsos negativos (Box AABB que no reporta una penetracion real
    entre 2 piezas no inclinadas, imposible) como falsos positivos (2
    paneles inclinados que solo se tocan, ver seccion 20/23 del pedido).

Fase 5C FINAL SIMPLIFICATION: NO se exige soporte lateral (de otro panel ni
de una pared del Load Space) para que una pieza inclinada sea valida -esa
exigencia demostro ser mas restrictiva que el comportamiento de producto
pretendido y fue removida de todos los validadores (manual, automatico y
esta validacion final).

Fase 6A (seccion 36 del pedido): un SEQUENCE_CYCLE (dependencias de
descarga -soporte + bloqueo lateral- que se contradicen entre si) es una
imposibilidad FISICA, no una preferencia de negocio -se agrega aca como
ERROR bloqueante. Los demas warnings operacionales (Delivery/Stacking
Sequence conflicts, limitacion de Loading Opening Type) son preferencias, no
imposibilidades: quedan fuera de esta validacion y se exponen por separado
via PackingResult.operational_warnings / /report/validate (`warnings`,
seccion 35) -nunca bloquean el export."""

from app.core.geometry import Box, boxes_too_close, check_stack_weight, check_support, within_container
from app.core.orientation import is_valid_orientation, orientation_rejection_reason
from app.core.reserved_zones import ReservedZone, zone_conflict
from app.core.road_weight import evaluate_road_weight, weight_point_from_placed
from app.core.sequence import compute_unload_dependencies, detect_sequence_cycle
from app.core.tilt_collision import precise_collision
from app.models.schemas import (
    CategoryStatus,
    CategoryStatusValue,
    ContainerSpec,
    OperationalWarningType,
    PackingResult,
    PlanValidationStatus,
    ReportValidationResponse,
    ValidationCategory,
    ValidationIssue,
    ValidationSeverity,
)

TOL = 1e-6

# Fase 6C, seccion 11 del pedido: mapeo ESTATICO OperationalWarningType ->
# ValidationCategory -nunca se parsea el texto de w.message para adivinar la
# categoria (seccion 4/11: "Do NOT parse warning strings"). SEQUENCE_CYCLE
# esta deliberadamente AUSENTE de este mapa: ya se escala a ERROR bloqueante
# mas abajo (mismo criterio que /report/validate hoy), asi que
# build_plan_validation_result la excluye de operational_warnings para no
# mostrarla dos veces (seccion 11: "Avoid showing SEQUENCE_CYCLE
# simultaneously as both an error and warning").
_WARNING_CATEGORY_MAP: dict[OperationalWarningType, ValidationCategory] = {
    OperationalWarningType.DELIVERY_SEQUENCE_CONFLICT: ValidationCategory.DELIVERY_SEQUENCE,
    OperationalWarningType.STACKING_SEQUENCE_CONFLICT: ValidationCategory.DELIVERY_SEQUENCE,
    OperationalWarningType.OPENING_CONFIGURATION_LIMITATION: ValidationCategory.OPERATIONAL_SEQUENCE,
    OperationalWarningType.OPERATIONAL_LOADABILITY_WARNING: ValidationCategory.OPERATIONAL_SEQUENCE,
}

# Categorias que siempre aparecen en el panel Plan Validation (seccion 8/20
# del pedido) -DATA_INTEGRITY queda afuera a proposito: es un chequeo de
# defensa en profundidad (ids duplicados) que nunca deberia dispararse en un
# plan generado por Cubox: no aporta una fila util al usuario en el caso
# normal, solo se refleja en errors/error_issues si de verdad llega a pasar.
_DISPLAY_CATEGORIES: list[ValidationCategory] = [
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
]


def _box_from_placed(p) -> Box:
    return Box(
        id=p.id,
        x=p.x,
        y=p.y,
        z=p.z,
        dx=p.dx,
        dy=p.dy,
        dz=p.dz,
        stackable=p.stackable,
        max_stack_weight=p.max_stack_weight,
        tilt_angle=p.tilt_angle,
        tilt_axis=p.tilt_axis,
        base_dx=p.base_dx,
        base_dy=p.base_dy,
        base_dz=p.base_dz,
        item_type=p.item_type,
    )


def collect_validation_issues(
    state: PackingResult,
    container: ContainerSpec,
    reserved_zones: list[ReservedZone] | None = None,
    clearance: float = 0.0,
) -> list[ValidationIssue]:
    """Fase 6C, seccion 4/6 del pedido: MISMOS checks exactos que
    validate_for_export ya corria (ni una regla fisica nueva, ni un orden
    distinto) -la unica diferencia es que cada mensaje se envuelve en un
    ValidationIssue con su categoria/severidad/item_id ya conocidos en el
    momento en que se genera, en vez de perderse en un string suelto.
    validate_for_export() sigue siendo la proyeccion [i.message for i in
    issues] de esta misma lista (seccion 6: "keep validate_for_export as a
    backward-compatible projection")."""
    issues: list[ValidationIssue] = []
    placed = state.placed
    reserved_zones = reserved_zones or []

    def error(category: ValidationCategory, message: str, item_id: str | None = None) -> None:
        issues.append(ValidationIssue(category=category, severity=ValidationSeverity.ERROR, message=message, item_id=item_id))

    ids = [p.id for p in placed]
    if len(ids) != len(set(ids)):
        dupes = sorted({pid for pid in ids if ids.count(pid) > 1})
        error(ValidationCategory.DATA_INTEGRITY, f"Ids de pieza duplicados: {', '.join(dupes)}")

    boxes = [_box_from_placed(p) for p in placed]
    weights_by_id = {p.id: p.weight for p in placed}

    for p, box in zip(placed, boxes):
        if not within_container(box, container.length, container.width, container.height):
            error(ValidationCategory.LOAD_SPACE, f"{p.id} esta fuera de los limites del contenedor", p.id)

        policy = p.resolved_orientation_policy
        if not is_valid_orientation(
            p.source_dimensions, p.dx, p.dy, p.dz, policy, allow_tilt=p.allow_tilt, max_tilt_angle=p.max_tilt_angle or 0.0
        ):
            error(ValidationCategory.ORIENTATION, f"{p.id}: {orientation_rejection_reason(policy)}", p.id)

        if abs(p.tilt_angle) > (p.max_tilt_angle or 0.0) + TOL:
            error(
                ValidationCategory.ORIENTATION,
                f"{p.id}: Tilt ({p.tilt_angle:g}°) excede el maximo permitido (±{p.max_tilt_angle or 0.0:g}°)",
                p.id,
            )

        support_ok, support_reason = check_support(box, boxes)
        if not support_ok:
            error(ValidationCategory.SUPPORT_STABILITY, f"{p.id}: {support_reason}", p.id)

        weight_ok, weight_reason = check_stack_weight(box, p.weight, boxes, weights_by_id)
        if not weight_ok:
            error(ValidationCategory.STACK_WEIGHT, f"{p.id}: {weight_reason}", p.id)

        zone = zone_conflict(box, reserved_zones)
        if zone is not None:
            error(ValidationCategory.LOAD_SPACE, f"{p.id} invade la zona reservada '{zone.label}'", p.id)

    for i, a in enumerate(boxes):
        for b in boxes[i + 1 :]:
            if precise_collision(a, b):
                error(ValidationCategory.COLLISION_CLEARANCE, f"Colision entre {a.id} y {b.id}", a.id)
            elif clearance > TOL and boxes_too_close(a, b, clearance):
                error(ValidationCategory.COLLISION_CLEARANCE, f"Clearance insuficiente entre {a.id} y {b.id}", a.id)

    total_weight = sum(p.weight for p in placed)
    if total_weight > container.max_weight + TOL:
        error(
            ValidationCategory.PAYLOAD,
            f"Peso total ({total_weight} kg) excede el maximo del contenedor ({container.max_weight} kg)",
        )

    # RoadWeightConfig (Fase 2B): max_weight NO reemplaza los limites por
    # support -ambos deben pasar (ver core/road_weight.py). None/disabled ->
    # sin efecto, ningun error se agrega (Container/Truck/Trailer legacy).
    road_weight = evaluate_road_weight(container.road_weight_config, (weight_point_from_placed(p) for p in placed))
    if road_weight is not None:
        for message in road_weight.errors:
            error(ValidationCategory.ROAD_WEIGHT, message)

    cycle = detect_sequence_cycle(compute_unload_dependencies(placed))
    if cycle:
        error(
            ValidationCategory.OPERATIONAL_SEQUENCE,
            f"Operational sequence conflict: {', '.join(cycle)} create incompatible unloading dependencies.",
        )

    return issues


def validate_for_export(
    state: PackingResult,
    container: ContainerSpec,
    reserved_zones: list[ReservedZone] | None = None,
    clearance: float = 0.0,
) -> list[str]:
    """Proyeccion backward-compatible de collect_validation_issues() -mismo
    contrato de siempre (list[str], vacia = exportable) para _ensure_exportable
    y cualquier caller existente. No repetir logica aca: agregar un chequeo
    nuevo significa tocar collect_validation_issues(), nunca esta funcion."""
    return [issue.message for issue in collect_validation_issues(state, container, reserved_zones, clearance)]


def build_plan_validation_result(
    state: PackingResult,
    container: ContainerSpec,
    reserved_zones: list[ReservedZone] | None = None,
    clearance: float = 0.0,
) -> ReportValidationResponse:
    """Fase 6C, seccion 7/34 del pedido: UNICA funcion de agregacion -tanto
    /report/validate (preflight de reportes) como el panel Plan Validation
    del Workspace consumen este mismo resultado, nunca dos calculos
    independientes de "listo para exportar". No valida nada nuevo: junta
    collect_validation_issues() (errores duros) + state.operational_warnings
    (preferencias de negocio, ya calculadas por sequence.py) +
    state.unloaded (informativo, nunca fisicamente invalido, seccion 10)."""
    error_issues = collect_validation_issues(state, container, reserved_zones, clearance)

    warning_issues: list[ValidationIssue] = []
    for w in state.operational_warnings:
        if w.type == OperationalWarningType.SEQUENCE_CYCLE:
            continue  # ya esta en error_issues (OPERATIONAL_SEQUENCE) -nunca se duplica como warning
        category = _WARNING_CATEGORY_MAP.get(w.type, ValidationCategory.OPERATIONAL_SEQUENCE)
        warning_issues.append(
            ValidationIssue(category=category, severity=ValidationSeverity.WARNING, message=w.message, item_id=w.item_id)
        )

    unloaded_count = len(state.unloaded)
    if unloaded_count > 0:
        unit_word = "unit" if unloaded_count == 1 else "units"
        warning_issues.append(
            ValidationIssue(
                category=ValidationCategory.UNLOADED_ITEMS,
                severity=ValidationSeverity.WARNING,
                message=f"{unloaded_count} {unit_word} were not loaded.",
            )
        )

    error_count = len(error_issues)
    warning_count = len(warning_issues)
    if error_count > 0:
        status = PlanValidationStatus.NOT_READY
    elif warning_count > 0:
        status = PlanValidationStatus.READY_WITH_WARNINGS
    else:
        status = PlanValidationStatus.READY

    road_weight_active = container.road_weight_config is not None and container.road_weight_config.enabled
    categories: list[CategoryStatus] = []
    for category in _DISPLAY_CATEGORIES:
        if category == ValidationCategory.ROAD_WEIGHT and not road_weight_active:
            categories.append(CategoryStatus(category=category, status=CategoryStatusValue.NOT_APPLICABLE))
            continue
        category_errors = [i for i in error_issues if i.category == category]
        category_warnings = [i for i in warning_issues if i.category == category]
        if category_errors:
            categories.append(CategoryStatus(category=category, status=CategoryStatusValue.ERROR, count=len(category_errors)))
        elif category_warnings:
            categories.append(
                CategoryStatus(category=category, status=CategoryStatusValue.WARNING, count=len(category_warnings))
            )
        else:
            categories.append(CategoryStatus(category=category, status=CategoryStatusValue.PASS))

    # Fase 6C, seccion 33 del pedido: `warnings` (list[str]) mantiene EXACTO
    # el mismo contenido que /report/validate ya devolvia antes de esta fase
    # (solo operational_warnings, nunca el resumen de unloaded items) -asi
    # ningun caller viejo (handleGenerateReport, por ejemplo) ve cambiar su
    # forma. El resumen de unloaded items solo vive en warning_issues/
    # unloaded_count/categories, los campos nuevos.
    return ReportValidationResponse(
        valid=error_count == 0,
        errors=[i.message for i in error_issues],
        warnings=[i.message for i in warning_issues if i.category != ValidationCategory.UNLOADED_ITEMS],
        status=status,
        error_count=error_count,
        warning_count=warning_count,
        unloaded_count=unloaded_count,
        categories=categories,
        error_issues=error_issues,
        warning_issues=warning_issues,
    )
