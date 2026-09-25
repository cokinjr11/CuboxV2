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
  - Si container.road_weight_config esta habilitado (Fase 2B, ver
    core/road_weight.py): el candidato no puede dejar ningun support
    longitudinal sobrecargado ni con reaccion negativa. None/disabled -> sin
    efecto (comportamiento identico a Fase 2A).

`strategy` (ver core/strategies.py) decide el ORDEN en que se intentan colocar
las piezas; el motor de colocacion en si es el mismo sin importar la
estrategia. `optimization_mode` es una preferencia de agrupamiento (no una
restriccion dura): se mezcla en el orden de varias estrategias.

BACK_RIGHT_FLOOR anchor audit: el ancla canonica de carga -vista desde la
puerta hacia adentro: fondo (mas profundo posible), pared DERECHA, piso- es
el punto de arranque UNICO para TODO item_type cuando esta fisicamente
disponible (ver el seed inicial `_Candidate(0,0,0,from_right=True)` en
pack_container). No es una heuristica de optimizacion como la lateral de
Panels & Fragile -es el candidato semilla desde el que TODO first-fit
arranca; si esta exacta esquina esta bloqueada (zona reservada/pieza
Locked), el algoritmo cae naturalmente al candidato valido mas cercano en
la misma direccion de ancla, nunca falla el packing por eso. Convencion de
coordenadas completa (puerta/fondo/izquierda/derecha/piso) documentada en
core/sequence.py, que reutiliza EXACTAMENTE el mismo `is_at_back_right_floor`
para anclar la Loading Sequence -nunca dos definiciones distintas de "toca
la esquina".

Panels & Fragile (ItemType.PANEL), heuristica lateral (ver
_panel_lateral_cost/_evaluate_candidate/_commit_candidate): dentro de un
mismo modulo/fila transversal, se PREFIERE (nunca se exige, sigue siendo
una preferencia de optimizacion sobre candidatos YA validos, jamas una
restriccion dura nueva) que las piezas mas altas queden cerca de las
paredes laterales y las mas bajas cerca del centro -reduce el riesgo de que
un panel/ventana alto quede solo entre piezas mucho mas chicas cerca del
centro. BOX/PALLET/CUSTOM (y cualquier PANEL sin ningun candidato que pase
los mismos chequeos duros de siempre) siguen exactamente el first-fit de
siempre, sin cambio de comportamiento.
"""

from dataclasses import dataclass

from app.core.geometry import Box, check_stack_weight, check_support, has_clearance_conflict, within_container
from app.core.orientation import get_valid_orientations
from app.core.reasons import UnloadedReason
from app.core.tilt_collision import has_precise_collision
from app.core.road_weight import piece_center_x, would_exceed_support_limits
from app.core.reserved_zones import ReservedZone, zone_conflict_with_clearance
from app.core.strategies import build_sort_key
from app.models.schemas import (
    ContainerSpec,
    ItemType,
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
    # BACK_RIGHT_FLOOR anchor audit: si True, `y` NO es la posicion real -es
    # la distancia interna acumulada desde la pared DERECHA hacia el centro,
    # exactamente el mismo truco que ya usa `x` para crecer desde el fondo
    # hacia la puerta (espejo en _evaluate_candidate/_commit_candidate). Este
    # es el seed CANONICO -ver pack_container: TODO item_type lo usa como
    # punto de partida por defecto, para que la primera pieza de cualquier
    # plan quede en BACK_RIGHT_FLOOR cuando es fisicamente posible.
    # False (default aca, pero NUNCA el seed inicial salvo en planes con
    # Panels & Fragile -ver pack_container) para candidatos en el sistema de
    # coordenadas real de siempre -zonas reservadas, piezas preplaced/Locked,
    # y (solo para PANEL) el segundo seed bilateral anclado a la pared
    # IZQUIERDA que habilita _panel_lateral_cost a llenar ambas paredes.
    from_right: bool = False


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
    for o in get_valid_orientations(
        item.dimensions,
        item.resolved_orientation_policy,
        allow_tilt=item.allow_tilt,
        max_tilt_angle=item.max_tilt_angle or 0.0,
    ):
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


def _evaluate_candidate(
    cand: "_Candidate",
    o,
    inst: "_Instance",
    w: WindowItem,
    container: ContainerSpec,
    placed_boxes: list[Box],
    reserved_zones: list[ReservedZone],
    clearance: float,
    weights_by_id: dict[str, float],
    total_weight: float,
    total_moment: float,
    road_weight_config,
) -> tuple[Box, float, float] | None:
    """Chequeos duros (limites, colision, zona reservada, clearance,
    soporte/stack-weight, road-weight) para UN candidato (posicion +
    orientacion) -exactamente los mismos que siempre corria pack_container
    inline, ahora extraidos para poder reutilizarlos tanto en el camino
    first-fit de siempre (BOX/PALLET/CUSTOM/PANEL sin heuristica lateral
    aplicable) como en el camino de evaluacion MULTIPLE de Panels & Fragile
    (ver _panel_lateral_cost) sin duplicar -y por lo tanto sin poder
    desincronizar- ninguna regla fisica. None si cualquier chequeo falla;
    si no, devuelve (candidate_box, final_x, candidate_center_x) listos
    para _commit_candidate."""
    # Espejo de Y (Panels & Fragile, llenado bilateral): identico en
    # espiritu al espejo de X mas abajo -`cand.y` en un candidato
    # from_right es la distancia interna acumulada desde la pared derecha,
    # nunca la posicion real. Para cualquier otro candidato (from_right=
    # False, TODO lo que no sea PANEL-desde-la-derecha), `cand.y` ya es la
    # posicion real de siempre, sin cambios.
    real_y = container.width - cand.y - o.dy if cand.from_right else cand.y

    candidate_box = Box(
        id=inst.instance_id,
        x=cand.x,
        y=real_y,
        z=cand.z,
        dx=o.dx,
        dy=o.dy,
        dz=o.dz,
        stackable=w.stackable,
        max_stack_weight=w.max_stack_weight,
        tilt_angle=o.tilt_angle,
        tilt_axis=o.tilt_axis,
        base_dx=o.base_dx if o.base_dx is not None else o.dx,
        base_dy=o.base_dy if o.base_dy is not None else o.dy,
        base_dz=o.base_dz if o.base_dz is not None else o.dz,
        item_type=w.item_type,
    )

    if not within_container(candidate_box, container.length, container.width, container.height):
        return None
    if has_precise_collision(candidate_box, placed_boxes) is not None:
        return None
    if zone_conflict_with_clearance(candidate_box, reserved_zones, clearance) is not None:
        return None
    if has_clearance_conflict(candidate_box, placed_boxes, clearance) is not None:
        return None

    if cand.z > TOL:
        support = next((b for b in placed_boxes if b.id == cand.support_id), None)
        if support is None or not support.stackable:
            return None
        ok, _ = check_support(candidate_box, placed_boxes)
        if not ok:
            return None
        ok, _ = check_stack_weight(candidate_box, w.weight, placed_boxes, weights_by_id)
        if not ok:
            return None

    # Espejo de X: ver comentario original en pack_container -lo primero
    # colocado (x=0 en la busqueda interna) termina junto a la pared del
    # fondo, lo ultimo cerca de la puerta.
    final_x = container.length - candidate_box.x - candidate_box.dx
    candidate_center_x = piece_center_x(final_x, candidate_box.dx)
    prospective_weight = total_weight + w.weight
    prospective_moment = total_moment + w.weight * candidate_center_x
    if would_exceed_support_limits(prospective_weight, prospective_moment, road_weight_config):
        return None

    return candidate_box, final_x, candidate_center_x


def _commit_candidate(
    candidate_box: Box,
    final_x: float,
    candidate_center_x: float,
    cand: "_Candidate",
    o,
    inst: "_Instance",
    w: WindowItem,
    placed_boxes: list[Box],
    placed_pieces: list[PlacedPiece],
    weights_by_id: dict[str, float],
    candidates: list["_Candidate"],
    clearance: float,
    total_weight: float,
    total_moment: float,
) -> tuple[float, float]:
    """Efectos de colocar definitivamente `candidate_box` -exactamente los
    mismos pasos que siempre corria pack_container inline al encontrar el
    primer candidato valido (append a placed_boxes/placed_pieces, nuevos
    candidatos derivados, actualizar total_weight/total_moment). Devuelve
    el (total_weight, total_moment) actualizados -son escalares inmutables,
    no se puede mutarlos por referencia como a las listas."""
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
            boxes_inside=w.boxes_inside,
            x=final_x,
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
            stackable_override=w.stackable_override,
            orientation_override=w.orientation_override,
            allow_tilt=w.allow_tilt,
            max_tilt_angle=w.max_tilt_angle,
            tilt_angle=o.tilt_angle,
            tilt_axis=o.tilt_axis,
            base_dx=o.base_dx if o.base_dx is not None else o.dx,
            base_dy=o.base_dy if o.base_dy is not None else o.dy,
            base_dz=o.base_dz if o.base_dz is not None else o.dz,
        )
    )
    new_total_weight = total_weight + w.weight
    new_total_moment = total_moment + w.weight * candidate_center_x

    # Con clearance > 0, un candidato pegado (gap=0) a esta pieza siempre
    # violaria la separacion minima; se adelanta el hueco requerido para
    # que el candidato generado ya sea valido.
    #
    # El candidato "siguiente en Y" (crecer la fila hacia el centro) debe
    # seguir en el MISMO sistema de coordenadas que uso `cand` para llegar
    # aca: si `cand` venia de la pared derecha (from_right), el siguiente
    # tambien crece la distancia INTERNA acumulada desde esa pared (mismo
    # truco que `x`); si no, sigue siendo la posicion real de siempre
    # (candidate_box.max_y). El candidato "siguiente en X" (profundidad) y
    # el de apilar (Z) SIEMPRE usan la posicion REAL ya resuelta
    # (candidate_box.y) -no necesitan mas espejado, sea cual sea el origen.
    if cand.from_right:
        y_extend = _Candidate(candidate_box.x, cand.y + o.dy + clearance, candidate_box.z, None, from_right=True)
    else:
        y_extend = _Candidate(candidate_box.x, candidate_box.max_y + clearance, candidate_box.z, None)
    new_candidates = [
        _Candidate(candidate_box.max_x + clearance, candidate_box.y, candidate_box.z, None),
        y_extend,
    ]
    # Fase 5C: una pieza inclinada no puede servir de soporte -no se ofrece
    # como semilla de apilamiento (seccion 24 del pedido).
    if w.stackable and o.tilt_angle == 0:
        new_candidates.append(_Candidate(candidate_box.x, candidate_box.y, candidate_box.top_z, candidate_box.id))
    candidates.extend(new_candidates)

    return new_total_weight, new_total_moment


def _panel_lateral_cost(box: Box, container: ContainerSpec) -> float:
    """Panels & Fragile -heuristica de optimizacion (NUNCA una restriccion
    dura, ver docstring de pack_container: esto solo desempata ENTRE
    candidatos que YA pasaron _evaluate_candidate) que premia piezas altas
    cerca de la pared lateral (eje Y, ancho) y piezas bajas cerca del
    centro -reduce el riesgo de que un panel/ventana alto quede solo entre
    piezas mucho mas bajas cerca del centro, seccion "Why" del pedido.

    Formula: producto de 2 fracciones normalizadas en [0,1] -
    height_fraction (que tan alta es ESTA pieza en esta orientacion,
    dz/container.height) y center_fraction (que tan lejos de la pared mas
    cercana esta este candidato, distancia/mitad-del-ancho, 0=pegado a la
    pared, 1=en el centro exacto). costo BAJO (bueno) cuando la pieza es
    alta Y esta cerca de la pared (height alto * center bajo), o cuando es
    baja Y esta lejos de la pared/cerca del centro (height bajo * lo que
    sea); costo ALTO (malo, se evita si hay alternativa) SOLO cuando es
    alta Y esta lejos de la pared -exactamente el caso de riesgo que este
    heuristico busca reducir. Nunca decide colocar o no colocar una pieza
    -solo cual de varios candidatos YA validos se usa."""
    if container.height <= TOL or container.width <= TOL:
        return 0.0
    height_fraction = min(1.0, box.dz / container.height)
    dist_to_wall = max(0.0, min(box.y, container.width - box.max_y))
    center_fraction = min(1.0, dist_to_wall / (container.width / 2))
    return height_fraction * center_fraction


# ==========================================================================
# Packing Engine Quality Pass -- Loose Boxes compactness (secciones 5-8 del
# pedido). Generaliza a BOX/PALLET/CUSTOM el MISMO patron "evaluar todos los
# candidatos validos del tier minimo (x,z), elegir el de menor costo" que
# Panels & Fragile ya usaba en exclusiva (ver _panel_lateral_cost) -nunca un
# segundo motor de colocacion, solo una funcion de costo distinta para
# desempatar DENTRO del mismo tier -restriccion ya existente.
# ==========================================================================

_MIN_USEFUL_LATERAL_GAP_MM = 150.0
"""Umbral practico (seccion 8 del pedido: 'does not need full 3D free-space
decomposition') -un hueco lateral remanente MENOR a esto se trata como una
franja practicamente inutilizable para carga real (ningun Box/Panel real
entra ahi); IGUAL o MAYOR se trata como espacio genuinamente reutilizable
mas adelante, sin penalizar. 0 exacto (flush, sin hueco) es siempre el
mejor caso."""


def _free_lateral_interval(candidate_box: Box, placed_boxes: list[Box]) -> tuple[float, float]:
    """El intervalo [lo, hi] en Y que estaba libre ANTES de este candidato
    -acotado por la pared (0/width, quien llama pasa el limite) o la pieza
    ya colocada mas cercana a cada lado, en el MISMO nivel Z y rango X
    (mismo criterio de 'comparte nivel' que geometry.py:check_support)-
    dentro del cual cae este candidato. Sirve para medir, en
    _box_compactness_cost, cuanto hueco USABLE queda a cada lado DESPUES de
    colocar aca -nunca decide si el candidato es valido (eso lo sigue
    haciendo _evaluate_candidate exclusivamente)."""
    lo, hi = 0.0, float("inf")
    for b in placed_boxes:
        if b.id == candidate_box.id:
            continue
        if b.z >= candidate_box.top_z - TOL or b.top_z <= candidate_box.z + TOL:
            continue  # no comparte nivel Z
        if b.x >= candidate_box.max_x - TOL or b.max_x <= candidate_box.x + TOL:
            continue  # no comparte rango X
        if b.max_y <= candidate_box.y + TOL and b.max_y > lo:
            lo = b.max_y
        if b.y >= candidate_box.max_y - TOL and b.y < hi:
            hi = b.y
    return lo, hi


def _sliver_penalty(residual: float) -> float:
    """0.0 si el hueco remanente es exactamente 0 (flush); penalidad
    DOMINANTE (hasta 1.0) cuanto mas chico y mas cerca de 0 sea un hueco
    POSITIVO por debajo del umbral practico -esa es la franja "50mm
    inutilizable" que el pedido pide evitar preferir sobre un hueco de
    600mm (seccion 8). Un hueco YA util (>= umbral) recibe una penalidad
    MUCHO mas chica (proporcional, factor 0.001) -nunca compite con una
    franja muerta real, pero preserva el sesgo de siempre de "lo mas pegado
    posible gana" entre dos candidatos igualmente utiles (sin esto, un
    candidato flush -0mm- y uno con 600mm de aire quedaban EMPATADOS en
    costo, perdiendo la preferencia por compacidad que el first-fit
    anterior si tenia -regresion real medida en el benchmark de esta
    tarea, seccion 21/23: mismo loaded/utilization, pero mas huecos/slivers
    que antes)."""
    if residual <= TOL:
        return 0.0
    if residual >= _MIN_USEFUL_LATERAL_GAP_MM:
        return 0.001 * residual
    return 1.0 - (residual / _MIN_USEFUL_LATERAL_GAP_MM)


def _box_compactness_cost(
    candidate_box: Box, cand: "_Candidate", container: ContainerSpec, placed_boxes: list[Box]
) -> tuple[float, int, float]:
    """Costo de compacidad para BOX/PALLET/CUSTOM (secciones 5-8 del
    pedido) -exactamente el mismo rol que _panel_lateral_cost cumple para
    Panels & Fragile: NUNCA decide si un candidato es valido (eso es
    siempre _evaluate_candidate), solo desempata ENTRE candidatos YA
    validos del mismo tier (x,z) minimo -que ya garantiza alineacion en
    profundidad/piso (seccion 7: 'same row boundary', Part 13 -alignment-
    para Boxes es automatico por construccion del tier, no hace falta un
    termino aparte).

    Componente 1 (dominante): penalidad por "franja muerta" -ver
    _sliver_penalty- sumada en AMBOS lados del hueco lateral libre en el
    que cae este candidato (_free_lateral_interval). Preferir colocar
    flush contra una pared o pieza vecina (Part 6: face contact) es
    exactamente lo que minimiza esta penalidad, sin necesitar un termino
    de "contacto" separado -un candidato SIEMPRE nace flush de un lado (es
    como se genera, ver _commit_candidate/pack_container); lo que varia
    caso a caso es si el lado abierto deja un residuo chico y muerto o uno
    grande y reutilizable.

    Componente 2 (desempate, BACK_RIGHT_FLOOR anchor audit): preferir
    from_right=True -mismo motivo exacto que el desempate ya aplicado a
    Panels & Fragile (ver _panel_cost en pack_container): un candidato de
    continuacion de zona reservada (from_right=False, coordenadas reales
    "sueltas") puede empatar en gap_cost con el seed canonico -sin este
    desempate, el campo 3 (`candidate_box.y` crudo) volvia a sesgar hacia
    la izquierda (y chico) en cualquier empate, resucitando exactamente el
    mismo bug que la correccion de ancla anterior ya habia cerrado.

    Orden de los campos (seccion 17/23 del pedido -"first benchmark... only
    introduce if it materially improves... do not make it pretty at the
    cost of a major loss", generalizado aca a "no empeorar las metricas
    secundarias sin necesidad"): `(0 if from_right, cand.y)` -posicion,
    EXACTAMENTE el mismo criterio que el first-fit de siempre- van
    PRIMERO, `gap_cost` va al FINAL. Benchmarking real (datasets de
    aceptacion, seccion 20-21) mostro que usar `gap_cost` como criterio
    PRIMARIO cambiaba ~30% de las decisiones en un dataset de cajas de
    tamano mixto y el resultado agregado (avg_gap/slivers) terminaba PEOR
    que el first-fit de siempre -un heuristico LOCAL (solo ve lo YA
    colocado en el momento de decidir) puede parecer bueno en el momento y
    quedar mal una vez que se colocan mas piezas despues, un efecto cascada
    conocido de cualquier heuristica greedy sin lookahead completo (seccion
    17 del pedido lo anticipa explicitamente). Con `cand.y` como criterio
    PRIMARIO (identico al de siempre, ya verificado que reproduce el
    first-fit anterior EXACTO, 0 diffs, en el mismo benchmark), `gap_cost`
    solo desempata el caso raro de dos candidatos con la MISMA distancia al
    ancla mismo `cand.y`) mismo -util para las esquinas de zona reservada/
    preplaced donde eso si puede pasar, sin arriesgar el caso comun."""
    lo, hi = _free_lateral_interval(candidate_box, placed_boxes)
    lo = max(lo, 0.0)
    hi = min(hi, container.width)
    left_residual = max(0.0, candidate_box.y - lo)
    right_residual = max(0.0, hi - candidate_box.max_y)
    gap_cost = _sliver_penalty(left_residual) + _sliver_penalty(right_residual)
    return (0 if cand.from_right else 1, cand.y, gap_cost)


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
        boxes_inside=w.boxes_inside,
        reason=reason_text,
        reason_code=reason_code.value,
        item_type=w.item_type,
        orientation_policy=w.orientation_policy,
        stackable_override=w.stackable_override,
        orientation_override=w.orientation_override,
        allow_tilt=w.allow_tilt,
        max_tilt_angle=w.max_tilt_angle,
    )


def _select_tier_candidate(
    inst: "_Instance",
    w: WindowItem,
    candidates: list["_Candidate"],
    orientations,
    container: ContainerSpec,
    placed_boxes: list[Box],
    reserved_zones: list[ReservedZone],
    clearance: float,
    weights_by_id: dict[str, float],
    total_weight: float,
    total_moment: float,
    road_weight_config,
    cost_fn,
) -> tuple | None:
    """Packing Engine Quality Pass (secciones 5-8, 12 del pedido): evalua
    TODOS los candidatos validos de este pass de orientaciones, restringe
    al tier minimo (x,z) -misma prioridad "profundidad y piso antes que
    lateral" de siempre, ver comentario de `candidates.sort` en
    pack_container- y elige el de MENOR costo segun `cost_fn` dentro de
    ese tier.

    Uso EXCLUSIVO de Panels & Fragile (cost_fn=_panel_cost, ver
    pack_container) -mismo patron que ya usaba antes de esta correccion
    (nunca cambiado). BOX/PALLET/CUSTOM usan `_select_first_fit_candidate`
    (mas abajo): benchmarking real (seccion 17/24 del pedido) mostro que
    evaluar TODOS los candidatos para Boxes -en vez de first-fit, que se
    detiene en el primero valido- es O(n) candidatos por pieza en vez de
    O(1) amortizado, y no aporta ninguna diferencia de resultado (el costo
    de compacidad de Boxes, `_box_compactness_cost`, quedo con la posicion
    -identica al first-fit de siempre- como criterio PRIMARIO, ver esa
    funcion) -pagar ese costo sin ningun beneficio de calidad a cambio es
    exactamente lo que la seccion 17 pide evitar ("Do NOT immediately
    rewrite the entire packer... Only introduce... if it materially
    improves"). _evaluate_candidate sigue siendo EXACTAMENTE el mismo
    chequeo fisico para ambos caminos, nunca se duplica ni se relaja.
    Devuelve (cand, o, candidate_box, final_x, candidate_center_x, tier_x,
    tier_z) del mejor candidato, o None si ninguno es valido en este pass."""
    valid: list[tuple] = []
    for cand in candidates:
        for o in orientations:
            evaluated = _evaluate_candidate(
                cand, o, inst, w, container, placed_boxes, reserved_zones, clearance,
                weights_by_id, total_weight, total_moment, road_weight_config,
            )
            if evaluated is not None:
                candidate_box, final_x, candidate_center_x = evaluated
                valid.append((cand, o, candidate_box, final_x, candidate_center_x))
                break
    if not valid:
        return None

    min_x = min(v[0].x for v in valid)
    tier = [v for v in valid if v[0].x == min_x]
    min_z = min(v[0].z for v in tier)
    tier = [v for v in tier if v[0].z == min_z]
    cand, o, candidate_box, final_x, candidate_center_x = min(tier, key=cost_fn)
    return cand, o, candidate_box, final_x, candidate_center_x, min_x, min_z


def _select_first_fit_candidate(
    inst: "_Instance",
    w: WindowItem,
    candidates: list["_Candidate"],
    orientations,
    container: ContainerSpec,
    placed_boxes: list[Box],
    reserved_zones: list[ReservedZone],
    clearance: float,
    weights_by_id: dict[str, float],
    total_weight: float,
    total_moment: float,
    road_weight_config,
) -> tuple | None:
    """First-fit puro (BOX/PALLET/CUSTOM): `candidates` ya viene ordenado
    por (x, z, y) -ver `candidates.sort` en pack_container-, asi que el
    PRIMER candidato valido en ese orden YA ES el de menor (x, z, y),
    exactamente el mismo resultado que `_select_tier_candidate` con costo
    puramente posicional (verificado: 0 diferencias contra el first-fit
    historico en el benchmark de esta tarea, ver
    _box_compactness_cost) -sin pagar el costo de evaluar TODOS los
    candidatos para llegar a la misma respuesta (seccion 17/24 del pedido:
    no introducir mas costo computacional del que un beneficio medido
    justifique). Devuelve la misma forma de tupla que
    _select_tier_candidate (con tier_x/tier_z del candidato encontrado,
    para que _find_gap_fill_substitute funcione identico para ambos
    caminos) o None si ninguno es valido en este pass."""
    for cand in candidates:
        for o in orientations:
            evaluated = _evaluate_candidate(
                cand, o, inst, w, container, placed_boxes, reserved_zones, clearance,
                weights_by_id, total_weight, total_moment, road_weight_config,
            )
            if evaluated is not None:
                candidate_box, final_x, candidate_center_x = evaluated
                return cand, o, candidate_box, final_x, candidate_center_x, cand.x, cand.z
    return None


def _global_shallowest_tier(candidates: list["_Candidate"]) -> tuple[float, float] | None:
    """El tier (x,z) mas superficial que existe en `candidates`, sea o no
    valido para la pieza que le toca el turno -sirve para detectar cuando
    esa pieza esta a punto de abrir un modulo/fila NUEVA (seccion 10 del
    pedido) pese a que hay hueco genuino mas cerca, ver
    _find_gap_fill_substitute.

    Performance (seccion 24 del pedido): el llamador SIEMPRE invoca esto
    justo despues de `candidates.sort(key=lambda c: (c.x, c.z, c.y))` -el
    primer elemento YA ES el (x,z) minimo, escanear todo `candidates` de
    nuevo (O(n), y `candidates` crece con cada pieza colocada -> O(n^2)
    acumulado en el loop principal) es trabajo redundante. Se toma un
    atajo O(1) leyendo `candidates[0]` en vez de recorrer la lista -
    equivalente exacto siempre que `candidates` siga ordenado en ese
    momento (documentado como precondicion, no se re-ordena aca para no
    ocultar un uso incorrecto desde otro lugar)."""
    if not candidates:
        return None
    min_x = candidates[0].x
    min_z = candidates[0].z
    return min_x, min_z


def _global_shallowest_tier_slow(candidates: list["_Candidate"]) -> tuple[float, float] | None:
    """Version O(n) de referencia (sin asumir orden previo) -usada solo por
    los tests para verificar que el atajo O(1) de arriba da el mismo
    resultado, nunca en el camino caliente de pack_container."""
    if not candidates:
        return None
    min_x = min(c.x for c in candidates)
    min_z = min(c.z for c in candidates if c.x == min_x)
    return min_x, min_z


def _reselection_cluster_key(w: WindowItem, optimization_mode: OptimizationMode):
    """Limite de reselection (seccion 16 del pedido, ver
    _find_gap_fill_substitute) -NUNCA cruza un limite de Group/System/
    Delivery Sequence cuando el modo activo los usa como objetivo primario
    (seccion 18: "Group cohesion remains primary, compactness secondary"),
    mismo criterio de agrupamiento que ya decide el ORDEN de colocacion en
    core/strategies.py (_grouping_key/_delivery_key) -reimplementado aca en
    chico para no acoplar packer.py a esos internals privados de
    strategies.py. None (BEST_SPACE): sin restriccion de cluster -compacidad
    es el unico objetivo, cualquier pieza pendiente del mismo item_type es
    candidata a la sustitucion."""
    if optimization_mode == OptimizationMode.KEEP_GROUPS:
        return w.group or ""
    if optimization_mode == OptimizationMode.KEEP_SYSTEMS:
        return w.system or ""
    if optimization_mode == OptimizationMode.PRIORITIZE_DELIVERY:
        return w.delivery_sequence
    return None


_GAP_FILL_LOOKAHEAD = 15
"""Packing Engine Quality Pass, seccion 16/17 del pedido: ventana ACOTADA
(no una busqueda exhaustiva/beam search -seccion 17: "Do NOT immediately
rewrite the entire packer") de piezas pendientes que _find_gap_fill_substitute
esta dispuesta a revisar antes de resignarse a abrir un modulo/fila nueva.
Mismo rango que el "beam width 5-20" que el pedido sugiere explicitamente
para un lookahead acotado -benchmarking real (seccion 24) mostro que un
valor mas alto (40) es innecesariamente caro: la sustitucion se dispara en
la gran mayoria de las piezas de un plan real (gap por tier casi siempre
distinto de 0 en un layout ya avanzado), asi que el costo de la ventana se
multiplica por CASI CADA pieza del plan -15 preserva la enorme mayoria del
beneficio medido (mismo resultado en los datasets de aceptacion) a una
fraccion del costo."""


def _find_gap_fill_substitute(
    start_index: int,
    instances: list["_Instance"],
    placed_flags: list[bool],
    optimization_mode: OptimizationMode,
    target_tier: tuple[float, float],
    candidates: list["_Candidate"],
    container: ContainerSpec,
    placed_boxes: list[Box],
    reserved_zones: list[ReservedZone],
    clearance: float,
    weights_by_id: dict[str, float],
    total_weight: float,
    total_moment: float,
    road_weight_config,
) -> tuple | None:
    """Packing Engine Quality Pass, secciones 15/16 del pedido ("module
    closure" / "item order should not create holes"): si a la pieza que le
    tocaba el turno (indice `start_index`) NO le entra ningun candidato en
    el tier mas superficial realmente disponible (`target_tier`) -aunque SI
    le entre uno mas profundo, lo que abriria un modulo/fila nueva
    prematuramente dejando el hueco actual sin cerrar- busca, ACOTADO a
    _GAP_FILL_LOOKAHEAD piezas pendientes y NUNCA cruzando un limite de
    cluster (ver _reselection_cluster_key), una pieza pendiente que SI
    entre exactamente en `target_tier`. Devuelve (index, cand, o,
    candidate_box, final_x, candidate_center_x) de la primera que encaje
    (primer-fit dentro de la busqueda de sustituto, mismo criterio
    determinista que el resto del motor), o None si ninguna lo hace -en ese
    caso el llamador sigue con la pieza original en el tier mas profundo
    que si le sirve, exactamente como siempre."""
    target_x, target_z = target_tier
    tier_only = [c for c in candidates if c.x == target_x and c.z == target_z]
    if not tier_only:
        return None

    origin = instances[start_index].source
    origin_key = _reselection_cluster_key(origin, optimization_mode)

    scanned = 0
    for j in range(start_index + 1, len(instances)):
        if placed_flags[j]:
            continue
        candidate_inst = instances[j]
        w = candidate_inst.source
        if w.item_type != origin.item_type:
            continue  # un plan siempre es homogeneo en item_type, pero nunca se asume sin chequear
        if _reselection_cluster_key(w, optimization_mode) != origin_key:
            # Limite de cluster (Group/System/Delivery Sequence activo):
            # mas alla de este punto ya no es "el mismo modulo/grupo activo"
            # -instances esta ordenado con la clave de agrupamiento como
            # campo DOMINANTE (ver strategies.py), asi que el cluster es
            # siempre contiguo -no hace falta seguir buscando mas lejos.
            break
        scanned += 1
        if scanned > _GAP_FILL_LOOKAHEAD:
            break
        if total_weight + w.weight > container.max_weight + TOL:
            continue
        orientations = get_valid_orientations(w.dimensions, w.resolved_orientation_policy)
        for cand in tier_only:
            for o in orientations:
                evaluated = _evaluate_candidate(
                    cand, o, candidate_inst, w, container, placed_boxes, reserved_zones, clearance,
                    weights_by_id, total_weight, total_moment, road_weight_config,
                )
                if evaluated is not None:
                    candidate_box, final_x, candidate_center_x = evaluated
                    return j, cand, o, candidate_box, final_x, candidate_center_x
    return None


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
    # Momento longitudinal acumulado (Sum(weight_i * center_x_i), coordenadas
    # reales) para el chequeo incremental de RoadWeightConfig (Fase 2B, ver
    # core/road_weight.py) -se mantiene junto a total_weight en vez de
    # recalcularlo desde cero por cada candidato evaluado.
    total_moment = 0.0
    road_weight_config = container.road_weight_config

    # BACK_RIGHT_FLOOR anchor audit (seccion 3-6 del pedido): el punto de
    # arranque CANONICO -x=0 interno (espejado a la pared del fondo, ver
    # _evaluate_candidate) + from_right=True (espejado a la pared DERECHA) +
    # z=0 (piso)- es ahora el UNICO seed inicial para TODO item_type, no solo
    # Panels & Fragile. Root cause de la regresion (seccion 3 del pedido):
    # BOX/PALLET/CUSTOM solo tenian el seed IZQUIERDO (from_right=False,
    # y=0) porque el camino first-fit general filtraba explicitamente
    # `if cand.from_right: continue` -la primera pieza de CUALQUIER plan
    # terminaba en BACK_LEFT_FLOOR, nunca BACK_RIGHT_FLOOR (confirmado
    # colocando piezas reales e inspeccionando sus coordenadas, no por
    # inspeccion visual). Ese filtro se elimino mas abajo (ver el loop
    # first-fit); con el UNICO seed inicial ahora anclado a la derecha, todo
    # item_type hereda BACK_RIGHT_FLOOR automaticamente, sin heuristica
    # nueva ni caso especial "primera pieza".
    #
    # El seed IZQUIERDO (from_right=False, y=0) SOLO se agrega para planes
    # de Panels & Fragile -es el segundo punto de arranque del llenado
    # bilateral (seccion 4/5 del pedido: "then LEFT WALL -> center, RIGHT
    # WALL -> center", nunca antes de comprometer el ancla canonica). Sin
    # este seed extra, solo existiria un punto de arranque real (la pared
    # derecha) y toda fila crecería solo desde ahi, sin forma de que la
    # pared opuesta tambien reciba piezas altas (ver _panel_lateral_cost).
    # BOX/PALLET/CUSTOM nunca lo ven -el camino first-fit general ya no lo
    # necesita, y la condicion de abajo ni lo agrega para esos planes- por
    # lo que no reintroduce el problema original (ademas, un plan siempre es
    # homogeneo en item_type -ver core/import_items.py- asi que este chequeo
    # es exacto, nunca una mezcla real).
    candidates: list[_Candidate] = [_Candidate(0.0, 0.0, 0.0, None, from_right=True)]
    if any(item.item_type == ItemType.PANEL for item in items):
        candidates.append(_Candidate(0.0, 0.0, 0.0, None))

    # Sin esto, la busqueda solo tiene UN punto de arranque (y=0) y todos los
    # demas candidatos se derivan de piezas ya colocadas (max_x/max_y) -si una
    # zona reservada (p.ej. el pasillo central) corta el contenedor en 2
    # franjas de Y, el lado que queda MAS ALLA de la zona nunca recibe ningun
    # candidato semilla y se queda vacio por completo (bug: "todo el cargo de
    # un solo lado"). Se siembra un punto de arranque extra justo despues de
    # cada zona para que el algoritmo pueda llenar ambos lados del pasillo.
    # BACK_RIGHT_FLOOR anchor audit (seccion 7 del pedido): con el seed
    # canonico ahora anclado a la pared DERECHA (from_right=True, ver mas
    # arriba), el lado que el seed por defecto YA cubria naturalmente paso
    # de ser "antes de la zona" (near_y, hacia y=0) a "despues de la zona"
    # (far_y, hacia la pared derecha) -sin este seed simetrico adicional en
    # near_y, un plan CON zona reservada que bloquee el tramo pegado a la
    # pared derecha se queda sin NINGUN candidato que alcance el resto del
    # contenedor (regresion real encontrada al testear: 0 piezas colocadas,
    # confirmado con datos reales, no visual). Ambos seeds usan coordenadas
    # reales sin espejar (igual que siempre) -solo garantizan que las DOS
    # franjas que deja una zona reservada sean alcanzables, sea cual sea la
    # direccion del ancla canonica.
    for zone in reserved_zones:
        far_y = zone.y + zone.width + clearance
        if far_y < container.width - TOL:
            candidates.append(_Candidate(0.0, far_y, 0.0, None))
        if zone.y - clearance > TOL:
            candidates.append(_Candidate(0.0, 0.0, 0.0, None))

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
            tilt_angle=p.tilt_angle,
            tilt_axis=p.tilt_axis,
            base_dx=p.base_dx,
            base_dy=p.base_dy,
            base_dz=p.base_dz,
            item_type=p.item_type,
        )
        placed_boxes.append(seed_box)
        placed_pieces.append(p)
        weights_by_id[p.id] = p.weight
        total_weight += p.weight
        # p.x ya esta en coordenadas reales (no espejadas) -a diferencia de
        # internal_x, que solo se usa para sembrar la busqueda geometrica.
        total_moment += p.weight * piece_center_x(p.x, p.dx)

        new_candidates = [
            _Candidate(seed_box.max_x + clearance, seed_box.y, seed_box.z, None),
            _Candidate(seed_box.x, seed_box.max_y + clearance, seed_box.z, None),
        ]
        # Fase 5C: una pieza inclinada no puede servir de soporte -no se
        # ofrece como semilla de apilamiento (seccion 24 del pedido).
        if p.stackable and p.tilt_angle == 0:
            new_candidates.append(_Candidate(seed_box.x, seed_box.y, seed_box.top_z, seed_box.id))
        candidates.extend(new_candidates)

    # Packing Engine Quality Pass (secciones 1-17 del pedido): closure que
    # captura `container` -definida UNA sola vez afuera del loop, mismo
    # patron ya usado antes de esta correccion (_panel_lateral_cost +
    # desempate BACK_RIGHT_FLOOR, sin cambios). BOX/PALLET/CUSTOM usan
    # _select_first_fit_candidate en vez de un cost_fn -ver comentario en
    # el loop principal mas abajo (seccion 17/24: benchmarking real mostro
    # que _box_compactness_cost, con la posicion como criterio primario,
    # da el MISMO resultado que first-fit puro pero paga el costo de
    # evaluar todos los candidatos; se mantiene definida y testeada -no se
    # borra la implementacion del pedido de las secciones 5-8- pero no se
    # conecta al loop principal por falta de beneficio medido).
    def _panel_cost(v: tuple) -> tuple:
        return (_panel_lateral_cost(v[2], container), 0 if v[0].from_right else 1)

    placed_flags = [False] * len(instances)
    i = 0
    while i < len(instances):
        if placed_flags[i]:
            i += 1
            continue

        inst = instances[i]
        w = inst.source

        if not _fits_in_container_at_all(w, container):
            unloaded.append(
                _unloaded_item(
                    inst, UnloadedReason.ORIENTATION_CONFLICT, "No cabe en el contenedor en ninguna orientacion valida"
                )
            )
            placed_flags[i] = True
            i += 1
            continue

        if total_weight + w.weight > container.max_weight + TOL:
            unloaded.append(
                _unloaded_item(inst, UnloadedReason.MAX_WEIGHT_EXCEEDED, "Excede el peso maximo del contenedor")
            )
            placed_flags[i] = True
            i += 1
            continue

        # Prioriza x (profundidad) sobre z e y: llena por completo una seccion
        # transversal (todo el ancho y alto disponibles en esa profundidad)
        # antes de avanzar a la siguiente. Combinado con el espejo de X al
        # construir cada PlacedPiece (ver mas abajo), esto hace que el
        # contenedor se llene solido desde el fondo hacia la puerta, en vez
        # de una sola fila a lo largo del contenedor.
        candidates.sort(key=lambda c: (c.x, c.z, c.y))

        # Fase 5C-FINAL, seccion 8 del pedido ("0 grados SIEMPRE preferido")
        # + performance (seccion 53): se prueban TODAS las posiciones
        # candidatas con las 4 orientaciones base (0 grados) primero, y solo
        # si NINGUNA posicion funciona a 0 grados se hace una segunda pasada
        # con las variantes inclinadas. Antes se probaban las 36
        # orientaciones (4 base + 32 con Tilt) en CADA posicion candidata
        # rechazada -carisimo, y ademas inutil: si el item termina en 0
        # grados de todos modos, esas 32 pruebas extra por posicion nunca
        # aportaron nada. Con este orden, un plan donde Tilt esta habilitado
        # pero nunca hace falta corre a la misma velocidad que uno sin Tilt.
        orientation_passes = [get_valid_orientations(w.dimensions, w.resolved_orientation_policy)]
        if w.allow_tilt and (w.max_tilt_angle or 0.0) > TOL:
            all_orientations = get_valid_orientations(
                w.dimensions, w.resolved_orientation_policy, allow_tilt=True, max_tilt_angle=w.max_tilt_angle
            )
            orientation_passes.append(all_orientations[len(orientation_passes[0]) :])

        # Packing Engine Quality Pass, secciones 5-8/12/17/24 del pedido:
        # Panels & Fragile sigue usando _select_tier_candidate (evaluar
        # TODOS los candidatos validos del tier minimo, elegir por costo
        # lateral -sin cambios de comportamiento respecto de antes de esta
        # tarea). BOX/PALLET/CUSTOM usan _select_first_fit_candidate -mas
        # rapido y, verificado en el benchmark de esta tarea, da EXACTO el
        # mismo resultado que evaluar todos los candidatos con
        # _box_compactness_cost (que quedo con la posicion, identica al
        # first-fit de siempre, como criterio primario -ver esa funcion);
        # pagar el costo O(candidatos) de evaluar todos sin ningun cambio
        # de resultado violaria la seccion 17 del pedido ("Only introduce
        # if it materially improves"). Nunca se salta ningun chequeo
        # fisico -_evaluate_candidate es EXACTAMENTE el mismo para ambos
        # caminos.
        is_panel = w.item_type == ItemType.PANEL

        result = None
        for orientations in orientation_passes:
            if is_panel:
                result = _select_tier_candidate(
                    inst, w, candidates, orientations, container, placed_boxes, reserved_zones, clearance,
                    weights_by_id, total_weight, total_moment, road_weight_config, _panel_cost,
                )
            else:
                result = _select_first_fit_candidate(
                    inst, w, candidates, orientations, container, placed_boxes, reserved_zones, clearance,
                    weights_by_id, total_weight, total_moment, road_weight_config,
                )
            if result is not None:
                break

        tier = (result[5], result[6]) if result is not None else None

        # Packing Engine Quality Pass, secciones 10/15/16 del pedido ("do
        # not open a new layer too early" / "module closure" / "item order
        # should not create holes"): si esta pieza NO uso el tier (x,z) mas
        # superficial que de verdad existe en `candidates` -sea porque cayo
        # en uno MAS PROFUNDO (abriendo un modulo/fila nueva prematuramente)
        # o porque no encontro NINGUN candidato valido en absoluto (seccion
        # 16, ejemplo exacto del pedido: "next item = 900mm... do NOT
        # immediately close the module... prefer the 650mm item"- un item
        # que no entra en NINGUN lado tampoco debe cerrar el hueco actual)-
        # se busca (acotado, nunca cruzando Group/System/Delivery Sequence
        # cuando el modo activo los usa como objetivo primario, ver
        # _find_gap_fill_substitute) una pieza PENDIENTE distinta que SI
        # entre exactamente en ese hueco mas superficial, y se la coloca a
        # ELLA en vez de resignar el hueco. La pieza original (`i`) queda
        # pendiente y se reintenta en la proxima vuelta -`candidates` ya
        # cambio, puede que ahora si le entre algo, o puede que siga sin
        # entrarle nada al tier superficial (en cuyo caso se usa su propio
        # resultado, sin loop infinito: cada sustitucion consume una pieza
        # pendiente DISTINTA, acotado por `len(instances)`).
        shallow = _global_shallowest_tier(candidates)
        substitute = None
        if shallow is not None and tier != shallow:
            substitute = _find_gap_fill_substitute(
                i, instances, placed_flags, optimization_mode, shallow, candidates, container, placed_boxes,
                reserved_zones, clearance, weights_by_id, total_weight, total_moment, road_weight_config,
            )

        if substitute is None and result is None:
            unloaded.append(
                _unloaded_item(inst, UnloadedReason.NO_VALID_SPACE, "Sin espacio disponible en el contenedor")
            )
            placed_flags[i] = True
            i += 1
            continue

        if substitute is not None:
            j, s_cand, s_o, s_box, s_final_x, s_center_x = substitute
            s_inst = instances[j]
            total_weight, total_moment = _commit_candidate(
                s_box, s_final_x, s_center_x, s_cand, s_o, s_inst, s_inst.source,
                placed_boxes, placed_pieces, weights_by_id, candidates, clearance, total_weight, total_moment,
            )
            placed_flags[j] = True
            continue  # `i` sigue pendiente, se reintenta en la proxima vuelta

        # substitute is None y result is not None aca (el caso "ninguno de
        # los dos" ya se resolvio como unloaded mas arriba) -se comete el
        # resultado propio de `inst` en su propio mejor tier (profundo o
        # no, segun corresponda).
        cand, o, candidate_box, final_x, candidate_center_x, _tier_x, _tier_z = result
        total_weight, total_moment = _commit_candidate(
            candidate_box, final_x, candidate_center_x, cand, o, inst, w,
            placed_boxes, placed_pieces, weights_by_id, candidates, clearance, total_weight, total_moment,
        )
        placed_flags[i] = True
        i += 1

    metrics = compute_metrics(container, placed_pieces, unloaded)

    return PackingResult(container=container, placed=placed_pieces, unloaded=unloaded, metrics=metrics)
