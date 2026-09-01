"""Validacion final antes de exportar cualquier PDF (CUBOX V4, prioridad 16).

No reimplementa ninguna regla nueva -reutiliza exactamente los mismos checks
que ya usan el packer y la edicion manual (geometry.py/orientation.py/
reserved_zones.py/road_weight.py) y los corre todos juntos sobre el estado
activo, juntando una lista de errores legible. Si la lista viene vacia, el
estado es exportable.

Tilt: no se valida porque el concepto no existe todavia en este proyecto (fue
pospuesto explicitamente en V3 y no se construyo en V4 -ver el plan de V4).
"""

from app.core.geometry import Box, boxes_overlap, boxes_too_close, check_stack_weight, check_support, within_container
from app.core.orientation import is_valid_orientation, orientation_rejection_reason
from app.core.reserved_zones import ReservedZone, zone_conflict
from app.core.road_weight import evaluate_road_weight, weight_point_from_placed
from app.models.schemas import ContainerSpec, PackingResult

TOL = 1e-6


def validate_for_export(
    state: PackingResult,
    container: ContainerSpec,
    reserved_zones: list[ReservedZone] | None = None,
    clearance: float = 0.0,
) -> list[str]:
    errors: list[str] = []
    placed = state.placed
    reserved_zones = reserved_zones or []

    ids = [p.id for p in placed]
    if len(ids) != len(set(ids)):
        dupes = sorted({pid for pid in ids if ids.count(pid) > 1})
        errors.append(f"Ids de pieza duplicados: {', '.join(dupes)}")

    boxes = [Box(p.id, p.x, p.y, p.z, p.dx, p.dy, p.dz, p.stackable, p.max_stack_weight) for p in placed]
    weights_by_id = {p.id: p.weight for p in placed}

    for p, box in zip(placed, boxes):
        if not within_container(box, container.length, container.width, container.height):
            errors.append(f"{p.id} esta fuera de los limites del contenedor")

        policy = p.resolved_orientation_policy
        if not is_valid_orientation(p.source_dimensions, p.dx, p.dy, p.dz, policy):
            errors.append(f"{p.id}: {orientation_rejection_reason(policy)}")

        support_ok, support_reason = check_support(box, boxes)
        if not support_ok:
            errors.append(f"{p.id}: {support_reason}")

        weight_ok, weight_reason = check_stack_weight(box, p.weight, boxes, weights_by_id)
        if not weight_ok:
            errors.append(f"{p.id}: {weight_reason}")

        zone = zone_conflict(box, reserved_zones)
        if zone is not None:
            errors.append(f"{p.id} invade la zona reservada '{zone.label}'")

    for i, a in enumerate(boxes):
        for b in boxes[i + 1 :]:
            if boxes_overlap(a, b):
                errors.append(f"Colision entre {a.id} y {b.id}")
            elif clearance > TOL and boxes_too_close(a, b, clearance):
                errors.append(f"Clearance insuficiente entre {a.id} y {b.id}")

    total_weight = sum(p.weight for p in placed)
    if total_weight > container.max_weight + TOL:
        errors.append(f"Peso total ({total_weight} kg) excede el maximo del contenedor ({container.max_weight} kg)")

    # RoadWeightConfig (Fase 2B): max_weight NO reemplaza los limites por
    # support -ambos deben pasar (ver core/road_weight.py). None/disabled ->
    # sin efecto, ningun error se agrega (Container/Truck/Trailer legacy).
    road_weight = evaluate_road_weight(container.road_weight_config, (weight_point_from_placed(p) for p in placed))
    if road_weight is not None:
        errors.extend(road_weight.errors)

    return errors
