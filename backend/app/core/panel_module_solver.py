"""CUBOX 2.0 -- Panel Module Solver (Stage A.6, Parte B del pedido).

Prototipo DEDICADO para Panels & Fragile -reemplaza la logica generica EMS
de core/packer_v2.py (_score_panel_placement) para este Load Type. Stage
A.5 mostro que apilar mas terminos de score sobre una busqueda item-por-item
generica llega a PARIDAD con el motor de produccion pero nunca a una mejora
material de concentracion de hueco -Stage A.6 pide dejar de afinar eso y
resolver el problema real: Panels & Fragile es fundamentalmente una
seleccion 1D acotada (que subconjunto de anchos llena mejor una franja
lateral) resuelta MODULO POR MODULO, no una busqueda de mejor-candidato
pieza por pieza.

Modelo (seccion B1-B2 del pedido): en cada posicion de profundidad se abre
un MODULO -una franja transversal de piezas de pie compartiendo banda de
profundidad. Para cada modulo, se resuelve un knapsack 0/1 acotado
(maximizar ancho ocupado sin exceder el ancho lateral disponible) sobre un
pool ACOTADO de piezas pendientes elegibles, nunca 2^N (seccion B2: "Do not
brute-force"). El modulo se arma bilateralmente (alto cerca de pared, bajo
cerca de centro, ver _arrange_bilateral) y solo entonces se abre el
siguiente modulo, avanzando en profundidad.

Orientacion (seccion B9 del pedido -"ensure fixtures exercise the intended
upright operational case", NUNCA deshabilitar una orientacion global):
de las 2 orientaciones 'de pie' validas para un panel (P1-a/P1-b, ver
core/orientation.py -ambas dejan Height vertical, la unica diferencia es
si Width o Thickness cae en profundidad), este motor prefiere P1-b (Width
en Y/lateral, Thickness en X/profundidad -asi el ancho PROPIO de cada
panel es lo que se optimiza lateralmente, coherente con el ejemplo del
pedido: 'available width=2388mm, remaining panel widths: 650,620,...').
Nunca se toca orientation.py -esta preferencia es interna y exclusiva de
este solver, P1-a/P2-* siguen disponibles como fallback fisico si P1-b no
entra.

Nunca cambia: orientaciones validas (core/orientation.py sin tocar),
reglas de colision/soporte/peso/zona reservada (mismos helpers que
core/packer.py y core/packer_v2.py), el ancla BACK_RIGHT_FLOOR, la
convencion de coordenadas (puerta x=0, fondo x=length, derecha y grande,
piso z=0)."""

import math
from dataclasses import dataclass, field

from app.core.geometry import TOL, Box, within_container
from app.core.orientation import get_valid_orientations
from app.core.reasons import UnloadedReason
from app.core.reserved_zones import ReservedZone, zone_conflict_with_clearance
from app.core.road_weight import piece_center_x
from app.core.strategies import build_sort_key
from app.models.schemas import (
    ContainerSpec,
    ItemType,
    OptimizationMode,
    PackingResult,
    PlacedPiece,
    UnloadedItem,
    WindowItem,
)

# Reutiliza compute_metrics/_Instance/_expand_instances/_unloaded_item de
# packer.py -misma razon que packer_v2.py: metricas/expansion/formato de
# UnloadedItem ya testeados, nunca una segunda implementacion.
from app.core.packer import _expand_instances, _Instance, _unloaded_item, compute_metrics

# Pool acotado de candidatos elegibles por modulo (seccion B2/B8 del pedido:
# "Limit candidate pool by active Group/System/Delivery cluster" -nunca
# evaluar TODAS las piezas pendientes, el DP ya es barato pero el pool
# tambien debe quedar acotado por disciplina, no solo por costo real).
_MODULE_POOL_SIZE = 40


@dataclass
class ModuleReport:
    """Resultado de UN modulo cerrado (seccion B7 del pedido: reportar
    ancho ocupado/residual/%, cantidad de piezas, razon de cierre)."""

    depth_x1: float
    depth: float
    occupied_width: float
    residual_width: float
    fill_pct: float
    panel_count: int
    closure_reason: str


@dataclass
class ModuleSolverStats:
    modules: list[ModuleReport] = field(default_factory=list)
    subset_evaluations: int = 0
    candidate_pool_calls: int = 0


def _upright_orientations(w: WindowItem) -> list:
    """Orientaciones 'de pie' validas para este panel, P1-b PRIMERO
    (Width propio -> lateral, ver docstring del modulo), P1-a como
    alternativa de pie, P2-* al final solo si ninguna de pie entra
    (misma regla de siempre: 'physical feasibility always wins', nunca se
    fuerza una caida imposible)."""
    orientations = get_valid_orientations(w.dimensions, w.resolved_orientation_policy)
    p1b = [o for o in orientations if o.label.startswith("P1-b")]
    p1a = [o for o in orientations if o.label.startswith("P1-a")]
    rest = [o for o in orientations if not o.label.startswith("P1")]
    return p1b + p1a + rest


def _select_module_subset(capacity: int, widths: list[int]) -> list[int]:
    """Knapsack 0/1 acotado (seccion B2 del pedido: 'dynamic programming /
    knapsack on discretized mm... Do not brute-force 2^N'): devuelve los
    INDICES (en `widths`) del subconjunto que maximiza la suma de anchos
    sin exceder `capacity` (mm enteros). DP O(n*capacity) -para un pool
    acotado (_MODULE_POOL_SIZE) y un contenedor tipico (~2400mm de ancho),
    esto es un array de ~96000 booleanos, trivial. Deterministico: el
    reconstructor siempre recupera el MISMO subconjunto para la misma
    entrada (no hay aleatoriedad ni orden de iteracion de sets)."""
    n = len(widths)
    if capacity <= 0 or n == 0:
        return []
    reachable = [False] * (capacity + 1)
    reachable[0] = True
    # parent[c] = (indice del item que llevo a c, suma previa) -None si c
    # todavia no es alcanzable cuando se lo escribe.
    parent: list[tuple[int, int] | None] = [None] * (capacity + 1)
    for i, w in enumerate(widths):
        if w <= 0 or w > capacity:
            continue
        for c in range(capacity, w - 1, -1):
            if reachable[c - w] and not reachable[c]:
                reachable[c] = True
                parent[c] = (i, c - w)
    best_c = 0
    for c in range(capacity, -1, -1):
        if reachable[c]:
            best_c = c
            break
    chosen: list[int] = []
    c = best_c
    while c > 0 and parent[c] is not None:
        i, prev_c = parent[c]
        chosen.append(i)
        c = prev_c
    return chosen


def _cluster_key(w: WindowItem, optimization_mode: OptimizationMode):
    """Identico a core/packer_v2.py:_cluster_key -misma semantica de
    cluster por Optimization Mode (seccion B8 del pedido), reimplementado
    aca (no importado) para que este solver quede autonomo del motor EMS
    generico, coherente con la decision de arquitectura de la Parte C
    ('different internal solvers... Shared physical validation / geometry
    helpers should still be reused', pero la logica de seleccion en si
    puede -y aca debe- ser propia)."""
    if optimization_mode == OptimizationMode.KEEP_GROUPS:
        return w.group or ""
    if optimization_mode == OptimizationMode.KEEP_SYSTEMS:
        return w.system or ""
    if optimization_mode == OptimizationMode.PRIORITIZE_DELIVERY:
        return w.delivery_sequence
    return None


def _gather_module_pool(
    head_index: int,
    instances: list[_Instance],
    placed_flags: list[bool],
    optimization_mode: OptimizationMode,
    container: ContainerSpec,
    total_weight: float,
) -> list[tuple[int, "object", int]]:
    """Ventana acotada de piezas PENDIENTES elegibles para el modulo actual
    (seccion B2/B8 del pedido) -nunca cruza un limite de cluster de
    Group/System/Delivery Sequence cuando el modo activo lo usa como
    objetivo primario (mismo criterio que core/packer_v2.py:
    _search_best_placement). Devuelve (indice_en_instances, orientacion_
    primaria, ancho_primario_mm, orientacion_angosta_o_None,
    ancho_angosto_mm_o_None) para cada candidato que fisicamente entra de
    pie (altura <= container.height) y no excede el peso restante -el DP
    de _select_module_subset trabaja sobre esta lista, nunca sobre TODAS
    las instancias.

    Dos orientaciones por pieza (Stage B, zona reservada + P1-b: sin
    esto, una pieza mas ancha que un INTERVALO tallado -aunque quepa en
    el ancho total del contenedor- nunca se reconsideraba con su otra
    orientacion de pie, y se perdia igual que antes de tallar en
    intervalos). `primaria` es la preferida (P1-b, ancho propio ->
    lateral, ver _upright_orientations); `angosta` es la de MENOR ancho
    lateral entre las que SI entran de pie (tipicamente P1-a, grosor ->
    lateral) -el llamador la usa como plan B para intervalos angostos
    donde la primaria no entra pero la angosta si."""
    head_w = instances[head_index].source
    cluster_key0 = _cluster_key(head_w, optimization_mode)
    pool: list[tuple[int, object, int, object | None, int | None]] = []
    remaining_weight = container.max_weight - total_weight
    for j in range(head_index, len(instances)):
        if placed_flags[j]:
            continue
        wj = instances[j].source
        if _cluster_key(wj, optimization_mode) != cluster_key0:
            break
        if wj.weight > remaining_weight + TOL:
            continue
        orientations = _upright_orientations(wj)
        standing = [o for o in orientations if o.dz <= container.height + TOL]
        # Stage B, bug real: solo validar dz <= container.height no alcanza
        # -con P1-b preferida primero (Width propio -> lateral), un panel
        # mas ANCHO que el contenedor pasaba este chequeo (misma altura
        # que P1-a) pero jamas podia entrar en ningun modulo (dy > ancho
        # disponible en TODO el contenedor), y como era la unica
        # orientacion aceptada, la pieza se perdia entera (0 placed) en
        # vez de caer a P1-a (dy=thickness, que si entra). Exigir tambien
        # dy <= container.width para la primaria.
        primary = next((o for o in standing if o.dy <= container.width + TOL), None)
        if primary is None:
            continue
        narrowest = min(standing, key=lambda o: o.dy, default=None)
        narrow = narrowest if narrowest is not None and narrowest.dy < primary.dy - TOL else None
        # Bug real de produccion (URGENTE, plan real de 67 lineas con
        # anchos fraccionarios -nunca reproducido por fixtures sinteticas
        # que solo usaban mm enteros): _select_module_subset necesita mm
        # discretos para su DP, pero convertir con round() redondea HACIA
        # ABAJO la mitad de las veces -y como el knapsack busca
        # DELIBERADAMENTE el ajuste MAS AJUSTADO posible (ese es el punto
        # entero del solver, ver Stage A.6), un ajuste "perfecto" segun
        # los enteros es exactamente el caso donde CUALQUIER redondeo
        # hacia abajo hace que la suma de anchos REALES (float) supere la
        # capacidad VERDADERA del intervalo -produce colisiones fisicas
        # reales entre paneles (confirmado: 16 colisiones en un dataset de
        # 67 piezas con anchos fraccionarios). math.ceil() en vez de
        # round() garantiza que el entero SIEMPRE sea >= el ancho real,
        # asi que la cuenta del DP nunca subestima -el DP puede perder
        # como mucho <1mm de ajuste optimo por pieza, pero JAMAS deja
        # pasar una combinacion cuya suma real exceda la capacidad real."""
        pool.append((j, primary, math.ceil(primary.dy), narrow, math.ceil(narrow.dy) if narrow else None))
        if len(pool) >= _MODULE_POOL_SIZE:
            break
    return pool


def _effective_orientation_for_capacity(entry, capacity: int):
    """Dado un item del pool y la capacidad de UN intervalo, devuelve
    (orientacion, ancho) a usar -la primaria si entra, si no la angosta
    si esa entra, si no None (esta pieza no entra en este intervalo con
    ninguna orientacion de pie conocida)."""
    _, primary_o, primary_dy, narrow_o, narrow_dy = entry
    if primary_dy <= capacity:
        return primary_o, primary_dy
    if narrow_o is not None and narrow_dy <= capacity:
        return narrow_o, narrow_dy
    return None


def _usable_lateral_intervals(
    module_x1: float, module_depth: float, container: ContainerSpec,
    reserved_zones: list[ReservedZone], clearance: float,
) -> list[tuple[float, float]]:
    """Talla el ancho lateral disponible del modulo en INTERVALOS usables,
    restando cualquier zona reservada (inflada por `clearance`, mismo
    criterio que zone_conflict_with_clearance) que se solape con la banda
    de profundidad de este modulo [module_x1 - module_depth, module_x1].
    Reemplaza el fallback de ruteo (Stage B, seccion 13: 'the Module
    Solver is physically safe with Reserved Zones but... a zone collision
    blocks an entire module') -antes, CUALQUIER zona reservada activa
    hacia caer TODO el plan al motor legacy; ahora el modulo SOLO pierde
    el sub-rango lateral realmente bloqueado, igual que pide el ejemplo
    del usuario: 'usable intervals: [0..850] [1350..2388]'.

    Devuelve una lista de (lo, hi) ordenada -el llamador resuelve un
    knapsack POR INTERVALO, consumiendo el pool acotado entre intervalos
    (multiple-knapsack acotado por construccion: como mucho tantos
    intervalos como zonas reservadas activas, tipicamente 1-2 en la
    practica real). El orden prioriza el intervalo que toca la pared
    DERECHA real del contenedor (hi ~= container.width) primero, recien
    despues por ancho descendente -sin esto, una zona reservada que
    recorta justo el lado derecho podria hacer que el intervalo IZQUIERDO
    (mas grande) se procese primero, rompiendo BACK_RIGHT_FLOOR para el
    primer modulo del plan (seccion B5 del pedido: 'Do not regress the
    canonical loading anchor', requisito duro, nunca condicional a que
    haya o no una zona reservada)."""
    module_x0 = module_x1 - module_depth
    intervals = [(0.0, container.width)]
    for zone in reserved_zones:
        zone_x0, zone_x1 = zone.x, zone.x + zone.length
        if zone_x1 <= module_x0 + TOL or zone_x0 >= module_x1 - TOL:
            continue  # esta zona no se solapa con la profundidad de este modulo
        zone_lo = zone.y - clearance
        zone_hi = zone.y + zone.width + clearance
        carved: list[tuple[float, float]] = []
        for lo, hi in intervals:
            if zone_hi <= lo + TOL or zone_lo >= hi - TOL:
                carved.append((lo, hi))
                continue
            if zone_lo > lo + TOL:
                carved.append((lo, min(zone_lo, hi)))
            if zone_hi < hi - TOL:
                carved.append((max(zone_hi, lo), hi))
        intervals = carved
    intervals = [(lo, hi) for lo, hi in intervals if hi - lo > TOL]
    intervals.sort(key=lambda iv: (abs(iv[1] - container.width) > TOL, -(iv[1] - iv[0])))
    return intervals


def _arrange_bilateral(
    chosen: list[tuple[int, object, int]], lo: float, hi: float,
) -> list[tuple[int, object, float]]:
    """Arma el modulo desde AMBAS paredes hacia adentro (seccion B4/B5 del
    pedido): ordena por altura DESCENDENTE y reparte -a CADA paso- al lado
    con MENOS ancho acumulado hasta ahora (empate -> derecha, preserva el
    ancla BACK_RIGHT_FLOOR para el primer item, seccion B5: 'The first
    module must therefore originate from the right-back-floor anchor').
    Procesar en un orden GLOBAL fijo (descendente) y solo AGREGAR al final
    de la secuencia de cada lado (nunca insertar en medio) garantiza que
    cada lado queda 'alto -> bajo -> centro' sin logica aparte, sea cual
    sea la regla de asignacion -no depende de alternar por CANTIDAD.

    Segundo bug real (Stage A.6, encontrado benchmarking PANEL-M1 con
    anchos MUY dispares entre si): alternar por cantidad (k par/impar) no
    garantiza que los dos lados avancen un ANCHO similar -con piezas de
    ancho muy distinto, el lado 'derecho' podia consumir mucho mas ancho
    que el 'izquierdo' y terminar cruzando el punto medio geometrico del
    contenedor. La metrica de violaciones de orden (panel_height_order_
    violations) clasifica cada pieza por su POSICION real (y < mitad =
    'izquierda'), no por de que pared vino en la construccion -asi que una
    pieza que este solver colocaba como 'derecha, rank 3' podia terminar
    fisicamente del lado izquierdo del contenedor, mezclandose con la
    secuencia de alturas del otro lado y generando una violacion real
    (medida, no solo aparente). Balancear por ANCHO ACUMULADO (no por
    cantidad de piezas) mantiene ambos lados fisicamente simetricos
    alrededor del centro, eliminando el cruce.

    `lo`/`hi` (Stage B, tallado por zona reservada): reemplaza el viejo
    `container_width` fijo -cada INTERVALO usable tiene sus propias dos
    paredes (`lo` y `hi`, que pueden ser una pared real del contenedor O
    el borde de una zona reservada), y la logica bilateral es identica
    sea cual sea el origen de esos bordes.

    Bug real de produccion (URGENTE -reportado contra un plan real de 67
    lineas, nunca reproducido por ningun fixture sintetico porque todos
    usaban anchos en mm ENTEROS): el ancho `dy` que llega en `chosen` es
    el ENTERO redondeado que usa el knapsack para su DP
    (_select_module_subset trabaja sobre mm discretos a proposito), pero
    la pieza fisica final se construye con `o.dy` -el ancho REAL en
    punto flotante (p.ej. 583.7mm en datos de Excel reales, casi nunca
    un entero limpio). Avanzar el cursor con el entero redondeado mientras
    la caja final usa el float real desalinea a las piezas siguientes del
    mismo lado -el error se acumula pieza a pieza y termina en colision
    fisica real entre paneles (confirmado: 16 colisiones en un dataset de
    67 piezas con anchos fraccionarios). El cursor debe avanzar por el
    mismo valor EXACTO que se usa para construir la caja -`o.dy`, nunca
    el entero del knapsack (que solo existe para que el DP tenga estados
    discretos, no para gobernar geometria real)."""
    ordered = sorted(chosen, key=lambda c: -c[1].dz)
    right_cursor = hi
    left_cursor = lo
    right_width = 0.0
    left_width = 0.0
    placements: list[tuple[int, object, float]] = []
    for idx, o, _dy in ordered:
        dy = o.dy
        if right_width <= left_width:
            right_cursor -= dy
            placements.append((idx, o, right_cursor))
            right_width += dy
        else:
            placements.append((idx, o, left_cursor))
            left_cursor += dy
            left_width += dy
    return placements


def _build_placed_piece(inst: _Instance, w: WindowItem, box: Box, label: str) -> PlacedPiece:
    """Mismos campos exactos que core/packer_v2.py:_build_placed_piece
    -nunca una segunda definicion del contrato PlacedPiece."""
    return PlacedPiece(
        id=inst.instance_id, code=w.code, description=w.description, system=w.system, group=w.group,
        weight=w.weight, stackable=w.stackable, priority=w.priority, max_stack_weight=w.max_stack_weight,
        delivery_sequence=w.delivery_sequence, boxes_inside=w.boxes_inside,
        x=box.x, y=box.y, z=box.z, dx=box.dx, dy=box.dy, dz=box.dz,
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


def pack_panels_module(
    items: list[WindowItem],
    container: ContainerSpec,
    optimization_mode: OptimizationMode = OptimizationMode.BEST_SPACE,
    reserved_zones: list[ReservedZone] | None = None,
    clearance: float = 0.0,
    strategy: str = "highest_priority",
    preplaced: list[PlacedPiece] | None = None,
) -> tuple[PackingResult, ModuleSolverStats]:
    """Punto de entrada del Panel Module Solver (Stage A.6). Avanza en
    profundidad BACK -> DOOR (seccion B1), abriendo un modulo a la vez:
    junta un pool acotado de piezas pendientes elegibles (_gather_module_pool),
    resuelve el mejor subconjunto por ancho via knapsack (_select_module_subset),
    lo arma bilateralmente (_arrange_bilateral) y recien entonces avanza -un
    modulo NUNCA cierra prematuramente (seccion B7) porque el subconjunto ya
    es, por construccion, el que maximiza el ancho ocupado del pool
    considerado (ningun item restante del pool podria haber mejorado el
    residual sin exceder la capacidad, ver docstring de _select_module_subset)."""
    reserved_zones = reserved_zones or []
    reserved_ids = {p.id for p in preplaced or []}
    instances = _expand_instances(items, reserved_ids)
    instances.sort(key=build_sort_key(strategy, optimization_mode))

    placed_pieces: list[PlacedPiece] = []
    unloaded: list[UnloadedItem] = []
    total_weight = 0.0
    total_moment = 0.0
    stats = ModuleSolverStats()

    for p in preplaced or []:
        placed_pieces.append(p)
        total_weight += p.weight
        total_moment += p.weight * piece_center_x(p.x, p.dx)

    placed_flags = [False] * len(instances)
    current_x1 = container.length  # fondo: los modulos avanzan hacia la puerta (x=0)

    i = 0
    while i < len(instances):
        if placed_flags[i]:
            i += 1
            continue

        head_w = instances[i].source
        if head_w.item_type != ItemType.PANEL:
            unloaded.append(_unloaded_item(instances[i], UnloadedReason.ORIENTATION_CONFLICT, "Item no es Panel"))
            placed_flags[i] = True
            i += 1
            continue

        stats.candidate_pool_calls += 1
        pool = _gather_module_pool(i, instances, placed_flags, optimization_mode, container, total_weight)
        if not pool:
            unloaded.append(
                _unloaded_item(instances[i], UnloadedReason.NO_VALID_SPACE, "No cabe de pie o excede peso restante")
            )
            placed_flags[i] = True
            i += 1
            continue

        # Cota superior de profundidad: la mayor dx entre AMBAS
        # orientaciones posibles de cada item (primaria y angosta) -no se
        # sabe todavia cual usara cada uno hasta resolver el knapsack por
        # intervalo, asi que se reserva conservadoramente.
        module_depth = max(max(p_o.dx, (n_o.dx if n_o else p_o.dx)) for _, p_o, _, n_o, _ in pool)
        if module_depth > current_x1 + TOL:
            # Ya no queda profundidad -todo lo restante del pool (y de la
            # lista) que no entro antes queda sin cargar.
            for j, *_ in pool:
                unloaded.append(_unloaded_item(instances[j], UnloadedReason.NO_VALID_SPACE, "Sin profundidad restante"))
                placed_flags[j] = True
            i += 1
            continue

        intervals = _usable_lateral_intervals(current_x1, module_depth, container, reserved_zones, clearance)
        if not intervals:
            # La zona reservada bloquea el modulo entero en esta banda de
            # profundidad -sin ancho usable en absoluto, no hay nada que
            # tallar (caso real pero raro: una zona que cubre TODO el
            # ancho, p.ej. un pasillo mas ancho que el propio contenedor).
            unloaded.append(
                _unloaded_item(instances[i], UnloadedReason.RESERVED_ZONE_CONFLICT, "Zona reservada bloquea todo el ancho del modulo")
            )
            placed_flags[i] = True
            i += 1
            continue

        # Multiple-knapsack acotado (Stage B, seccion 13 del pedido: tallar
        # el ancho usable en intervalos en vez de perder el modulo entero
        # por una zona reservada) -un knapsack POR INTERVALO, en el orden
        # que ya prioriza BACK_RIGHT primero (ver _usable_lateral_intervals),
        # consumiendo el pool restante entre intervalos. No es optimo
        # global (un solo knapsack conjunto seria NP-dificil sin acotar
        # mas la busqueda), pero es determinista, acotado, y resuelve el
        # caso real: una zona reservada ya NO tira todo el modulo, cada
        # intervalo se llena de forma independiente y tan ajustada como
        # el pool restante lo permita.
        remaining_pool = list(pool)
        all_chosen: list[tuple[int, object, int]] = []
        all_placements: list[tuple[int, object, float]] = []
        occupied_width = 0.0
        for lo, hi in intervals:
            if not remaining_pool:
                break
            capacity = int(hi - lo)
            # Por item, la orientacion EFECTIVA para ESTE intervalo -la
            # primaria si entra, si no la angosta (Stage B: una pieza mas
            # ancha que este intervalo especifico, aunque quepa en el
            # contenedor entero, se reconsidera con su orientacion mas
            # angosta antes de descartarla)."""
            effective: list[tuple[tuple, object, int]] = []
            for entry in remaining_pool:
                choice = _effective_orientation_for_capacity(entry, capacity)
                if choice is not None:
                    effective.append((entry, choice[0], choice[1]))
            if not effective:
                continue
            widths = [dy for _, _, dy in effective]
            stats.subset_evaluations += len(effective) * capacity
            chosen_local = _select_module_subset(capacity, widths)
            if not chosen_local:
                continue
            interval_chosen_eff = [effective[k] for k in chosen_local]
            chosen_entry_ids = {id(entry) for entry, _, _ in interval_chosen_eff}
            interval_chosen = [(entry[0], o, dy) for entry, o, dy in interval_chosen_eff]
            # Red de seguridad contra el bug real de arriba (redondeo del
            # knapsack vs ancho float real): el DP decide sobre mm
            # ENTEROS, asi que la suma de anchos REALES (o.dy, float) del
            # subconjunto elegido podria -en un caso raro, con muchos
            # items cuyo redondeo se acumula en la misma direccion-
            # superar la capacidad VERDADERA (hi-lo) aunque la suma de
            # enteros haya respetado `capacity=int(hi-lo)`. Nunca emitir
            # geometria que exceda el intervalo -si pasa, esas piezas
            # quedan sin cargar en vez de arriesgar una colision o un
            # desborde silencioso (mismo principio que preferir 320/400
            # correctas a 400/400 con una invalida)."""
            actual_total_width = sum(o.dy for _, o, _ in interval_chosen)
            if actual_total_width > (hi - lo) + TOL:
                for j, _, _ in interval_chosen:
                    unloaded.append(
                        _unloaded_item(instances[j], UnloadedReason.NO_VALID_SPACE, "Redondeo excedio el ancho real del intervalo")
                    )
                    placed_flags[j] = True
                remaining_pool = [entry for entry in remaining_pool if id(entry) not in chosen_entry_ids]
                continue
            remaining_pool = [entry for entry in remaining_pool if id(entry) not in chosen_entry_ids]
            all_chosen.extend(interval_chosen)
            occupied_width += actual_total_width
            all_placements.extend(_arrange_bilateral(interval_chosen, lo, hi))

        if not all_chosen:
            unloaded.append(
                _unloaded_item(instances[i], UnloadedReason.NO_VALID_SPACE, "Ningun ancho elegible entra en ningun intervalo usable")
            )
            placed_flags[i] = True
            i += 1
            continue

        chosen = all_chosen
        placements = all_placements
        module_x1 = current_x1
        total_usable_width = sum(hi - lo for lo, hi in intervals)
        # `module_depth` (arriba) es una cota conservadora sobre TODO el
        # pool (primaria O angosta, lo que sea mayor) -correcta para el
        # chequeo de profundidad restante y para tallar intervalos, pero
        # usarla para avanzar `current_x1` desperdicia profundidad real:
        # con paneles delgados (grosor ~30-50mm) la orientacion angosta
        # (P1-a, ancho propio -> profundidad) puede tener un dx ORDENES
        # DE MAGNITUD mayor que la primaria, y la mayoria de los items
        # elegidos en la practica SI usan la primaria (delgada). Bug real
        # encontrado en test_subset_search_stays_bounded_at_scale: con
        # 400 piezas, reservar la cota conservadora para CADA modulo
        # agotaba la profundidad del contenedor a los ~90 items (91/400
        # cargadas). Avanzar por la profundidad REAL usada por las piezas
        # YA ELEGIDAS es siempre segura (nunca mayor a la cota
        # conservadora que goberno el chequeo de intervalos/profundidad
        # restante, asi que nunca genera solapamiento con el proximo
        # modulo) y recupera la profundidad no usada.
        actual_depth_used = max(o.dx for _, o, _ in chosen)

        for idx, o, y in placements:
            inst = instances[idx]
            w = inst.source
            box = Box(
                id=inst.instance_id, x=module_x1 - o.dx, y=y, z=0.0, dx=o.dx, dy=o.dy, dz=o.dz,
                stackable=w.stackable, max_stack_weight=w.max_stack_weight, item_type=w.item_type,
                tilt_angle=o.tilt_angle, tilt_axis=o.tilt_axis,
            )
            if not within_container(box, container.length, container.width, container.height, TOL):
                # UnloadedReason.OUT_OF_BOUNDS no existe (bug real de
                # produccion: AttributeError en un plan real de 67 lineas,
                # nunca disparado por ningun fixture sintetico de Stage
                # A/A.5/A.6/B -core/reasons.py nunca tuvo ese miembro).
                # NO_VALID_SPACE es el mismo que usan core/packer.py y
                # core/packer_v2.py para el caso analogo (un candidato que
                # no entra en el contenedor, ver within_container ahi).
                unloaded.append(_unloaded_item(inst, UnloadedReason.NO_VALID_SPACE, "Fuera de los limites del contenedor"))
                placed_flags[idx] = True
                continue
            if zone_conflict_with_clearance(box, reserved_zones, clearance) is not None:
                unloaded.append(_unloaded_item(inst, UnloadedReason.RESERVED_ZONE_CONFLICT, "Conflicto con zona reservada"))
                placed_flags[idx] = True
                continue
            placed_pieces.append(_build_placed_piece(inst, w, box, o.label))
            total_weight += w.weight
            total_moment += w.weight * piece_center_x(box.x, box.dx)
            placed_flags[idx] = True

        # Ancho residual/fill% relativos al ancho USABLE (suma de
        # intervalos tras tallar zonas reservadas), no a container.width
        # entero -una zona reservada activa reduce la capacidad real, eso
        # no es una perdida de "calidad de llenado" del modulo en si.
        residual_width = total_usable_width - occupied_width
        min_remaining = None
        for j in range(i, len(instances)):
            if placed_flags[j] or instances[j].source.item_type != ItemType.PANEL:
                continue
            wj = instances[j].source
            orientations = _upright_orientations(wj)
            if orientations:
                min_remaining = orientations[0].dy if min_remaining is None else min(min_remaining, orientations[0].dy)
        if min_remaining is not None and min_remaining <= residual_width + TOL:
            reason = f"optimo del pool ({len(pool)} candidatos); residual {residual_width:.0f}mm, pero ningun pendiente lo aprovecha en ESTE modulo (cluster/peso)"
        else:
            reason = f"optimo del pool ({len(pool)} candidatos); residual {residual_width:.0f}mm menor al panel pendiente mas angosto"
        stats.modules.append(
            ModuleReport(
                depth_x1=module_x1, depth=actual_depth_used, occupied_width=occupied_width,
                residual_width=residual_width,
                fill_pct=100.0 * occupied_width / total_usable_width if total_usable_width > TOL else 0.0,
                panel_count=len(chosen), closure_reason=reason,
            )
        )
        current_x1 -= actual_depth_used
        if placed_flags[i]:
            i += 1

    metrics = compute_metrics(container, placed_pieces, unloaded)
    result = PackingResult(container=container, placed=placed_pieces, unloaded=unloaded, metrics=metrics)
    return result, stats
