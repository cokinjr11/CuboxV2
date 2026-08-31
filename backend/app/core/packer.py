"""
Algoritmo de cubicaje automatico.

Heuristica "corner point" (puntos candidatos de anclaje): simple, rapida y
suficientemente confiable para un MVP. No busca la solucion optima, busca una
solucion valida que respete todas las restricciones del negocio.

Reglas que SIEMPRE se respetan (ver core/orientation.py y core/geometry.py):
  - Solo las orientaciones validas segun la OrientationPolicy resuelta del item
    (por defecto, la regla de ventanas: nunca acostada en el vidrio).
  - Sin colisiones entre piezas.
  - Piezas dentro de los limites del contenedor.
  - Peso total <= peso maximo del contenedor.
  - Apilamiento solo si la pieza inferior es stackable y hay suficiente soporte (%).
  - Ninguna pieza invade una zona reservada (por ejemplo, el pasillo central).
  - Separacion minima (clearance) entre piezas del mismo nivel, si se configuro.
  - MaxStackWeight de la pieza soporte, si esta definido.

`strategy` (ver core/strategies.py) decide el ORDEN en que se intentan colocar
las piezas; el motor de colocacion en si es el mismo sin importar la
estrategia. `optimization_mode` es una preferencia de agrupamiento (no una
restriccion dura): se mezcla en el orden de varias estrategias.
"""

from dataclasses import dataclass

from app.core.geometry import Box, check_stack_weight, check_support, has_clearance_conflict, has_collision, within_container
from app.core.orientation import get_valid_orientations
from app.core.reasons import UnloadedReason
from app.core.reserved_zones import ReservedZone, zone_conflict_with_clearance
from app.core.strategies import build_sort_key
from app.models.schemas import (
    ContainerSpec,
    OptimizationMode,
    PackingMetrics,
    PackingResult,
    PlacedPiece,
    UnloadedItem,
    WindowItem,
)

TOL = 1e-6


@dataclass
class _Instance:
    """Una unidad individual de una WindowItem (quantity expandida)."""

    instance_id: str
    source: WindowItem


@dataclass
class _Candidate:
    x: float
    y: float
    z: float
    support_id: str | None = None


def _expand_instances(items: list[WindowItem], reserved_ids: set[str] | None = None) -> list[_Instance]:
    """Expande cada WindowItem.quantity en instancias individuales con id
    unico. El contador de sufijo es GLOBAL por code dentro de la lista (no se
    reinicia por cada WindowItem): si dos entradas separadas comparten code
    -por ejemplo, al reconstruir piezas sueltas de quantity=1 para Optimize
    Remaining- los ids no colisionan entre si. `reserved_ids` (por ejemplo,
    los ids originales de las piezas Locked que se pasan por separado via
    `preplaced`) se salta ademas para no colisionar con esos."""
    reserved_ids = reserved_ids or set()
    instances: list[_Instance] = []
    counters: dict[str, int] = {}
    for item in items:
        for _ in range(item.quantity):
            counters[item.code] = counters.get(item.code, 0) + 1
            instance_id = f"{item.code}-{counters[item.code]:03d}"
            while instance_id in reserved_ids:
                counters[item.code] += 1
                instance_id = f"{item.code}-{counters[item.code]:03d}"
            instances.append(_Instance(instance_id=instance_id, source=item))
    return instances


def _fits_in_container_at_all(item: WindowItem, container: ContainerSpec) -> bool:
    for o in get_valid_orientations(item.width, item.height, item.thickness, item.resolved_orientation_policy):
        if o.dx <= container.length + TOL and o.dy <= container.width + TOL and o.dz <= container.height + TOL:
            return True
    return False


def compute_metrics(
    container: ContainerSpec, placed: list[PlacedPiece], unloaded: list[UnloadedItem]
) -> PackingMetrics:
    """Recalcula las metricas a partir del estado actual de placed/unloaded.

    Se usa tanto al final del cubicaje automatico como despues de cada edicion
    manual (mover, rotar, quitar, insertar), sin necesidad de re-ejecutar el
    algoritmo de empaque.
    """
    container_volume = container.length * container.width * container.height
    container_floor_area = container.length * container.width
    total_weight = sum(p.weight for p in placed)
    used_volume = sum(p.dx * p.dy * p.dz for p in placed)
    # Las piezas al nivel del piso no se solapan entre si (colisiones lo
    # impiden), asi que esta suma es el area de piso ocupada exacta.
    used_floor_area = sum(p.dx * p.dy for p in placed if p.z <= TOL)
    total_pieces = len(placed) + len(unloaded)

    groups = {p.group for p in placed if p.group}
    systems = {p.system for p in placed if p.system}

    # Balance de peso izquierda/derecha (eje Y, el ancho) y frente/fondo (eje
    # X, el largo): un contenedor muy cargado de un solo lado tiende a
    # volcarse. 100% = perfectamente equilibrado, 0% = todo el peso de un
    # solo lado.
    left_weight = sum(p.weight for p in placed if p.y + p.dy / 2 < container.width / 2)
    right_weight = total_weight - left_weight
    weight_balance_pct = (
        round((1 - abs(left_weight - right_weight) / total_weight) * 100, 2) if total_weight > 0 else 100.0
    )

    front_weight = sum(p.weight for p in placed if p.x + p.dx / 2 < container.length / 2)
    back_weight = total_weight - front_weight

    def _pct(part: float) -> float:
        return round((part / total_weight) * 100, 2) if total_weight > 0 else 0.0

    # Centro de masa: centro geometrico de cada pieza ponderado por su peso.
    # Sin piezas, se usa el centro del contenedor como valor neutral.
    if total_weight > 0:
        com_x = sum(p.weight * (p.x + p.dx / 2) for p in placed) / total_weight
        com_y = sum(p.weight * (p.y + p.dy / 2) for p in placed) / total_weight
        com_z = sum(p.weight * (p.z + p.dz / 2) for p in placed) / total_weight
    else:
        com_x, com_y, com_z = container.length / 2, container.width / 2, container.height / 2

    return PackingMetrics(
        total_pieces=total_pieces,
        loaded_pieces=len(placed),
        unloaded_pieces=len(unloaded),
        used_volume_pct=round((used_volume / container_volume) * 100, 2) if container_volume else 0.0,
        total_weight=round(total_weight, 2),
        weight_utilization_pct=round((total_weight / container.max_weight) * 100, 2)
        if container.max_weight
        else 0.0,
        floor_utilization_pct=round((used_floor_area / container_floor_area) * 100, 2)
        if container_floor_area
        else 0.0,
        container_floor_area=round(container_floor_area, 2),
        used_floor_area=round(used_floor_area, 2),
        max_payload=container.max_weight,
        number_of_groups=len(groups),
        number_of_systems=len(systems),
        weight_balance_pct=weight_balance_pct,
        left_weight_kg=round(left_weight, 2),
        right_weight_kg=round(right_weight, 2),
        left_weight_pct=_pct(left_weight),
        right_weight_pct=_pct(right_weight),
        front_weight_kg=round(front_weight, 2),
        back_weight_kg=round(back_weight, 2),
        front_weight_pct=_pct(front_weight),
        back_weight_pct=_pct(back_weight),
        center_of_mass_x=round(com_x, 2),
        center_of_mass_y=round(com_y, 2),
        center_of_mass_z=round(com_z, 2),
    )


def _unloaded_item(inst: _Instance, reason_code: UnloadedReason, reason_text: str) -> UnloadedItem:
    w = inst.source
    return UnloadedItem(
        id=inst.instance_id,
        code=w.code,
        description=w.description,
        width=w.width,
        height=w.height,
        thickness=w.thickness,
        weight=w.weight,
        system=w.system,
        group=w.group,
        stackable=w.stackable,
        priority=w.priority,
        max_stack_weight=w.max_stack_weight,
        delivery_sequence=w.delivery_sequence,
        reason=reason_text,
        reason_code=reason_code.value,
        item_type=w.item_type,
        orientation_policy=w.orientation_policy,
    )


def pack_container(
    items: list[WindowItem],
    container: ContainerSpec,
    optimization_mode: OptimizationMode = OptimizationMode.BEST_SPACE,
    reserved_zones: list[ReservedZone] | None = None,
    clearance: float = 0.0,
    strategy: str = "highest_priority",
    preplaced: list[PlacedPiece] | None = None,
) -> PackingResult:
    """Si se pasa `preplaced` (por ejemplo, las piezas Locked de Optimize
    Remaining), esas piezas se agregan al resultado tal cual -posicion y
    orientacion intactas- y sus volumenes se tratan como espacio ya ocupado
    para colocar el resto. Sin `preplaced`, comportamiento identico a antes.
    """
    reserved_zones = reserved_zones or []
    reserved_ids = {p.id for p in preplaced or []}
    instances = _expand_instances(items, reserved_ids)
    instances.sort(key=build_sort_key(strategy, optimization_mode))

    placed_boxes: list[Box] = []
    placed_pieces: list[PlacedPiece] = []
    unloaded: list[UnloadedItem] = []
    weights_by_id: dict[str, float] = {}
    total_weight = 0.0

    candidates: list[_Candidate] = [_Candidate(0.0, 0.0, 0.0, None)]

    # Sin esto, la busqueda solo tiene UN punto de arranque (y=0) y todos los
    # demas candidatos se derivan de piezas ya colocadas (max_x/max_y) -si una
    # zona reservada (p.ej. el pasillo central) corta el contenedor en 2
    # franjas de Y, el lado que queda MAS ALLA de la zona nunca recibe ningun
    # candidato semilla y se queda vacio por completo (bug: "todo el cargo de
    # un solo lado"). Se siembra un punto de arranque extra justo despues de
    # cada zona para que el algoritmo pueda llenar ambos lados del pasillo.
    for zone in reserved_zones:
        far_y = zone.y + zone.width + clearance
        if far_y < container.width - TOL:
            candidates.append(_Candidate(0.0, far_y, 0.0, None))

    for p in preplaced or []:
        # La busqueda interna trabaja en el sistema "sin espejar" (crece desde
        # x=0); el espejo x -> length-x-dx es su propia inversa, asi que se
        # usa la misma formula para volver de la posicion real (p.x) a la
        # posicion interna de busqueda.
        internal_x = container.length - p.x - p.dx
        seed_box = Box(
            id=p.id,
            x=internal_x,
            y=p.y,
            z=p.z,
            dx=p.dx,
            dy=p.dy,
            dz=p.dz,
            stackable=p.stackable,
            max_stack_weight=p.max_stack_weight,
        )
        placed_boxes.append(seed_box)
        placed_pieces.append(p)
        weights_by_id[p.id] = p.weight
        total_weight += p.weight

        new_candidates = [
            _Candidate(seed_box.max_x + clearance, seed_box.y, seed_box.z, None),
            _Candidate(seed_box.x, seed_box.max_y + clearance, seed_box.z, None),
        ]
        if p.stackable:
            new_candidates.append(_Candidate(seed_box.x, seed_box.y, seed_box.top_z, seed_box.id))
        candidates.extend(new_candidates)

    for inst in instances:
        w = inst.source

        if not _fits_in_container_at_all(w, container):
            unloaded.append(
                _unloaded_item(
                    inst, UnloadedReason.ORIENTATION_CONFLICT, "No cabe en el contenedor en ninguna orientacion valida"
                )
            )
            continue

        if total_weight + w.weight > container.max_weight + TOL:
            unloaded.append(
                _unloaded_item(inst, UnloadedReason.MAX_WEIGHT_EXCEEDED, "Excede el peso maximo del contenedor")
            )
            continue

        placed_ok = False
        # Prioriza x (profundidad) sobre z e y: llena por completo una seccion
        # transversal (todo el ancho y alto disponibles en esa profundidad)
        # antes de avanzar a la siguiente. Combinado con el espejo de X al
        # construir cada PlacedPiece (ver mas abajo), esto hace que el
        # contenedor se llene solido desde el fondo hacia la puerta, en vez
        # de una sola fila a lo largo del contenedor.
        candidates.sort(key=lambda c: (c.x, c.z, c.y))

        for cand in candidates:
            if placed_ok:
                break
            for o in get_valid_orientations(w.width, w.height, w.thickness, w.resolved_orientation_policy):
                candidate_box = Box(
                    id=inst.instance_id,
                    x=cand.x,
                    y=cand.y,
                    z=cand.z,
                    dx=o.dx,
                    dy=o.dy,
                    dz=o.dz,
                    stackable=w.stackable,
                    max_stack_weight=w.max_stack_weight,
                )

                if not within_container(candidate_box, container.length, container.width, container.height):
                    continue
                if has_collision(candidate_box, placed_boxes) is not None:
                    continue
                if zone_conflict_with_clearance(candidate_box, reserved_zones, clearance) is not None:
                    continue
                if has_clearance_conflict(candidate_box, placed_boxes, clearance) is not None:
                    continue

                if cand.z > TOL:
                    support = next((b for b in placed_boxes if b.id == cand.support_id), None)
                    if support is None or not support.stackable:
                        continue
                    ok, _ = check_support(candidate_box, placed_boxes)
                    if not ok:
                        continue
                    ok, _ = check_stack_weight(candidate_box, w.weight, placed_boxes, weights_by_id)
                    if not ok:
                        continue

                placed_boxes.append(candidate_box)
                weights_by_id[candidate_box.id] = w.weight
                placed_pieces.append(
                    PlacedPiece(
                        id=inst.instance_id,
                        code=w.code,
                        description=w.description,
                        system=w.system,
                        group=w.group,
                        weight=w.weight,
                        stackable=w.stackable,
                        priority=w.priority,
                        max_stack_weight=w.max_stack_weight,
                        delivery_sequence=w.delivery_sequence,
                        # Espejo de X: la busqueda interna siempre construye desde x=0
                        # hacia afuera; reflejarlo hace que lo primero colocado (el
                        # fondo del contenedor, x=0 en la busqueda interna) termine
                        # junto a la pared del fondo (x=length) y lo ultimo quede
                        # cerca de la puerta (x=0), que es como se carga en la
                        # realidad: del fondo hacia la puerta.
                        x=container.length - candidate_box.x - candidate_box.dx,
                        y=candidate_box.y,
                        z=candidate_box.z,
                        dx=candidate_box.dx,
                        dy=candidate_box.dy,
                        dz=candidate_box.dz,
                        orientation_label=o.label,
                        source_width=w.width,
                        source_height=w.height,
                        source_thickness=w.thickness,
                        item_type=w.item_type,
                        orientation_policy=w.orientation_policy,
                    )
                )
                total_weight += w.weight

                # Con clearance > 0, un candidato pegado (gap=0) a esta pieza
                # siempre violaria la separacion minima; se adelanta el hueco
                # requerido para que el candidato generado ya sea valido.
                new_candidates = [
                    _Candidate(candidate_box.max_x + clearance, candidate_box.y, candidate_box.z, None),
                    _Candidate(candidate_box.x, candidate_box.max_y + clearance, candidate_box.z, None),
                ]
                if w.stackable:
                    new_candidates.append(
                        _Candidate(candidate_box.x, candidate_box.y, candidate_box.top_z, candidate_box.id)
                    )
                candidates.extend(new_candidates)

                placed_ok = True
                break

        if not placed_ok:
            unloaded.append(
                _unloaded_item(inst, UnloadedReason.NO_VALID_SPACE, "Sin espacio disponible en el contenedor")
            )

    metrics = compute_metrics(container, placed_pieces, unloaded)

    return PackingResult(container=container, placed=placed_pieces, unloaded=unloaded, metrics=metrics)
