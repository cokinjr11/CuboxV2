"""CUBOX 2.0 -- Panel Module Solver, Stage 2: Cross-Module Residual Pocket
Reuse (pedido "PANEL MODULE SOLVER -- CROSS-MODULE RESIDUAL POCKET REUSE").

Motivacion (caso real, plan de 67 lineas): un panel D12 ("Marcos", legitimo,
no un error de datos -confirmado via el campo `description` del estado real:
"Marcos" vs "Hojas" de sus hermanos) solo tiene UNA orientacion fisicamente
valida (4521.2 x 300 x 300mm), forzando un modulo mucho mas profundo que sus
13 companeros de modulo (~2743mm). Eso deja una region fisicamente vacia y
usable (~1778mm de profundidad x ~2052mm de ancho lateral) DENTRO del rango
de profundidad que el modulo ya reservo -el Module Solver original (Stage 1,
core/panel_module_solver.py, NUNCA tocado por este archivo) la deja vacia
porque los items pendientes ya estan asignados conceptualmente a modulos
posteriores, mas cerca de la puerta.

Objetivo: usar espacio libre YA EXISTENTE antes de consumir MAS longitud
nueva del contenedor. Bajo Best Space Utilization, un candidato que entra en
un pocket existente (longitud usada += 0) debe ganarle fuertemente a un
candidato que solo puede abrir/extender un modulo nuevo (longitud usada > 0),
siempre que la geometria/orientacion/cluster/secuencia sean validas.

Arquitectura -- Stage 1 (core/panel_module_solver.py) permanece como
PANEL_MODULE_V1, sin tocar: knapsack de modulo, orden bilateral, ancla
BACK_RIGHT_FLOOR, tallado de Zona Reservada, orientaciones, clustering de
Group/System/Delivery, Optimization Modes, validacion, motor de secuencia
-nada de eso se reescribe aca, todo se REUSA via import directo de las
mismas funciones ya testeadas. Este archivo agrega Stage 2 (pipeline nuevo):

    seleccionar modulo -> armar modulo -> cerrar modulo
    -> derivar pockets residuales REALES (geometria final, no supuestos)
    -> intentar reuso acotado con Panels pendientes elegibles
    -> recalcular pockets restantes
    -> recien entonces abrir/extender el proximo modulo

La duplicacion del LOOP externo (no de los algoritmos que llama) es
deliberada: el pedido exige interceptar el punto exacto "modulo cerrado,
antes de abrir el siguiente" sin tocar core/panel_module_solver.py (para no
arriesgar el camino de produccion ya endurecido con multiples bugs reales
corregidos, ver ese archivo). PANEL_MODULE_V1 y PANEL_MODULE_POCKET_REUSE
son dos funciones separadas y comparables (seccion 25 del pedido) -esta NO
reemplaza produccion todavia; el gate de integracion se evalua por separado.

Explicitamente FUERA de alcance (seccion 1/14/26 del pedido, ver tambien
core/panel_module_solver.py): stacking vertical (Z>0) -toda pieza de pocket
sigue siendo z=0.0, igual que Stage 1 (la auditoria de soporte confirmo que
el Module Solver de produccion no tiene ninguna arquitectura de candidato
Z>0); Boxes/EMS V2; ningun cambio a Clearance ademas de lo ya soportado."""

from dataclasses import dataclass, field

from app.core.geometry import TOL, Box, within_container
from app.core.packer import _expand_instances, _unloaded_item, compute_metrics
from app.core.panel_module_solver import (
    ModuleReport,
    ModuleSolverStats,
    _arrange_bilateral,
    _build_placed_piece,
    _cluster_key,
    _effective_orientation_for_capacity,
    _gather_module_pool,
    _select_module_subset,
    _upright_orientations,
    _usable_lateral_intervals,
)
from app.core.reasons import UnloadedReason
from app.core.reserved_zones import ReservedZone, zone_conflict_with_clearance
from app.core.road_weight import piece_center_x
from app.core.sequence import (
    _boxes as _sequence_boxes,
    _delivery_conflict,
    compute_blocking_pairs,
    compute_unload_dependencies,
    detect_sequence_cycle,
)
from app.core.strategies import build_sort_key
from app.models.schemas import (
    ContainerSpec,
    ItemType,
    OperationalWarningType,
    OptimizationMode,
    PackingResult,
    PlacedPiece,
    UnloadedItem,
    WindowItem,
)

ENGINE_PANEL_MODULE_POCKET_REUSE = "PANEL_MODULE_POCKET_REUSE"

# Ventana acotada de pendientes elegibles considerada por pocket -mismo
# principio de disciplina que _MODULE_POOL_SIZE en panel_module_solver.py
# (seccion 7 del pedido: "Do not brute-force... bounded window"). Se
# reutiliza _gather_module_pool tal cual, que ya aplica este limite.
_MIN_POCKET_DIM_MM = 5.0
"""Descarta pockets degenerados/sliver de punto flotante (seccion 17:
'numerical/tolerance slivers') -no tiene relacion con el tamano de ningun
Panel real, es puramente una tolerancia geometrica."""


@dataclass(frozen=True)
class _PocketRect:
    x0: float  # borde hacia la puerta (menor X)
    x1: float  # borde hacia el fondo (mayor X)
    y0: float
    y1: float

    @property
    def depth(self) -> float:
        return self.x1 - self.x0

    @property
    def width(self) -> float:
        return self.y1 - self.y0

    @property
    def area(self) -> float:
        return self.depth * self.width


@dataclass
class PocketReport:
    """Un pocket derivado de geometria real (seccion 3/18 del pedido) y el
    resultado de intentar reusarlo -area/IDs/razon quedan pobladas incluso
    cuando el pocket NO se llena, para poder responder la seccion 19
    ('si el pocket sigue grande, reportar por que')."""

    x0: float
    x1: float
    y0: float
    y1: float
    area: float
    panel_ids: list[str] = field(default_factory=list)
    reused_area: float = 0.0
    remaining_area: float = 0.0
    reason: str = ""


@dataclass
class PocketReuseStats(ModuleSolverStats):
    pockets_generated: int = 0
    pockets_considered: int = 0
    pockets_filled: int = 0
    pocket_candidate_evaluations: int = 0
    pocket_reports: list[PocketReport] = field(default_factory=list)


def _boxes_overlap(a: Box, b: Box, tol: float = TOL) -> bool:
    return (
        a.x < b.x + b.dx - tol
        and b.x < a.x + a.dx - tol
        and a.y < b.y + b.dy - tol
        and b.y < a.y + a.dy - tol
        and a.z < b.z + b.dz - tol
        and b.z < a.z + a.dz - tol
    )


def _split_free_rect(
    free: tuple[float, float, float, float], occ: tuple[float, float, float, float]
) -> list[tuple[float, float, float, float]]:
    """Divide un rectangulo libre alrededor de UN rectangulo ocupado -patron
    MaxRects estandar de 2D bin packing (seccion 3 del pedido: derivar de
    geometria real, no de supuestos del solver): hasta 4 franjas de ancho/
    alto COMPLETO del rectangulo libre original (izquierda/derecha/abajo/
    arriba del ocupado), deliberadamente NO recortadas a la interseccion
    exacta -las franjas se solapan entre si tras procesar varios ocupados,
    pero eso es intencional: `_prune_pockets` despues se queda solo con los
    rectangulos MAXIMALES (seccion 17: 'merge only where the merged
    rectangle is genuinely free' -este approach nunca genera un rectangulo
    que no sea 100% libre, solo puede generar de mas, nunca de menos)."""
    fx0, fx1, fy0, fy1 = free
    ox0, ox1, oy0, oy1 = occ
    if ox1 <= fx0 + TOL or ox0 >= fx1 - TOL or oy1 <= fy0 + TOL or oy0 >= fy1 - TOL:
        return [free]
    pieces: list[tuple[float, float, float, float]] = []
    if ox0 > fx0 + TOL:
        pieces.append((fx0, ox0, fy0, fy1))
    if ox1 < fx1 - TOL:
        pieces.append((ox1, fx1, fy0, fy1))
    if oy0 > fy0 + TOL:
        pieces.append((fx0, fx1, fy0, oy0))
    if oy1 < fy1 - TOL:
        pieces.append((fx0, fx1, oy1, fy1))
    return pieces


def _prune_pockets(rects: list[tuple[float, float, float, float]], min_dim: float = _MIN_POCKET_DIM_MM) -> list[tuple[float, float, float, float]]:
    """Seccion 17 del pedido: descarta slivers de tolerancia numerica y
    rectangulos CONTENIDOS en otro (nunca a la inversa) -orden de entrada
    ya deterministico (viene de una lista, nunca de un set/dict, seccion
    23), y el desempate por area descendente + `sort` estable de Python
    preserva ese orden entre iguales, asi que el resultado es siempre el
    mismo para la misma entrada."""
    filtered = [r for r in rects if (r[1] - r[0]) > min_dim and (r[3] - r[2]) > min_dim]
    filtered.sort(key=lambda r: -(r[1] - r[0]) * (r[3] - r[2]))
    kept: list[tuple[float, float, float, float]] = []
    for r in filtered:
        if any(s[0] <= r[0] + TOL and s[1] >= r[1] - TOL and s[2] <= r[2] + TOL and s[3] >= r[3] - TOL for s in kept):
            continue
        kept.append(r)
    return kept


def _derive_residual_pockets(
    x_front: float,
    x_back: float,
    container: ContainerSpec,
    placed_in_band: list[PlacedPiece],
    reserved_zones: list[ReservedZone],
    clearance: float,
) -> list[_PocketRect]:
    """Pockets = envolvente del modulo [x_front, x_back] x [0, container.
    width] MENOS rectangulos ocupados por Panels ya colocados (geometria
    final real, seccion 3) MENOS Zonas Reservadas (infladas por clearance,
    mismo criterio que zone_conflict_with_clearance -seccion 15/16: nunca
    reusar a traves de una zona bloqueada, nunca inventar clearance nuevo).
    Solo XY -las piezas de este solver son siempre z=0 (seccion 14, sin
    stacking en este trabajo)."""
    free: list[tuple[float, float, float, float]] = [(x_front, x_back, 0.0, container.width)]
    occupied: list[tuple[float, float, float, float]] = [(p.x, p.x + p.dx, p.y, p.y + p.dy) for p in placed_in_band]
    for zone in reserved_zones:
        zx0, zx1 = zone.x - clearance, zone.x + zone.length + clearance
        zy0, zy1 = zone.y - clearance, zone.y + zone.width + clearance
        cx0, cx1 = max(zx0, x_front), min(zx1, x_back)
        cy0, cy1 = max(zy0, 0.0), min(zy1, container.width)
        if cx1 > cx0 + TOL and cy1 > cy0 + TOL:
            occupied.append((cx0, cx1, cy0, cy1))
    for occ in occupied:
        next_free: list[tuple[float, float, float, float]] = []
        for f in free:
            next_free.extend(_split_free_rect(f, occ))
        # Podar (degenerados + contenidos) DESPUES DE CADA rectangulo
        # ocupado, no solo al final -patron estandar de MaxRects. Sin esto,
        # la lista libre crece multiplicativamente (cada split puede
        # convertir 1 rect en hasta 4) y con ~30-40 piezas ocupadas por
        # banda (bug real de performance medido: 13.6M llamadas a
        # _split_free_rect para solo 39 bandas, algunas bandas con cientos
        # de miles de rectangulos libres redundantes sin podar) el costo
        # se vuelve no acotado -exactamente lo que la seccion 22 del pedido
        # pide evitar ('not a new combinatorial search').
        free = _prune_pockets(next_free)
    return [_PocketRect(*r) for r in free]


def _effective_orientation_for_pocket(entry, pocket_depth: float, pocket_width: int):
    """Analogo a panel_module_solver._effective_orientation_for_capacity
    (seccion 9 del pedido: 'preserve the useful behavior already added for
    Reserved Zones -if preferred orientation does not fit, try another
    legal upright orientation'), con la restriccion ADICIONAL de
    profundidad que un modulo completo no tiene (un pocket es angosto en
    X, no solo en Y). Nunca agrega una orientacion nueva -`entry` ya viene
    de _gather_module_pool sin modificar, asi que 'lying flat' solo
    aparece aca si esa funcion YA lo permitia (mismo criterio de Handling
    Rules que el resto del solver)."""
    _, primary_o, primary_dy, narrow_o, narrow_dy = entry
    if primary_o.dx <= pocket_depth + TOL and primary_dy <= pocket_width:
        return primary_o, primary_dy
    if narrow_o is not None and narrow_o.dx <= pocket_depth + TOL and narrow_dy <= pocket_width:
        return narrow_o, narrow_dy
    return None


def pack_panels_module_pocket_reuse(
    items: list[WindowItem],
    container: ContainerSpec,
    optimization_mode: OptimizationMode = OptimizationMode.BEST_SPACE,
    reserved_zones: list[ReservedZone] | None = None,
    clearance: float = 0.0,
    strategy: str = "highest_priority",
    preplaced: list[PlacedPiece] | None = None,
    start_x1: float | None = None,
) -> tuple[PackingResult, PocketReuseStats]:
    """PANEL_MODULE_POCKET_REUSE (seccion 25 del pedido) -- punto de entrada
    Stage 2. El cuerpo del loop principal es una copia deliberada de
    core/panel_module_solver.py:pack_panels_module (Stage 1, NUNCA tocado)
    hasta el punto exacto de cierre de modulo -desde ahi hasta la apertura
    del proximo modulo se inserta el paso nuevo (derivar pockets, intentar
    reuso). Toda pieza de seleccion/orden/ancla/tallado que este loop llama
    (_gather_module_pool, _select_module_subset, _usable_lateral_intervals,
    _arrange_bilateral, _effective_orientation_for_capacity) es la MISMA
    funcion importada de panel_module_solver.py, nunca una reimplementacion
    (seccion 1 del pedido: 'do NOT rewrite... Reuse the existing subset/
    knapsack machinery').

    `start_x1` (Section Coordinator Stage 2, core/section_coordinator.py):
    de donde arranca el cursor de profundidad -None (default) preserva
    EXACTO el comportamiento de siempre (arranca en container.length, el
    fondo real). El coordinador de Secciones lo usa para RETOMAR el
    empaque exactamente donde el Grupo activo anterior lo dejo (mismo
    contenedor real, nunca uno 'virtual' mas chico) -sin esto, cada
    llamada por Grupo volveria a arrancar en el fondo y colisionaria con
    lo ya colocado. Nunca cambia ninguna otra logica de esta funcion."""
    reserved_zones = reserved_zones or []
    reserved_ids = {p.id for p in preplaced or []}
    instances = _expand_instances(items, reserved_ids)
    instances.sort(key=build_sort_key(strategy, optimization_mode))

    placed_pieces: list[PlacedPiece] = []
    placed_boxes: list[Box] = []
    unloaded: list[UnloadedItem] = []
    total_weight = 0.0
    total_moment = 0.0
    stats = PocketReuseStats()

    def _piece_box(p: PlacedPiece) -> Box:
        return Box(
            id=p.id, x=p.x, y=p.y, z=p.z, dx=p.dx, dy=p.dy, dz=p.dz,
            stackable=p.stackable, max_stack_weight=p.max_stack_weight, item_type=p.item_type,
        )

    for p in preplaced or []:
        placed_pieces.append(p)
        placed_boxes.append(_piece_box(p))
        total_weight += p.weight
        total_moment += p.weight * piece_center_x(p.x, p.dx)

    placed_flags = [False] * len(instances)
    current_x1 = container.length if start_x1 is None else start_x1  # fondo (o donde retoma el coordinador de Secciones): los modulos avanzan hacia la puerta (x=0)

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

        module_depth = max(max(p_o.dx, (n_o.dx if n_o else p_o.dx)) for _, p_o, _, n_o, _ in pool)
        if module_depth > current_x1 + TOL:
            for j, *_ in pool:
                unloaded.append(_unloaded_item(instances[j], UnloadedReason.NO_VALID_SPACE, "Sin profundidad restante"))
                placed_flags[j] = True
            i += 1
            continue

        intervals = _usable_lateral_intervals(current_x1, module_depth, container, reserved_zones, clearance)
        if not intervals:
            unloaded.append(
                _unloaded_item(instances[i], UnloadedReason.RESERVED_ZONE_CONFLICT, "Zona reservada bloquea todo el ancho del modulo")
            )
            placed_flags[i] = True
            i += 1
            continue

        remaining_pool = list(pool)
        all_chosen: list[tuple[int, object, int]] = []
        all_placements: list[tuple[int, object, float]] = []
        occupied_width = 0.0
        for lo, hi in intervals:
            if not remaining_pool:
                break
            capacity = int(hi - lo)
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
        actual_depth_used = max(o.dx for _, o, _ in chosen)
        module_front = module_x1 - actual_depth_used

        for idx, o, y in placements:
            inst = instances[idx]
            w = inst.source
            box = Box(
                id=inst.instance_id, x=module_x1 - o.dx, y=y, z=0.0, dx=o.dx, dy=o.dy, dz=o.dz,
                stackable=w.stackable, max_stack_weight=w.max_stack_weight, item_type=w.item_type,
                tilt_angle=o.tilt_angle, tilt_axis=o.tilt_axis,
            )
            if not within_container(box, container.length, container.width, container.height, TOL):
                unloaded.append(_unloaded_item(inst, UnloadedReason.NO_VALID_SPACE, "Fuera de los limites del contenedor"))
                placed_flags[idx] = True
                continue
            if zone_conflict_with_clearance(box, reserved_zones, clearance) is not None:
                unloaded.append(_unloaded_item(inst, UnloadedReason.RESERVED_ZONE_CONFLICT, "Conflicto con zona reservada"))
                placed_flags[idx] = True
                continue
            placed_pieces.append(_build_placed_piece(inst, w, box, o.label))
            placed_boxes.append(box)
            total_weight += w.weight
            total_moment += w.weight * piece_center_x(box.x, box.dx)
            placed_flags[idx] = True

        residual_width = total_usable_width - occupied_width
        min_remaining = None
        for j in range(i, len(instances)):
            if placed_flags[j] or instances[j].source.item_type != ItemType.PANEL:
                continue
            orientations = _upright_orientations(instances[j].source)
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

        # ======================================================================
        # STAGE 2 -- Cross-Module Residual Pocket Reuse (secciones 2-13 del
        # pedido). Se ejecuta DESPUES de comprometer el modulo y ANTES de
        # avanzar current_x1 -exactamente el punto "commit module -> derive
        # REAL residual pockets -> attempt bounded reuse... -> only then open
        # next module" pedido en la seccion 2.
        # ======================================================================
        def _band_occupants() -> list[PlacedPiece]:
            return [
                p for p in placed_pieces
                if p.z <= TOL and p.x < module_x1 - TOL and p.x + p.dx > module_front + TOL
            ]

        pending_pockets = _derive_residual_pockets(module_front, module_x1, container, _band_occupants(), reserved_zones, clearance)
        stats.pockets_generated += len(pending_pockets)

        # Los rectangulos libres derivados por MaxRects pueden SOLAPARSE entre
        # si (por diseno, ver docstring de _split_free_rect) -si un pocket
        # anterior (procesado primero, mayor area) ya reuso parte del espacio
        # fisico que un pocket posterior tambien cubria, ese pocket posterior
        # quedaria parcial o totalmente ocupado. La red de seguridad geometrica
        # de abajo YA evita emitir una colision real en ese caso (nunca
        # geometria invalida), pero para no desperdiciar la oportunidad de
        # reuso se RECALCULA la lista de pockets desde geometria real cada vez
        # que un llenado tiene EXITO (seccion 2 del pedido: 'recompute
        # remaining pockets'). Termina siempre: cada exito consume al menos
        # una pieza pendiente (finita, no vuelve a estar disponible) y cada
        # rechazo simplemente saca ese pocket de la cola -ninguna rama repite
        # trabajo indefinidamente.
        while pending_pockets:
            pocket = pending_pockets.pop(0)
            report = PocketReport(x0=pocket.x0, x1=pocket.x1, y0=pocket.y0, y1=pocket.y1, area=pocket.area)

            next_head = next((k for k in range(i, len(instances)) if not placed_flags[k]), None)
            if next_head is None:
                report.reason = "no quedan Panels pendientes"
                stats.pocket_reports.append(report)
                continue

            pocket_pool = _gather_module_pool(next_head, instances, placed_flags, optimization_mode, container, total_weight)
            if not pocket_pool:
                report.reason = "ningun pendiente entra de pie o excede el peso restante"
                stats.pocket_reports.append(report)
                continue

            stats.pockets_considered += 1
            pocket_capacity = int(pocket.width)
            effective = []
            for entry in pocket_pool:
                choice = _effective_orientation_for_pocket(entry, pocket.depth, pocket_capacity)
                if choice is not None:
                    effective.append((entry, choice[0], choice[1]))
            stats.pocket_candidate_evaluations += len(effective)
            if not effective:
                report.reason = "ningun pendiente elegible entra en la profundidad/ancho del pocket"
                stats.pocket_reports.append(report)
                continue

            widths = [dy for _, _, dy in effective]
            chosen_local = _select_module_subset(pocket_capacity, widths)
            if not chosen_local:
                report.reason = "el knapsack del pocket no encontro subconjunto valido"
                stats.pocket_reports.append(report)
                continue

            interval_chosen_eff = [effective[k] for k in chosen_local]
            interval_chosen = [(entry[0], o, dy) for entry, o, dy in interval_chosen_eff]
            actual_total_width = sum(o.dy for _, o, _ in interval_chosen)
            if actual_total_width > pocket.width + TOL:
                # misma red de seguridad que el modulo (seccion 21 del
                # pedido): el redondeo del knapsack nunca debe generar
                # geometria que exceda la capacidad real del pocket.
                report.reason = "redondeo del knapsack excedio el ancho real del pocket -descartado por seguridad"
                stats.pocket_reports.append(report)
                continue

            # Elegibilidad de cluster (seccion 12): solo se reusa un
            # pendiente que comparte el MISMO cluster (Group/System/
            # Delivery) que el modulo DUENO del pocket -nunca cruza un
            # limite de cluster activo, cualquiera sea el modo.
            cluster_key0 = _cluster_key(head_w, optimization_mode)
            if any(_cluster_key(instances[idx].source, optimization_mode) != cluster_key0 for idx, _, _ in interval_chosen):
                report.reason = "el subconjunto optimo del pocket cruza un limite de cluster activo (Group/System/Delivery) -descartado"
                stats.pocket_reports.append(report)
                continue

            pocket_placements = _arrange_bilateral(interval_chosen, pocket.y0, pocket.y1)

            candidate_boxes: list[Box] = []
            candidate_meta: list[tuple[int, "object", Box, str]] = []
            geometry_ok = True
            for idx, o, y in pocket_placements:
                inst = instances[idx]
                w = inst.source
                box = Box(
                    id=inst.instance_id, x=pocket.x1 - o.dx, y=y, z=0.0, dx=o.dx, dy=o.dy, dz=o.dz,
                    stackable=w.stackable, max_stack_weight=w.max_stack_weight, item_type=w.item_type,
                    tilt_angle=o.tilt_angle, tilt_axis=o.tilt_axis,
                )
                if not within_container(box, container.length, container.width, container.height, TOL):
                    geometry_ok = False
                    break
                if zone_conflict_with_clearance(box, reserved_zones, clearance) is not None:
                    geometry_ok = False
                    break
                if any(_boxes_overlap(box, ob) for ob in placed_boxes) or any(_boxes_overlap(box, ob) for ob in candidate_boxes):
                    geometry_ok = False
                    break
                candidate_boxes.append(box)
                candidate_meta.append((idx, inst, box, o.label))

            if not geometry_ok:
                report.reason = "candidato geometricamente invalido (limites/zona reservada/colision real) -descartado"
                stats.pocket_reports.append(report)
                continue

            # Chequeo de secuencia/bloqueo (seccion 13): reusa app.core.
            # sequence.py, nunca duplica logica de bloqueo. Un
            # SEQUENCE_CYCLE (imposibilidad fisica) rechaza en CUALQUIER
            # modo; un NUEVO conflicto de Delivery Sequence solo rechaza
            # bajo Prioritize Delivery Sequence (accesibilidad primero
            # para ese modo; Best Space Utilization puede aceptar un
            # conflicto blando, seccion 13).
            candidate_pieces = [_build_placed_piece(inst, inst.source, box, label) for _, inst, box, label in candidate_meta]
            # Chequeo LOCAL, no global (seccion 22 del pedido: 'bounded, not
            # a new combinatorial search'): el bloqueo lateral exige overlap
            # Y-Z (compute_blocking_pairs/_yz_overlap_area) -una pieza ya
            # colocada cuyo rango Y no toca el rango Y de este pocket jamas
            # puede bloquear ni ser bloqueada por los candidatos, asi que
            # reducir el grafo a solo las piezas con overlap Y real (mas los
            # propios candidatos) da EXACTAMENTE el mismo resultado que el
            # grafo completo (placed_pieces + candidatos), a una fraccion
            # del costo -evita el O(total_placed^2) por candidato que hacia
            # que este chequeo escalara mal (medido: de <0.1s a >7s entre
            # n=40 y n=200 antes de este fix, con `total_placed` creciendo a
            # cada exito por el recalculo de pockets).
            #
            # Stage 3.1 (seccion 16 de ese pedido) agrego temporalmente una
            # ventana en X sobre este mismo filtro como mejora de
            # performance. REVERTIDO (pedido de correccion "CORRECTNESS GATE
            # -- COMPLETE-GROUP-BEFORE-PARTIAL + SEQUENCE FILTER SAFETY",
            # seccion 7/8): el bloqueo lateral (compute_blocking_pairs)
            # NO tiene cota de distancia en X -el predicado real es
            # `abox.max_x <= bbox.x` (el bloqueador esta COMPLETAMENTE
            # entre la puerta y la pieza bloqueada), sin ningun termino de
            # distancia. Contraejemplo geometrico real: una pieza a x=[0,
            # 500] y otra a x=[9000,9500] (8500mm de separacion, dentro del
            # mismo contenedor de 40ft), con el mismo rango Y/Z y NADA
            # colocado entre ambas, SI tienen una relacion de bloqueo
            # DIRECTA real (compute_blocking_pairs las devuelve como par
            # directo -ver test_long_range_dependency.py). Una ventana en X
            # de unos pocos miles de mm descarta esa arista real cuando la
            # separacion supera la ventana -no es una aproximacion, es una
            # perdida de informacion real que podria, en una configuracion
            # distinta, ocultar un ciclo genuino. El filtro SOLO-Y de
            # arriba SI es exacto (Y es condicion NECESARIA tanto para
            # bloqueo -requiere overlap Y-Z- como para soporte -requiere
            # overlap X-Y-, asi que descartar una pieza sin overlap Y jamas
            # puede omitir una arista real); la cota en X no comparte esa
            # propiedad, por eso se revierte y se vuelve a NUNCA acotar por
            # X en este chequeo de seguridad/correctitud.
            local_neighbors = [
                p for p in placed_pieces
                if p.y < pocket.y1 - TOL and p.y + p.dy > pocket.y0 + TOL
            ]
            local_placed = local_neighbors + candidate_pieces
            if detect_sequence_cycle(compute_unload_dependencies(local_placed)):
                report.reason = "crearia un ciclo de dependencia de descarga (imposibilidad fisica) -descartado"
                stats.pocket_reports.append(report)
                continue

            if optimization_mode == OptimizationMode.PRIORITIZE_DELIVERY:
                boxes_map = _sequence_boxes(local_placed)
                by_id = {p.id: p for p in local_placed}
                new_ids = {p.id for p in candidate_pieces}
                conflict = None
                for blocker_id, blocked_id in compute_blocking_pairs(local_placed, boxes_map):
                    if blocker_id in new_ids or blocked_id in new_ids:
                        conflict = _delivery_conflict(
                            blocker_id, blocked_id, by_id, OperationalWarningType.DELIVERY_SEQUENCE_CONFLICT, "blocked by"
                        )
                        if conflict is not None:
                            break
                if conflict is not None:
                    report.reason = "crearia un bloqueo de Delivery Sequence -descartado bajo Prioritize Delivery Sequence"
                    stats.pocket_reports.append(report)
                    continue

            reused_area = 0.0
            panel_ids: list[str] = []
            for (idx, inst, box, label), piece in zip(candidate_meta, candidate_pieces):
                placed_pieces.append(piece)
                placed_boxes.append(box)
                total_weight += inst.source.weight
                total_moment += inst.source.weight * piece_center_x(box.x, box.dx)
                placed_flags[idx] = True
                reused_area += box.dx * box.dy
                panel_ids.append(inst.source.code)

            report.panel_ids = panel_ids
            report.reused_area = reused_area
            report.remaining_area = max(0.0, pocket.area - reused_area)
            report.reason = f"reusado: {len(panel_ids)} pieza(s), {reused_area:.0f}mm2 de {pocket.area:.0f}mm2"
            stats.pockets_filled += 1
            stats.pocket_reports.append(report)

            # Exito -> la geometria del band cambio, recalcular desde cero en
            # vez de seguir iterando la cola vieja (ver comentario arriba).
            pending_pockets = _derive_residual_pockets(module_front, module_x1, container, _band_occupants(), reserved_zones, clearance)
            stats.pockets_generated += len(pending_pockets)

        current_x1 -= actual_depth_used
        if placed_flags[i]:
            i += 1

    metrics = compute_metrics(container, placed_pieces, unloaded)
    result = PackingResult(container=container, placed=placed_pieces, unloaded=unloaded, metrics=metrics)
    return result, stats
