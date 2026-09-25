"""CUBOX 2.0 -- Packing Engine V2 (prototype, STAGE A).

Camino EXPERIMENTAL -no se usa en produccion todavia, ver core/packer.py
(sigue siendo el motor real). Este modulo implementa una arquitectura
distinta para evaluar si resuelve mejor el problema de calidad de Loose
Boxes que la Packing Engine Quality Pass anterior NO resolvio (avg_gap/
slivers identicos al first-fit de siempre, ver esa tarea).

Diferencia de fondo con core/packer.py: en vez de una lista de PUNTOS
candidatos que solo CRECE (nunca se poda, nunca sabe cuanto espacio libre
real rodea a cada punto), este motor mantiene el conjunto de REGIONES
RECTANGULARES libres (Empty Maximal Spaces / EMS) que efectivamente quedan
en el Load Space -se actualiza (corta + poda) despues de cada pieza
colocada, asi que el motor siempre sabe la forma real del hueco que le
queda a cada region, no una aproximacion basada en vecinos cercanos.

Boxes: EMS genericos + seleccion acotada de mejor (item x orientacion x
espacio) -ver pack_boxes_v2/_score_box_placement.

Panels & Fragile: mismo motor de EMS (particionado/poda COMPARTIDOS, nunca
dos implementaciones de esa parte -bug-prone) pero con una politica de
seleccion/costo DEDICADA (ver _score_panel_placement) que preserva la
heuristica bilateral (alto cerca de pared, bajo cerca de centro) y ademas
concentra el hueco residual inevitable cerca del CENTRO del modulo -algo
que el EMS logra "gratis": al insertar piezas desde AMBAS paredes hacia
adentro, el ultimo fragmento libre de cada modulo queda, por construccion,
en el medio.

Nunca cambia: orientaciones validas (core/orientation.py), reglas de
colision/soporte/stack-weight/clearance/zona reservada/road-weight
(core/geometry.py, core/reserved_zones.py, core/road_weight.py,
core/tilt_collision.py para Tilt -sin cambios, reusados tal cual), el
ancla canonica BACK_RIGHT_FLOOR, ni la convencion de coordenadas (puerta
x=0, fondo x=length, derecha y grande, piso z=0)."""

from dataclasses import dataclass

from app.core.geometry import (
    TOL,
    Box,
    check_stack_weight,
    check_support,
    has_clearance_conflict,
    within_container,
)
from app.core.orientation import get_valid_orientations
from app.core.reasons import UnloadedReason
from app.core.reserved_zones import ReservedZone, zone_conflict_with_clearance
from app.core.road_weight import piece_center_x, would_exceed_support_limits
from app.core.strategies import build_sort_key
from app.core.tilt_collision import has_precise_collision
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

# Reutiliza compute_metrics/_Instance/_expand_instances/_unloaded_item de
# packer.py -EXACTAMENTE la misma logica (metricas, expansion de quantity,
# formato de UnloadedItem), nunca una segunda implementacion de algo que ya
# esta testeado y no tiene nada que ver con la arquitectura de packing en
# si.
from app.core.packer import _expand_instances, _fits_in_container_at_all, _Instance, _unloaded_item, compute_metrics

_MIN_USEFUL_GAP_MM = 150.0
"""Mismo umbral practico que core/packer.py (ver ahi la justificacion) -
duplicado a proposito para no acoplar el prototipo V2 al modulo de
produccion via un import cruzado de una constante interna."""

_ITEM_WINDOW = 20
"""Packing Engine V2, seccion 12/17 del pedido: ventana ACOTADA de items
PENDIENTES elegibles a evaluar por decision de colocacion -'candidate
remaining items: top 10-20'."""

_SPACE_WINDOW = 60
"""Ventana ACOTADA de espacios libres candidatos por decision -'candidate
spaces: top N relevant free spaces'. Benchmarking real (seccion 19/23 del
pedido) encontro que un valor chico (12) causaba PERDIDA DE CAPACIDAD real
-items marcados sin espacio pese a haber varios metros cubicos libres,
porque la region grande sin fragmentar quedaba fuera de la ventana una vez
que suficientes fragmentos chicos (apilado/filas laterales) ya existian.
Se aislo experimentalmente (ver antes/despues en el reporte de esta
tarea): la ventana de ESPACIOS es la que importa para capacidad -la de
ITEMS (_ITEM_WINDOW) puede quedarse chica sin perder nada."""


def _sliver_penalty(residual: float) -> float:
    if residual <= TOL:
        return 0.0
    if residual >= _MIN_USEFUL_GAP_MM:
        return 0.001 * residual
    return 1.0 - (residual / _MIN_USEFUL_GAP_MM)


# ==========================================================================
# Empty Maximal Spaces -- motor de espacio libre compartido por Boxes y
# Panels (seccion 2/13 del pedido).
# ==========================================================================


@dataclass(frozen=True)
class EmptySpace:
    """Una region rectangular VACIA del Load Space, en coordenadas REALES
    de siempre (nunca espejadas -a diferencia de core/packer.py:_Candidate,
    este motor no necesita ese truco: la region libre YA conoce su propia
    extension real en los 3 ejes, asi que anclar una pieza a su esquina
    fondo-derecha-piso es directo: x = x1 - dx, y = y1 - dy, z = z0)."""

    x: float
    y: float
    z: float
    dx: float
    dy: float
    dz: float

    @property
    def x1(self) -> float:
        return self.x + self.dx

    @property
    def y1(self) -> float:
        return self.y + self.dy

    @property
    def z1(self) -> float:
        return self.z + self.dz

    @property
    def volume(self) -> float:
        return self.dx * self.dy * self.dz


def _intersects(s: EmptySpace, b: Box, tol: float = TOL) -> bool:
    return not (
        b.x >= s.x1 - tol or b.x + b.dx <= s.x + tol
        or b.y >= s.y1 - tol or b.y + b.dy <= s.y + tol
        or b.z >= s.z1 - tol or b.z + b.dz <= s.z + tol
    )


def _split_space(s: EmptySpace, b: Box, tol: float = TOL) -> list[EmptySpace]:
    """Si `b` no invade `s`, la devuelve intacta. Si la invade, genera hasta
    6 sub-espacios MAXIMALES -uno por cada cara de `b` que corta a `s`,
    cada uno extendido al maximo en los otros 2 ejes (tecnica estandar de
    Empty Maximal Spaces, seccion 2 del pedido: 'split the affected free
    space into remaining valid rectangular spaces'). Los degenerados
    (volumen ~0) se descartan aca mismo."""
    if not _intersects(s, b, tol):
        return [s]

    bx0, bx1 = b.x, b.x + b.dx
    by0, by1 = b.y, b.y + b.dy
    bz0, bz1 = b.z, b.z + b.dz

    candidates = [
        EmptySpace(s.x, s.y, s.z, bx0 - s.x, s.dy, s.dz) if bx0 > s.x + tol else None,
        EmptySpace(bx1, s.y, s.z, s.x1 - bx1, s.dy, s.dz) if s.x1 > bx1 + tol else None,
        EmptySpace(s.x, s.y, s.z, s.dx, by0 - s.y, s.dz) if by0 > s.y + tol else None,
        EmptySpace(s.x, by1, s.z, s.dx, s.y1 - by1, s.dz) if s.y1 > by1 + tol else None,
        EmptySpace(s.x, s.y, s.z, s.dx, s.dy, bz0 - s.z) if bz0 > s.z + tol else None,
        EmptySpace(s.x, s.y, bz1, s.dx, s.dy, s.z1 - bz1) if s.z1 > bz1 + tol else None,
    ]
    return [c for c in candidates if c is not None and c.dx > tol and c.dy > tol and c.dz > tol]


def _contains(a: EmptySpace, b: EmptySpace, tol: float = TOL) -> bool:
    """True si `b` esta completamente adentro de `a` -usado para podar
    espacios no-maximales despues de un split (seccion 2 del pedido:
    'remove contained spaces; merge/prune duplicates')."""
    return (
        b.x >= a.x - tol and b.x1 <= a.x1 + tol
        and b.y >= a.y - tol and b.y1 <= a.y1 + tol
        and b.z >= a.z - tol and b.z1 <= a.z1 + tol
    )


def _prune_spaces(spaces: list[EmptySpace]) -> list[EmptySpace]:
    """Elimina espacios degenerados y NO-maximales (contenidos en otro) -
    mantiene el conjunto de espacios ACOTADO (seccion 13 del pedido: 'poor
    scaling partly because candidate count grows without sufficient
    pruning', el problema explicito que este motor busca resolver). Entre
    dos espacios IDENTICOS, conserva uno solo (deduplicado por contencion
    mutua)."""
    spaces = [s for s in spaces if s.dx > TOL and s.dy > TOL and s.dz > TOL]
    kept: list[EmptySpace] = []
    for i, s in enumerate(spaces):
        redundant = False
        for j, o in enumerate(spaces):
            if i == j:
                continue
            if _contains(o, s) and not (_contains(s, o) and i < j):
                # `s` cabe adentro de `o` -no es maximal, se descarta.
                # Empate exacto (mismo espacio dos veces): se queda con el
                # de menor indice para que el resultado sea deterministico.
                redundant = True
                break
        if not redundant:
            kept.append(s)
    return kept


def _update_spaces(spaces: list[EmptySpace], b: Box) -> list[EmptySpace]:
    updated: list[EmptySpace] = []
    for s in spaces:
        updated.extend(_split_space(s, b))
    return _prune_spaces(updated)


# ==========================================================================
# Colocacion: mismos chequeos duros que core/packer.py (nunca una segunda
# implementacion de una regla fisica) -la diferencia es que un candidato
# nacido de un EMS ya esta GARANTIZADO libre de colision con piezas
# colocadas (invariante del propio EMS), asi que se salta el chequeo O(
# piezas colocadas) de colision para la mayoria de los casos -unica
# excepcion: piezas con Tilt (envolvente AABB puede no coincidir con la
# forma real inclinada, ver core/geometry.py:Box.tilt_angle), donde SI se
# reverifica con la colision precisa como red de seguridad.
# ==========================================================================


@dataclass
class _EvalStats:
    candidate_evaluations: int = 0
    collision_checks: int = 0
    max_spaces: int = 0
    total_spaces_samples: int = 0
    space_count_samples: int = 0


def _try_place_in_space(
    space: EmptySpace,
    o,
    inst: _Instance,
    w: WindowItem,
    container: ContainerSpec,
    placed_boxes: list[Box],
    reserved_zones: list[ReservedZone],
    clearance: float,
    weights_by_id: dict[str, float],
    total_weight: float,
    total_moment: float,
    road_weight_config,
    stats: _EvalStats,
    anchor: str = "right",
) -> tuple[Box, float, float, str] | None:
    """Intenta anclar `inst` (con orientacion `o`) a la esquina fondo(x1)-
    piso(z0) del `space` dado, y lateralmente a la derecha (y1, default) o
    IZQUIERDA (y0, `anchor="left"`) de ESE espacio -mismo ancla canonico
    BACK_RIGHT_FLOOR de siempre para Boxes (que solo usan "right", nunca
    "left"). Panels & Fragile SI necesitan ambos anclajes -seccion 9/11
    del pedido, llenado bilateral- ver _search_best_placement: para cada
    espacio candidato prueba AMBOS anclajes cuando `anchor_modes` trae mas
    de uno. Sin esto, un espacio SIEMPRE se llena creciendo hacia un solo
    lado (el de su propio borde y1), nunca el otro -bug real encontrado
    benchmarking Panels: las 10 piezas terminaban todas del mismo lado,
    nunca bilateral (ver seccion 23 del reporte de esta tarea)."""
    stats.candidate_evaluations += 1
    if o.dx > space.dx + TOL or o.dy > space.dy + TOL or o.dz > space.dz + TOL:
        return None

    x = space.x1 - o.dx
    y = space.y1 - o.dy if anchor == "right" else space.y
    z = space.z

    box = Box(
        id=inst.instance_id, x=x, y=y, z=z, dx=o.dx, dy=o.dy, dz=o.dz,
        stackable=w.stackable, max_stack_weight=w.max_stack_weight,
        tilt_angle=o.tilt_angle, tilt_axis=o.tilt_axis,
        base_dx=o.base_dx if o.base_dx is not None else o.dx,
        base_dy=o.base_dy if o.base_dy is not None else o.dy,
        base_dz=o.base_dz if o.base_dz is not None else o.dz,
        item_type=w.item_type,
    )

    if not within_container(box, container.length, container.width, container.height):
        return None
    if zone_conflict_with_clearance(box, reserved_zones, clearance) is not None:
        return None
    if clearance > TOL and has_clearance_conflict(box, placed_boxes, clearance) is not None:
        return None
    # Invariante EMS: `space` esta garantizado libre de colision con
    # `placed_boxes` -salvo para Tilt (la AABB de una pieza inclinada no
    # es su forma fisica real, ver core/geometry.py:Box.tilt_angle), donde
    # SI se reverifica con la colision precisa como red de seguridad.
    if box.tilt_angle != 0:
        stats.collision_checks += 1
        if has_precise_collision(box, placed_boxes) is not None:
            return None

    if z > TOL:
        ok, _ = check_support(box, placed_boxes)
        if not ok:
            return None
        ok, _ = check_stack_weight(box, w.weight, placed_boxes, weights_by_id)
        if not ok:
            return None

    prospective_weight = total_weight + w.weight
    center_x = piece_center_x(box.x, box.dx)
    prospective_moment = total_moment + w.weight * center_x
    if would_exceed_support_limits(prospective_weight, prospective_moment, road_weight_config):
        return None

    return box, box.x, center_x, o.label


# ==========================================================================
# Scoring (seccion 4/10/11 del pedido) -Boxes y Panels usan el MISMO motor
# de espacio libre y la MISMA busqueda acotada (_search_best_placement mas
# abajo), pero cada uno con su propia politica de costo -nunca la logica
# generica de Boxes aplicada a Panels (seccion 8 del pedido: "Do NOT use
# generic box logic" para Panels).
# ==========================================================================


def _space_priority_key(space: EmptySpace, container: ContainerSpec) -> tuple[float, float, float]:
    """Que tan cerca del ancla canonica (fondo + piso + derecha) esta este
    espacio -mismo principio de siempre ('profundidad y piso antes que
    lateral'), ahora aplicado a la REGION libre en si, no a un punto
    candidato aislado."""
    depth_gap = container.length - space.x1
    lateral_gap = container.width - space.y1
    return (depth_gap, space.z, lateral_gap)


def _score_box_placement(
    space: EmptySpace, box: Box, container: ContainerSpec, anchor: str, current_min_x: float,
    is_natural: bool = True, placed_boxes: list[Box] | None = None, pending_height_counts: dict[float, int] | None = None,
) -> tuple:
    """Seccion 4 del pedido -orden ACTUALIZADO Stage A.5 Parte 2 ("used
    length toward the door must be a STRONG objective"): (0) NUEVO
    depth_increase, (1) profundidad usada por el espacio en si, (2) piso,
    (3) lateral, (4) calidad del hueco remanente en los 3 ejes. La
    'maximizacion de continuidad/capacidad' (objetivo 1 del pedido) no es
    un campo de este tuple -la logra la BUSQUEDA (_search_best_placement,
    que intenta activamente varios items antes de resignarse), no el
    desempate entre candidatos ya validos. `anchor` no se usa -Boxes SOLO
    reciben anchor_modes=("right",), ver pack_boxes_v2.

    `depth_increase` (Stage A.5, causa raiz confirmada con trace
    item-por-item en BOX-B, ver reporte de esa tarea): antes, `depth_gap`
    media la profundidad del ESPACIO elegido en si (container.length -
    space.x1) -un proxy razonable la mayoria de las veces, pero NUNCA
    comparaba contra cuanta profundidad ya esta realmente en uso por el
    layout completo. Con datasets mixtos, dos espacios podian tener
    depth_gap parecido pero uno de ellos, al colocar la pieza ahi, SI
    extendia la envolvente usada (box.x menor a lo ya usado) mientras el
    otro caia DENTRO de la profundidad que ya estaba en uso -sin este
    campo, ambos competian en igualdad de condiciones en depth_gap/hueco
    remanente, y la pieza que en realidad abria profundidad nueva podia
    ganar por un mejor desempate lateral/sliver. depth_increase=0 para
    CUALQUIER candidato que no extienda la envolvente actual -entre esos,
    el resto del tuple decide como antes; solo paga costo el que de verdad
    la extiende, proporcional a cuanto la extiende.

    `is_natural` (Stage A.5 Parte 2-3): el primer intento fue forzar
    SIEMPRE la orientacion natural (permutacion L x W x H) antes de
    considerar rotaciones -replicaba el orden de core/packer.py, pero el
    benchmark de esta tarea mostro que empeoraba used_length (6200mm ->
    6700mm): muchas de las rotaciones que el motor elegia SI eran
    genuinamente utiles (encajaban en huecos que la orientacion natural
    no habria llenado), y bloquearlas todas de entrada perdia mas de lo
    que corregia. La version final es un DESEMPATE suave, no una
    restriccion dura: `non_natural_cost` solo decide entre candidatos que
    YA EMPATAN en depth_increase (el mismo impacto real en la
    envolvente) -ahi, preferir la orientacion natural evita el patron de
    'girar de canto para ahorrar unos mm' sin nunca bloquear una rotacion
    que de verdad reduce la profundidad usada."""
    depth_increase = max(0.0, current_min_x - box.x)
    non_natural_cost = 0.0 if is_natural else 1.0
    depth_gap = container.length - space.x1
    lateral_gap = container.width - space.y1
    leftover_cost = (
        _sliver_penalty(space.dx - box.dx) + _sliver_penalty(space.dy - box.dy) + _sliver_penalty(space.dz - box.dz)
    )
    return (depth_increase, non_natural_cost, depth_gap, space.z, lateral_gap, leftover_cost)


def _panel_module_gap_metrics(space: EmptySpace, box: Box, placed_boxes: list[Box], tol: float = 1e-6) -> tuple[int, float, float]:
    """Seccion 10/14 del pedido + Stage A.5 Parte 6 ('Add explicit GAP
    QUALITY evaluation... internal discontinuity count; total internal
    lateral gap; largest internal gap'). Simula la fila lateral del MODULO
    (piezas ya colocadas cuyo borde de fondo coincide con `space.x1`, mas
    esta candidata) y mide los huecos INTERNOS resultantes -no el hueco
    hacia las paredes (eso ya lo cubre `wall_pref_cost`/`balance_cost`).
    Devuelve (cantidad de huecos, hueco total, hueco mas grande) -0/0.0/0.0
    si la fila queda sin ningun hueco interno (flush)."""
    module_back_x = space.x1
    same_module = [pb for pb in placed_boxes if abs((pb.x + pb.dx) - module_back_x) < 50.0]
    row = sorted([(pb.y, pb.y + pb.dy) for pb in same_module] + [(box.y, box.y + box.dy)])
    gaps = [b_lo - a_hi for (_, a_hi), (b_lo, _) in zip(row, row[1:]) if b_lo - a_hi > tol]
    return len(gaps), sum(gaps), (max(gaps) if gaps else 0.0)


def _panel_order_violation_cost(space: EmptySpace, box: Box, container: ContainerSpec, placed_boxes: list[Box]) -> float:
    """Stage A.5, Parte 6/7 del pedido -segundo problema real encontrado
    validando contra PANEL-A/C con alturas DUPLICADAS (4 copias de cada
    una de 10 alturas): `wall_pref_cost` es una formula CONTINUA
    (fraccion de altura vs fraccion de distancia a la pared) -matematicamente
    correcta, pero con alturas muy cercanas al maximo del contenedor
    (2350mm contra una altura interior de 2393mm, solo 43mm de margen) el
    'costo ideal' de dos alturas parecidas (2350 y 2300) puede quedar mas
    cerca para la MENOR en una posicion levemente alejada de la pared,
    haciendo que el motor prefiera 2300mm ahi y deje 2 copias de 2350mm
    varadas para el final (el CENTRO del modulo, la peor posicion posible
    para una pieza alta) -confirmado con trace decision-por-decision,
    nunca un problema de la ventana de items (persistia con
    _ITEM_WINDOW=40, ventana completa) ni de la formula en si (probado
    con la formula original de core/packer.py y con la version al
    cuadrado, mismo resultado). En vez de seguir afinando una formula
    continua que no garantiza una asignacion global consistente, esta
    funcion verifica DIRECTAMENTE si el candidato mantiene el orden
    alto->bajo hacia el centro contra sus vecinos YA COLOCADOS del mismo
    lado -devuelve 0.0 si el orden se preserva, la magnitud de la
    violacion si no (mismo criterio que
    core/packing_quality.py:panel_height_order_violations, aplicado antes
    de comprometer la pieza en vez de solo medido despues)."""
    module_back_x = space.x1
    same_module = [pb for pb in placed_boxes if abs((pb.x + pb.dx) - module_back_x) < 50.0]
    half = container.width / 2

    def _dist_from_wall(y: float, dy: float) -> float:
        mid = y + dy / 2.0
        return y if mid < half else (container.width - (y + dy))

    cand_dist = _dist_from_wall(box.y, box.dy)
    cand_is_left = (box.y + box.dy / 2.0) < half
    same_side = [pb for pb in same_module if ((pb.y + pb.dy / 2.0) < half) == cand_is_left]
    row = sorted([(_dist_from_wall(pb.y, pb.dy), pb.dz) for pb in same_side] + [(cand_dist, box.dz)])
    violation = 0.0
    for (_, h_a), (_, h_b) in zip(row, row[1:]):
        if h_b > h_a + TOL:
            violation += h_b - h_a
    return violation


def _panel_wall_neighbor_mismatch_cost(
    space: EmptySpace, box: Box, container: ContainerSpec, placed_boxes: list[Box],
    pending_height_counts: dict[float, int] | None,
) -> float:
    """Stage A.5, Parte 7 -segunda mitad del fix de orden con alturas
    DUPLICADAS (4 copias de cada una de 10 alturas en PANEL-A/C):
    `_panel_order_violation_cost` por si sola no alcanza porque solo
    detecta violaciones YA COMETIDAS contra vecinos ya colocados -al
    momento de decidir el rank-2 de un lado (con un unico vecino, el de
    rank-1), tanto repetir la MISMA altura como bajar a la siguiente son
    -ambas- ordenes validos localmente, asi que no discrimina cual evita
    'varar' copias duplicadas para el final. Dos intentos previos
    documentados en el historial de esta tarea (ver commits/reporte
    Stage A.5) quedaron cortos:
    - Costo binario fijo (sin demanda pendiente): 4 -> 2 violaciones,
      pero solo evitaba abandonar una racha ya empezada -no evitaba
      SALTAR una altura entera que nunca llego a competir como vecina de
      nadie (2300mm se saltaba directo a 2200mm sin pasar por el, porque
      wall_pref_cost -formula continua- prefiere 2200mm ahi por el mismo
      efecto de compresion cerca del maximo del contenedor que origino
      todo este problema).
    - Exhaustion-only (con demanda pendiente, pero solo mirando si la
      altura del VECINO se agoto): mismo resultado, 2 violaciones -no
      alcanza si la altura correcta nunca llega a ser vecina.

    Fix final -asignacion por RANGO, no por coincidencia de fraccion
    continua: dado el vecino mas cercano a la pared, la unica altura que
    preserva el orden es la MAS ALTA entre las que TODAVIA quedan
    pendientes (`pending_height_counts`, contado en _search_best_placement
    sobre TODAS las instancias no colocadas) y no exceden la del vecino.
    Costo 0.0 si el candidato es exactamente esa altura (o si no hay
    vecino todavia, o si nada elegible queda pendiente); costo alto si
    no lo es -elimina la influencia de la formula continua sobre CUAL
    altura sigue, dejandole solo decidir la posicion fina dentro de un
    empate real (varias copias identicas)."""
    module_back_x = space.x1
    same_module = [pb for pb in placed_boxes if abs((pb.x + pb.dx) - module_back_x) < 50.0]
    half = container.width / 2

    def _dist_from_wall(y: float, dy: float) -> float:
        mid = y + dy / 2.0
        return y if mid < half else (container.width - (y + dy))

    cand_dist = _dist_from_wall(box.y, box.dy)
    cand_is_left = (box.y + box.dy / 2.0) < half
    same_side = [pb for pb in same_module if ((pb.y + pb.dy / 2.0) < half) == cand_is_left]
    wall_ward = [pb for pb in same_side if _dist_from_wall(pb.y, pb.dy) < cand_dist - TOL]
    if not wall_ward:
        return 0.0
    nearest = min(wall_ward, key=lambda pb: cand_dist - _dist_from_wall(pb.y, pb.dy))
    if abs(nearest.dz - box.dz) <= TOL:
        return 0.0
    if pending_height_counts is None:
        return 1.0
    # Segundo intento (exhaustion-only, ver historial arriba): solo
    # evitaba abandonar una racha empezada -no evitaba SALTAR una altura
    # entera que nunca llego a ser vecina de nadie (confirmado con
    # trace: 2350mm se agotaba bien de a 4, pero el salto siguiente iba
    # directo a 2200mm sin pasar por 2300mm, porque 2300 nunca competia
    # como 'vecino' -solo competia por wall_pref_cost contra 2200 para
    # esa posicion, y la formula continua igual prefería 2200 ahi).
    # Fix final: exigir que el candidato sea la altura MAS ALTA entre
    # las que TODAVIA quedan pendientes y no exceden la del vecino -eso
    # es, por construccion, la unica altura que preserva el orden
    # correcto para esta posicion (asignacion por rango, no por
    # coincidencia de fraccion continua)."""
    eligible = [h for h, cnt in pending_height_counts.items() if cnt > 0 and h <= nearest.dz + TOL]
    if not eligible:
        return 0.0
    expected = max(eligible)
    return 0.0 if abs(expected - box.dz) <= TOL else 1.0


def _score_panel_placement(
    space: EmptySpace, box: Box, container: ContainerSpec, anchor: str, current_min_x: float,
    is_natural: bool, placed_boxes: list[Box], pending_height_counts: dict[float, int] | None = None,
) -> tuple:
    """Seccion 10/11 del pedido -especifica de Panels & Fragile, NUNCA
    reusa _score_box_placement. Orden ACTUALIZADO (Stage A.5, Parte 7 del
    pedido). El orden conceptual SUGERIDO por el pedido era: 1
    factibilidad fisica, 2 continuar el modulo activo, 3 minimizar huecos
    internos, 4 balance bilateral, 5 alto-cerca-de-pared/bajo-cerca-de-
    centro, 6 hueco residual de centro, 7 progresion de profundidad -la
    Parte 7 misma pide "Validate this against actual fixtures", y
    validarlo REVELO un problema real: con gap-quality antes que
    wall_pref_cost, 40 paneles compartiendo un unico modulo ancho (PANEL-A/
    C, module_count=1) rompian el gradiente bilateral ya establecido y
    validado en la tarea Stage A original (height_order_violations 0 -> 4).
    Ese gate (seccion 18: "bilateral height rule preserved") es de
    tolerancia CERO y ya estaba pasando -gap-quality no puede competir con
    el, solo afinar DENTRO de el. Orden final, validado:

    (1) factibilidad: la resuelve la BUSQUEDA (_try_place_in_space ya
    filtro los candidatos invalidos antes de llegar aca), no un campo de
    este tuple.
    (2) depth_gap: profundidad del espacio -preferir el modulo activo
    (mas superficial) antes que abrir uno nuevo, mismo criterio de
    siempre.
    (3) balance_cost: bug real de esta tarea (ver seccion 23 del reporte
    Stage A) -mantener un frente LEFT y un frente RIGHT por separado;
    `space.y`/`space.y1` del unico espacio remanente de un modulo fresco
    SON el ancho ya comprometido por cada lado (crecen desde 0 y desde
    container.width respectivamente), asi que sirven de proxy directo sin
    requerir estado adicional. Friccion positiva por seguir engordando el
    lado que ya tiene mas ancho comprometido.
    (4) wall_pref_cost: costo de DESAJUSTE entre "que tan alta es la
    pieza" y "que tan cerca del centro cae" (bug real de esta tarea: un
    producto height_fraction*center_fraction daba costo 0 para CUALQUIER
    pieza anclada contra una pared sea cual sea su altura -no discriminaba
    cual pieza debia ganar el lugar junto a la pared). Medido sobre la
    posicion REAL de `box`, nunca sobre los limites del `space` (otro bug
    real: un espacio "leftover" toca una pared por su propio limite SEA
    CUAL SEA el anclaje elegido, dando el mismo costo a ambos lados).
    (5) excess_discontinuities/excess_gap (Parte 6, NUEVO -ver
    _panel_module_gap_metrics): cuenta y suma de huecos INTERNOS de la
    fila del modulo si esta pieza se coloca aca, DESCONTANDO el hueco mas
    grande (el residuo central esperado por diseno del llenado bilateral
    -no es un defecto, contarlo penalizaba trivialmente la distribucion
    bilateral correcta frente a apilar todo de un lado, bug real
    encontrado en test_panels_distribute_across_both_walls). Solo penaliza
    fragmentacion REAL (huecos adicionales, dispersos) DESPUES de que el
    orden de altura ya eligio la mejor pieza para esta posicion.
    (6) leftover_cost: hueco lateral remanente del espacio en si -ultimo
    desempate de "calidad de hueco", ya que (5) mide el resultado real de
    TODA la fila y es mas preciso.
    (7) anchor_tiebreak/space.z: BACK_RIGHT -preferir anchor="right" en
    empate exacto (para el PRIMER panel de un modulo fresco, anclar por la
    derecha o la izquierda del MISMO espacio inicial empata en TODOS los
    campos anteriores -sin este desempate explicito, la primera pieza
    podia caer en BACK_LEFT en vez de BACK_RIGHT_FLOOR).

    Al insertar piezas desde AMBAS paredes hacia adentro (`anchor_modes`
    incluye "right" y "left", ver _search_best_placement), el ultimo hueco
    de cada modulo queda naturalmente en el CENTRO (seccion 10: 'concentrate
    unavoidable residual space near center') sin necesitar un campo de
    'center residual gap' (item 6 del orden conceptual) por separado."""
    depth_gap = container.length - space.x1
    discontinuity_count, total_gap, largest_gap = _panel_module_gap_metrics(space, box, placed_boxes)
    # El llenado bilateral (anchor_modes=("right","left")) por diseno deja
    # EXACTAMENTE un hueco central al final de cada modulo (seccion 10:
    # "concentrate unavoidable residual space near center") -ese hueco
    # (el MAS GRANDE de la fila) cuenta como una discontinuidad valida, no
    # un defecto: ni su cantidad ni su tamano deben penalizarse. Contar
    # discontinuity_count/total_gap crudos penalizaba CUALQUIER hueco por
    # igual, premiando trivialmente "todo apilado de un solo lado, cero
    # huecos internos" sobre la distribucion bilateral correcta (que
    # siempre tiene >=1 hueco en cuanto ambos lados se tocan) -bug real
    # encontrado en test_panels_distribute_across_both_walls tras agregar
    # estos terminos (Stage A.5 Parte 6). Solo penaliza lo que sobra
    # DESPUES de descontar un hueco (el mas grande, presumiblemente el
    # central) -fragmentacion real y dispersa, nunca el residuo esperado.
    excess_discontinuities = max(0, discontinuity_count - 1)
    excess_gap = max(0.0, total_gap - largest_gap)
    # Tercer bug (balance bilateral, seccion 9 del pedido: mantener un
    # frente LEFT y un frente RIGHT por separado): con el desajuste de
    # altura ya corregido arriba, el motor seguia apilando las 10 piezas
    # del lado derecho porque nada en el costo premia EMPEZAR el lado
    # todavia vacio -extender la pila derecha ya empezada (que sigue
    # "cerca" de esa pared en distancia absoluta) competia de igual a
    # igual contra anclar al muro izquierdo virgen. `space.y` y
    # `space.y1` de el unico espacio remanente de un modulo fresco SON el
    # ancho ya comprometido por cada lado (crecen desde 0 y desde
    # container.width respectivamente a medida que cada lado avanza), asi
    # que sirven de proxy directo sin requerir estado adicional en la
    # firma de score_fn. Se compara el lado que ganaria ESTE anchor
    # contra el lado opuesto; friccion positiva por seguir engordando el
    # lado que ya tiene mas ancho comprometido.
    left_committed = space.y
    right_committed = container.width - space.y1
    balance_cost = (right_committed - left_committed) if anchor == "right" else (left_committed - right_committed)
    order_violation_cost = _panel_order_violation_cost(space, box, container, placed_boxes)
    wall_neighbor_mismatch_cost = _panel_wall_neighbor_mismatch_cost(
        space, box, container, placed_boxes, pending_height_counts
    )
    if container.height <= TOL or container.width <= TOL:
        wall_pref_cost = 0.0
    else:
        # BACK_RIGHT_FLOOR anchor audit (leccion aplicada aca, ver
        # core/packer.py:_panel_lateral_cost): la distancia a la pared debe
        # medirse sobre la posicion REAL de `box` (box.y/box.y1) -NUNCA
        # sobre los limites del `space` que la aloja. Un espacio "leftover"
        # que quedo despues de una pieza previa suele tocar una pared por
        # su propio limite (space.y=0 o space.y1=width) SEA CUAL SEA el
        # anclaje elegido dentro de el -medir sobre el espacio hacia que
        # wall_pref_cost diera el MISMO valor para anchor="right" y
        # anchor="left" en ese caso, y el llenado bilateral nunca elegia el
        # lado izquierdo (bug real: los 10 paneles de prueba terminaban
        # TODOS del mismo lado, ver seccion 23 del reporte de esta tarea).
        #
        # Segundo bug (gradiente de altura, seccion 11 del pedido): un
        # producto height_fraction*center_fraction da costo 0 para
        # CUALQUIER pieza (alta o baja) anclada justo contra una pared
        # (center_fraction=0 ahi sea cual sea su altura) -no discrimina
        # cual pieza deberia ganar el lugar junto a la pared, y el
        # desempate quedaba en manos de leftover_cost (ruido de
        # orientacion/dy, no de altura). La formula correcta es un costo
        # de DESAJUSTE entre "que tan alta es la pieza" y "que tan cerca
        # del centro cae": ideal es height_fraction + center_fraction == 1
        # (mientras mas cerca del centro, mas baja debe ser la pieza).
        # abs(height_fraction - (1 - center_fraction)) es 0 en el caso
        # ideal, crece si una pieza baja ocupa la pared (desperdicia el
        # lugar premium) o si una alta ocupa el centro.
        height_fraction = min(1.0, box.dz / container.height)
        dist_to_wall = max(0.0, min(box.y, container.width - box.max_y))
        center_fraction = min(1.0, dist_to_wall / (container.width / 2))
        wall_pref_cost = abs(height_fraction - (1.0 - center_fraction))
    leftover_cost = _sliver_penalty(space.dy - box.dy)
    anchor_tiebreak = 0 if anchor == "right" else 1
    # Orden final validado contra los fixtures (Stage A.5, Parte 7:
    # "Validate this against actual fixtures... Physical safety remains
    # absolute"): el orden conceptual sugerido ponia 'minimize internal
    # gaps' ANTES que 'tall-near-wall/short-near-center', pero validarlo
    # contra PANEL-A/C rompio el gradiente bilateral ya establecido
    # (height_order_violations 0 -> 4, con 40 paneles compartiendo un
    # unico modulo ancho, la minimizacion de hueco competia directamente
    # contra el orden de altura y ganaba). El orden de altura es un gate
    # YA VALIDADO de la tarea Stage A original (seccion 18: "bilateral
    # height rule preserved", tolerancia CERO) -gap-quality debe afinar
    # DENTRO de ese orden, nunca competir con el.  balance_cost y
    # wall_pref_cost vuelven a su prioridad original; excess_discontinuities/
    # excess_gap actuan como desempate MAS PRECISO que leftover_cost
    # (que solo ve el espacio candidato, no la fila completa del modulo)
    # pero nunca por encima del orden de altura.
    #
    # `order_violation_cost` (ver _panel_order_violation_cost): va ANTES
    # que wall_pref_cost a proposito -una formula continua de
    # coincidencia de altura puede preferir una altura "casi tan buena"
    # que en la practica DESORDENA piezas ya colocadas (confirmado con
    # PANEL-A/C, 4 copias de cada altura); verificar el orden real contra
    # los vecinos YA COLOCADOS es una garantia mas dura y debe ganarle a
    # cualquier preferencia fina de coincidencia de altura.
    return (
        depth_gap, balance_cost, order_violation_cost, wall_neighbor_mismatch_cost, wall_pref_cost,
        excess_discontinuities, excess_gap, leftover_cost, anchor_tiebreak, space.z,
    )


# ==========================================================================
# Busqueda acotada compartida (seccion 5/12/17 del pedido): para el item de
# turno, evalua una ventana de items PENDIENTES elegibles (nunca cruza un
# limite de cluster de Group/System/Delivery Sequence cuando el modo activo
# los usa como objetivo primario -seccion 5/21) x una ventana de espacios
# candidatos x orientaciones validas, y elige la mejor combinacion segun
# `score_fn`. Sin beam search de varios pasos (seccion 12: "Benchmark
# first. If beam search produces negligible benefit, do not use it" -el
# benchmark de esta tarea decide si hace falta agregarlo).
# ==========================================================================


def _cluster_key(w: WindowItem, optimization_mode: OptimizationMode):
    if optimization_mode == OptimizationMode.KEEP_GROUPS:
        return w.group or ""
    if optimization_mode == OptimizationMode.KEEP_SYSTEMS:
        return w.system or ""
    if optimization_mode == OptimizationMode.PRIORITIZE_DELIVERY:
        return w.delivery_sequence
    return None


def _orientation_passes_for(w: WindowItem) -> list:
    """Mismo patron de siempre (core/packer.py): orientaciones a 0 grados
    primero, Tilt solo en una segunda pasada si la primera no da ningun
    candidato valido -nunca se mezclan de entrada."""
    passes = [get_valid_orientations(w.dimensions, w.resolved_orientation_policy)]
    if w.allow_tilt and (w.max_tilt_angle or 0.0) > TOL:
        all_orientations = get_valid_orientations(
            w.dimensions, w.resolved_orientation_policy, allow_tilt=True, max_tilt_angle=w.max_tilt_angle
        )
        passes.append(all_orientations[len(passes[0]) :])
    return passes


def _select_space_window(spaces: list[EmptySpace], container: ContainerSpec) -> list[EmptySpace]:
    """Ventana de espacios candidatos (seccion 12/17 del pedido: 'candidate
    spaces: top N relevant free spaces') -union del FRENTE (todo espacio a
    profundidad cercana a la minima usada, ver mas abajo) Y los top-N
    restantes por VOLUMEN. Incluir tambien los de mayor volumen garantiza
    que la busqueda SIEMPRE vea la region grande disponible, sea cual sea
    su profundidad (bug real #1, ver seccion 23 de la tarea Stage A:
    items se marcaban 'sin espacio' habiendo varios metros de profundidad
    completamente libres).

    Segundo bug (Stage A.5, seccion 1-2 del pedido -causa raiz confirmada
    con trace item-por-item en BOX-B: OLD 5800mm vs V2 6750mm de longitud
    usada): la version anterior tomaba el TOP-(_SPACE_WINDOW//2) por
    prioridad de ancla como un recorte de CANTIDAD fija -con datasets
    mixtos, una vez que se acumulan >30 espacios a profundidades distintas
    pero todas cerca del frente activo (filas laterales/apiladas
    fragmentadas por formas mixtas), ese recorte por cantidad empieza a
    dejar AFUERA espacios genuinamente mas superficiales (mejor
    depth_gap) mientras el lado por-volumen sigue dejando ENTRAR espacios
    mucho mas profundos con mayor volumen -verificado en la decision 110
    de BOX-B: 21 espacios con depth_gap 3200-4000mm quedaron excluidos
    mientras uno con depth_gap 4900mm (peor) SI entraba a la ventana por
    volumen. El resultado: la busqueda a veces abre profundidad nueva
    pudiendo haber cerrado un hueco mas superficial que nunca llego a ver.

    Fix: el FRENTE (toda profundidad dentro de _MIN_USEFUL_GAP_MM de la
    minima activa) se incluye COMPLETO, sin recorte por cantidad -es
    exactamente el conjunto que decide si la longitud usada crece o no,
    nunca debe perderse por competencia de ranking. El presupuesto
    restante de _SPACE_WINDOW se llena con los de mayor volumen entre el
    resto (misma logica de siempre para no perder visibilidad de una
    region grande lejana)."""
    if len(spaces) <= _SPACE_WINDOW:
        return spaces
    min_depth_gap = min(container.length - s.x1 for s in spaces)
    frontier = [s for s in spaces if (container.length - s.x1) <= min_depth_gap + _MIN_USEFUL_GAP_MM]
    seen = {id(s) for s in frontier}
    remaining_budget = max(0, _SPACE_WINDOW - len(frontier))
    by_volume = sorted((s for s in spaces if id(s) not in seen), key=lambda s: -s.volume)[:remaining_budget]
    return frontier + by_volume


def _search_best_placement(
    head_index: int,
    instances: list[_Instance],
    placed_flags: list[bool],
    spaces: list[EmptySpace],
    container: ContainerSpec,
    placed_boxes: list[Box],
    reserved_zones: list[ReservedZone],
    clearance: float,
    weights_by_id: dict[str, float],
    total_weight: float,
    total_moment: float,
    road_weight_config,
    optimization_mode: OptimizationMode,
    score_fn,
    stats: _EvalStats,
    current_min_x: float,
    anchor_modes: tuple[str, ...] = ("right",),
    prefer_natural_orientation: bool = False,
):
    """Devuelve (idx, box, final_x, center_x, orientation_label) de la mejor combinacion
    (item pendiente elegible x espacio libre x anclaje x orientacion) segun
    `score_fn`, o None si nada de la ventana es valido -en ese caso el
    llamador decide si de verdad no hay lugar (unloaded) o si otro item
    fuera de la ventana podria entrar (no aplica en este prototipo, la
    ventana ya cubre el caso pedido en la seccion 5).

    `anchor_modes`: Boxes usan solo ("right",) -BACK_RIGHT_FLOOR canonico,
    un solo lado, sin cambios. Panels usan ("right", "left") -llenado
    bilateral (seccion 9/11 del pedido): CADA espacio candidato se evalua
    anclado a su propio borde derecho Y a su propio borde izquierdo, y
    `score_fn` (ver _score_panel_placement) decide cual conviene -nunca se
    fuerza un lado, la pieza mas alta sigue yendo a la pared, la mas baja
    al centro, en AMBOS lados."""
    head_w = instances[head_index].source
    cluster_key0 = _cluster_key(head_w, optimization_mode)

    window_indices = []
    for j in range(head_index, len(instances)):
        if placed_flags[j]:
            continue
        wj = instances[j].source
        if _cluster_key(wj, optimization_mode) != cluster_key0:
            break
        window_indices.append(j)
        if len(window_indices) >= _ITEM_WINDOW:
            break

    sorted_spaces = _select_space_window(spaces, container)

    # Stage A.5, Parte 7: cuantas copias de cada altura de Panel siguen
    # pendientes en TODA la lista de instancias (no solo la ventana) -ver
    # _panel_wall_neighbor_mismatch_cost, evita que el motor "avance" a la
    # siguiente altura mientras todavia queden copias de la actual sin
    # colocar en ningun lado. O(n) sobre instancias, barato para Panels
    # (escala de decenas, nunca cientos como Boxes)."""
    pending_height_counts: dict[float, int] = {}
    for j, other in enumerate(instances):
        if placed_flags[j]:
            continue
        ow = other.source
        if ow.item_type == ItemType.PANEL:
            key = round(ow.height, 1)
            pending_height_counts[key] = pending_height_counts.get(key, 0) + 1

    best = None  # (score, idx, box, final_x, center_x, label)
    for idx in window_indices:
        inst = instances[idx]
        w = inst.source
        if total_weight + w.weight > container.max_weight + TOL:
            continue
        natural_label = None
        if prefer_natural_orientation:
            first_pass = get_valid_orientations(w.dimensions, w.resolved_orientation_policy)
            if first_pass:
                natural_label = first_pass[0].label
        for orientations in _orientation_passes_for(w):
            found_this_pass = False
            for space in sorted_spaces:
                for anchor in anchor_modes:
                    for o in orientations:
                        result = _try_place_in_space(
                            space, o, inst, w, container, placed_boxes, reserved_zones, clearance,
                            weights_by_id, total_weight, total_moment, road_weight_config, stats, anchor,
                        )
                        if result is None:
                            continue
                        box, final_x, center_x, label = result
                        is_natural = natural_label is None or label == natural_label
                        score = score_fn(
                            space, box, container, anchor, current_min_x, is_natural, placed_boxes,
                            pending_height_counts,
                        )
                        if best is None or score < best[0]:
                            best = (score, idx, box, final_x, center_x, label)
                        found_this_pass = True
                        break  # primera orientacion valida en ESTE espacio+anclaje -nunca se reordenan orientaciones
            if found_this_pass:
                break  # no hace falta la pasada de Tilt si la de 0 grados ya dio candidatos

    if best is None:
        return None
    _, idx, box, final_x, center_x, label = best
    return idx, box, final_x, center_x, label


def _build_placed_piece(inst: _Instance, w: WindowItem, box: Box, final_x: float, label: str) -> PlacedPiece:
    """Mismos campos exactos que core/packer.py:_commit_candidate -nunca
    una segunda definicion del contrato PlacedPiece."""
    return PlacedPiece(
        id=inst.instance_id, code=w.code, description=w.description, system=w.system, group=w.group,
        weight=w.weight, stackable=w.stackable, priority=w.priority, max_stack_weight=w.max_stack_weight,
        delivery_sequence=w.delivery_sequence, boxes_inside=w.boxes_inside,
        x=final_x, y=box.y, z=box.z, dx=box.dx, dy=box.dy, dz=box.dz,
        orientation_label=label,
        source_width=w.width, source_height=w.height, source_thickness=w.thickness,
        item_type=w.item_type, orientation_policy=w.orientation_policy,
        stackable_override=w.stackable_override, orientation_override=w.orientation_override,
        allow_tilt=w.allow_tilt, max_tilt_angle=w.max_tilt_angle,
        tilt_angle=box.tilt_angle, tilt_axis=box.tilt_axis,
        base_dx=box.base_dx if box.base_dx is not None else box.dx,
        base_dy=box.base_dy if box.base_dy is not None else box.dy,
        base_dz=box.base_dz if box.base_dz is not None else box.dz,
    )


# ==========================================================================
# Loop principal compartido (seccion 2/8 del pedido: el motor de espacio
# libre y la busqueda son compartidos; `score_fn` es lo unico que cambia
# entre Boxes y Panels -ver pack_boxes_v2/pack_panels_v2 mas abajo, que
# solo fijan `score_fn` y delegan aca).
# ==========================================================================


def _run_v2_loop(
    items: list[WindowItem],
    container: ContainerSpec,
    optimization_mode: OptimizationMode,
    reserved_zones: list[ReservedZone] | None,
    clearance: float,
    strategy: str,
    preplaced: list[PlacedPiece] | None,
    score_fn,
    anchor_modes: tuple[str, ...] = ("right",),
    prefer_natural_orientation: bool = False,
) -> tuple[PackingResult, _EvalStats]:
    reserved_zones = reserved_zones or []
    reserved_ids = {p.id for p in preplaced or []}
    instances = _expand_instances(items, reserved_ids)
    instances.sort(key=build_sort_key(strategy, optimization_mode))

    placed_boxes: list[Box] = []
    placed_pieces: list[PlacedPiece] = []
    unloaded: list[UnloadedItem] = []
    weights_by_id: dict[str, float] = {}
    total_weight = 0.0
    total_moment = 0.0
    road_weight_config = container.road_weight_config
    stats = _EvalStats()

    # Espacio libre inicial: todo el Load Space, menos zonas reservadas y
    # piezas preplaced (Optimize Remaining/Locked) -mismo tratamiento que
    # una pieza colocada de siempre, cortando el espacio libre alrededor
    # (seccion 2 del pedido).
    spaces: list[EmptySpace] = [EmptySpace(0.0, 0.0, 0.0, container.length, container.width, container.height)]
    for zone in reserved_zones:
        zone_box = Box(id="__reserved__", x=zone.x, y=zone.y, z=zone.z, dx=zone.length, dy=zone.width, dz=zone.height)
        spaces = _update_spaces(spaces, zone_box)

    for p in preplaced or []:
        seed = Box(
            id=p.id, x=p.x, y=p.y, z=p.z, dx=p.dx, dy=p.dy, dz=p.dz, stackable=p.stackable,
            max_stack_weight=p.max_stack_weight, tilt_angle=p.tilt_angle, tilt_axis=p.tilt_axis,
            base_dx=p.base_dx, base_dy=p.base_dy, base_dz=p.base_dz, item_type=p.item_type,
        )
        placed_boxes.append(seed)
        placed_pieces.append(p)
        weights_by_id[p.id] = p.weight
        total_weight += p.weight
        total_moment += p.weight * piece_center_x(p.x, p.dx)
        spaces = _update_spaces(spaces, seed)

    # Envolvente de profundidad usada hasta ahora (Stage A.5, Parte 2 del
    # pedido: "used length toward the door must be a STRONG objective").
    # Se actualiza en cada commit -_score_box_placement la usa para saber
    # si un candidato EXTIENDE la envolvente (x menor a lo ya usado) o cae
    # DENTRO de lo que ya esta en uso (sin costo de profundidad nuevo).
    current_min_x = min((p.x for p in placed_pieces), default=container.length)

    placed_flags = [False] * len(instances)
    i = 0
    while i < len(instances):
        if placed_flags[i]:
            i += 1
            continue

        head_w = instances[i].source
        if not _fits_in_container_at_all(head_w, container):
            unloaded.append(
                _unloaded_item(
                    instances[i], UnloadedReason.ORIENTATION_CONFLICT,
                    "No cabe en el contenedor en ninguna orientacion valida",
                )
            )
            placed_flags[i] = True
            i += 1
            continue

        stats.space_count_samples += 1
        stats.total_spaces_samples += len(spaces)
        stats.max_spaces = max(stats.max_spaces, len(spaces))

        found = _search_best_placement(
            i, instances, placed_flags, spaces, container, placed_boxes, reserved_zones, clearance,
            weights_by_id, total_weight, total_moment, road_weight_config, optimization_mode, score_fn, stats,
            current_min_x, anchor_modes, prefer_natural_orientation,
        )

        if found is None:
            unloaded.append(
                _unloaded_item(instances[i], UnloadedReason.NO_VALID_SPACE, "Sin espacio disponible en el contenedor")
            )
            placed_flags[i] = True
            i += 1
            continue

        idx, box, final_x, center_x, label = found
        inst = instances[idx]
        w = inst.source

        placed_boxes.append(box)
        weights_by_id[box.id] = w.weight
        placed_pieces.append(_build_placed_piece(inst, w, box, final_x, label))
        total_weight += w.weight
        total_moment += w.weight * center_x
        spaces = _update_spaces(spaces, box)
        current_min_x = min(current_min_x, box.x)
        placed_flags[idx] = True
        if idx == i:
            i += 1
        # si idx != i, la pieza que le tocaba el turno a `i` sigue pendiente
        # -se reintenta en la proxima vuelta (`spaces` ya cambio).

    metrics = compute_metrics(container, placed_pieces, unloaded)
    result = PackingResult(container=container, placed=placed_pieces, unloaded=unloaded, metrics=metrics)
    return result, stats


def pack_boxes_v2(
    items: list[WindowItem],
    container: ContainerSpec,
    optimization_mode: OptimizationMode = OptimizationMode.BEST_SPACE,
    reserved_zones: list[ReservedZone] | None = None,
    clearance: float = 0.0,
    strategy: str = "highest_priority",
    preplaced: list[PlacedPiece] | None = None,
) -> tuple[PackingResult, _EvalStats]:
    """Loose Boxes/Palletized/Custom -motor EMS + seleccion acotada de
    mejor (item x orientacion x espacio), ver _score_box_placement.
    prefer_natural_orientation=True (Stage A.5, Parte 1-3): prueba la
    orientacion natural del item en TODA la ventana de espacios antes de
    competir con rotaciones alternativas -ver _search_best_placement,
    replica el orden implicito de core/packer.py en vez de dejar que
    todas las orientaciones compitan libremente por depth_increase."""
    return _run_v2_loop(
        items, container, optimization_mode, reserved_zones, clearance, strategy, preplaced, _score_box_placement,
        prefer_natural_orientation=True,
    )


def pack_panels_v2(
    items: list[WindowItem],
    container: ContainerSpec,
    optimization_mode: OptimizationMode = OptimizationMode.BEST_SPACE,
    reserved_zones: list[ReservedZone] | None = None,
    clearance: float = 0.0,
    strategy: str = "highest_priority",
    preplaced: list[PlacedPiece] | None = None,
) -> tuple[PackingResult, _EvalStats]:
    """Panels & Fragile -mismo motor EMS, politica de costo DEDICADA (ver
    _score_panel_placement, seccion 8-11 del pedido: nunca la logica
    generica de Boxes). anchor_modes=("right","left") -llenado bilateral,
    unica diferencia real de invocacion contra pack_boxes_v2."""
    return _run_v2_loop(
        items, container, optimization_mode, reserved_zones, clearance, strategy, preplaced, _score_panel_placement,
        ("right", "left"),
    )


def pack_container_v2(
    items: list[WindowItem],
    container: ContainerSpec,
    optimization_mode: OptimizationMode = OptimizationMode.BEST_SPACE,
    reserved_zones: list[ReservedZone] | None = None,
    clearance: float = 0.0,
    strategy: str = "highest_priority",
    preplaced: list[PlacedPiece] | None = None,
) -> tuple[PackingResult, _EvalStats]:
    """Punto de entrada UNICO del prototipo -despacha por item_type, mismo
    criterio de "un plan siempre es homogeneo en item_type" que ya usa
    core/packer.py (ver core/import_items.py). Nunca se usa en produccion
    todavia (STAGE A, ver docstring del modulo) -core/optimize.py sigue
    llamando exclusivamente a core/packer.py:pack_container."""
    is_panel_plan = any(item.item_type == ItemType.PANEL for item in items)
    fn = pack_panels_v2 if is_panel_plan else pack_boxes_v2
    return fn(items, container, optimization_mode, reserved_zones, clearance, strategy, preplaced)
