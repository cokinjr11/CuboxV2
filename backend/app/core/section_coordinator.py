"""CUBOX 2.0 -- Keep Groups Together, Stage 2 + Stage 2.1: Dynamic Section
Coordinator with In-Section Group Handoff (pedidos "DYNAMIC SECTION ENGINE,
FLOOR + POCKET ONLY" y "STAGE 2.1 -- IN-SECTION GROUP HANDOFF").

Experimental path (seccion 27/Stage-2.1 del pedido: "Do NOT integrate
production blindly") -- NUNCA wireado a core/optimize.py. Comparable contra
KEEP_GROUPS actual, mismo criterio que panel_pocket_reuse.py antes de su
propia integracion.

Que es una Section aca: la banda de profundidad fisica real de UNO o MAS
modulos que pack_panels_module_pocket_reuse (Stage 1, NUNCA reescrito)
cierra -Stage 2.1 (seccion 6 del pedido de handoff: "Section object must
not equal Module forever") deja de asumir 1 Section == 1 modulo como
invariante permanente: una Section puede contener `group_phases` (mas de
un Grupo, en orden cronologico, cuando hubo handoff DENTRO de la misma
banda fisica) y expone `modules`/`levels` como listas (levels vacio en
Stage 2.1 a proposito -gancho de Stage 3, seccion 12 del pedido de
handoff: 'This task must NOT implement... Level 2, z > 0').

Handoff en-Section (seccion 1-5 del pedido de Stage 2.1): GROUP COMPLETE y
SECTION CLOSED son eventos DISTINTOS. Cuando el Grupo activo A termina su
propia llamada a pack_panels_module_pocket_reuse (que ya agoto, por su
cuenta, toda oportunidad de A -incluido su PROPIO reuso de pockets, nunca
tocado), el coordinador -y SOLO el coordinador, nunca el solver Stage 1/
Pocket Reuse en si- intenta rellenar los pockets residuales que A dejo en
CADA uno de sus propios modulos con items del SIGUIENTE Grupo ya
seleccionado (B), reusando las MISMAS funciones de bajo nivel que Pocket
Reuse usa para sus propios pockets (_derive_residual_pockets,
_effective_orientation_for_capacity/_effective_orientation_for_pocket,
_select_module_subset, _arrange_bilateral, _gather_module_pool,
_build_placed_piece) -nunca una reimplementacion del algoritmo de
seleccion/orientacion/arreglo bilateral, solo la orquestacion cruzada de
Grupos que el solver Stage 1 deliberadamente NO permite por si solo
(seccion 12 de la integracion de Pocket Reuse: un modulo nunca reusa
pockets de OTRO Grupo -esa regla sigue siendo correcta DURANTE el
procesamiento de un Grupo; el handoff solo se autoriza DESPUES de que el
Grupo dueno ya esta COMPLETO, nunca antes -seccion 5 del pedido de
handoff: 'No early leakage')."""

from dataclasses import dataclass, field

from app.core.geometry import TOL, Box, within_container
from app.core.group_selection import (
    GroupCompletionReport,
    _group_cluster_key,
    _system_cluster_key,
    _unloaded_from_excluded,
    select_complete_clusters,
    select_complete_groups,
)
from app.core.level_coordinator import MAX_LEVEL, LevelReport, try_place_level_2
from app.core.packer import _expand_instances, compute_metrics
from app.core.panel_module_solver import (
    _arrange_bilateral,
    _build_placed_piece,
    _gather_module_pool,
    _select_module_subset,
)
from app.core.panel_pocket_reuse import (
    _boxes_overlap,
    _derive_residual_pockets,
    _effective_orientation_for_pocket,
    pack_panels_module_pocket_reuse,
)
from app.core.reserved_zones import ReservedZone, zone_conflict_with_clearance
from app.core.road_weight import piece_center_x
from app.core.sequence import compute_unload_dependencies, detect_sequence_cycle
from app.models.schemas import ContainerSpec, OptimizationMode, PackingResult, PlacedPiece, UnloadedItem, WindowItem

_UNGROUPED_LABEL = "(sin Grupo)"


@dataclass
class SectionReport:
    """Metrica reportable por Section (seccion 10 del pedido de Stage
    2.1). `modules`/`group_phases`/`levels` son LISTAS a proposito
    (seccion 6: 'A SectionReport should be able to contain: modules[],
    group_phases[], levels[] even if levels remains empty') -Section ya no
    asume 1:1 con un modulo del solver."""

    section_id: int
    x_back: float
    x_front: float
    depth: float
    group_phases: list[str]
    modules: list[str]
    floor_items_by_group: dict[str, list[str]]
    floor_footprint_utilization_pct: float
    residual_pockets_before_handoff: int
    residual_pockets_after_handoff: int
    largest_residual_pocket_area: float
    closed_reason: str
    levels: list = field(default_factory=list)


def _piece_box(p: PlacedPiece) -> Box:
    return Box(
        id=p.id, x=p.x, y=p.y, z=p.z, dx=p.dx, dy=p.dy, dz=p.dz,
        stackable=p.stackable, max_stack_weight=p.max_stack_weight, item_type=p.item_type,
    )


def _try_backfill_into_module(
    module_front: float,
    module_back: float,
    candidate_instances: list,
    candidate_flags: list[bool],
    all_placed: list[PlacedPiece],
    all_placed_boxes: list[Box],
    container: ContainerSpec,
    reserved_zones: list[ReservedZone],
    clearance: float,
    optimization_mode: OptimizationMode,
    total_weight: float,
) -> list[PlacedPiece]:
    """Rellena los pockets residuales de UN modulo YA CERRADO (del Grupo
    dueno, ya COMPLETO) con candidatos del SIGUIENTE Grupo seleccionado
    -mismas funciones de bajo nivel que Pocket Reuse usa para sus propios
    pockets, nunca una reimplementacion (ver docstring del modulo).
    `candidate_instances` pertenecen TODAS al mismo Grupo (B) -el chequeo
    de cluster de _gather_module_pool nunca corta antes de tiempo aca."""
    placed_in_band = [
        p for p in all_placed
        if p.z <= TOL and p.x < module_back - TOL and p.x + p.dx > module_front + TOL
    ]
    pockets = _derive_residual_pockets(module_front, module_back, container, placed_in_band, reserved_zones, clearance)
    newly_placed: list[PlacedPiece] = []

    for pocket in pockets:
        next_head = next((k for k in range(len(candidate_instances)) if not candidate_flags[k]), None)
        if next_head is None:
            break
        pool = _gather_module_pool(next_head, candidate_instances, candidate_flags, optimization_mode, container, total_weight)
        if not pool:
            continue
        pocket_capacity = int(pocket.width)
        effective = []
        for entry in pool:
            choice = _effective_orientation_for_pocket(entry, pocket.depth, pocket_capacity)
            if choice is not None:
                effective.append((entry, choice[0], choice[1]))
        if not effective:
            continue
        widths = [dy for _, _, dy in effective]
        chosen_local = _select_module_subset(pocket_capacity, widths)
        if not chosen_local:
            continue
        interval_chosen_eff = [effective[k] for k in chosen_local]
        interval_chosen = [(entry[0], o, dy) for entry, o, dy in interval_chosen_eff]
        actual_total_width = sum(o.dy for _, o, _ in interval_chosen)
        if actual_total_width > pocket.width + TOL:
            continue  # misma red de seguridad que Pocket Reuse: nunca emitir geometria que exceda el pocket real

        pocket_placements = _arrange_bilateral(interval_chosen, pocket.y0, pocket.y1)
        candidate_boxes: list[Box] = []
        candidate_meta = []
        geometry_ok = True
        for idx, o, y in pocket_placements:
            inst = candidate_instances[idx]
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
            if any(_boxes_overlap(box, ob) for ob in all_placed_boxes) or any(_boxes_overlap(box, ob) for ob in candidate_boxes):
                geometry_ok = False
                break
            candidate_boxes.append(box)
            candidate_meta.append((idx, inst, box, o.label))
        if not geometry_ok:
            continue

        candidate_pieces = [_build_placed_piece(inst, inst.source, box, label) for _, inst, box, label in candidate_meta]
        # Chequeo LOCAL en Y (seccion 16 del pedido de Stage 3.1 agrego
        # temporalmente una ventana en X aca tambien, por el mismo
        # argumento de performance -- REVERTIDA por el pedido de correccion
        # "CORRECTNESS GATE -- SEQUENCE FILTER SAFETY" (seccion 7/8): el
        # bloqueo lateral (compute_blocking_pairs, core/sequence.py) no
        # tiene cota de distancia en X -el predicado es `abox.max_x <=
        # bbox.x` mas overlap Y-Z, sin termino de distancia- asi que una
        # ventana en X puede descartar una arista de bloqueo real entre
        # piezas separadas por miles de mm con nada colocado entre ambas
        # (ver panel_pocket_reuse.py, mismo argumento exacto, y
        # test_long_range_dependency.py). El filtro SOLO-Y SI es exacto: Y
        # es condicion necesaria tanto para bloqueo (overlap Y-Z) como para
        # soporte (overlap X-Y), asi que nunca omite una arista real -solo
        # la cota en X carecia de esa garantia, por eso se revierte solo
        # esa parte y se preserva el filtro por Y.
        local_neighbors = [
            p for p in all_placed
            if p.y < pocket.y1 - TOL and p.y + p.dy > pocket.y0 + TOL
        ]
        if detect_sequence_cycle(compute_unload_dependencies(local_neighbors + candidate_pieces)):
            continue  # imposibilidad fisica de secuencia -descartado, nunca comprometido

        for (idx, _inst, box, _label), piece in zip(candidate_meta, candidate_pieces):
            newly_placed.append(piece)
            all_placed_boxes.append(box)
            candidate_flags[idx] = True

    return newly_placed


def section_keep_groups_pocket_reuse(
    items: list[WindowItem],
    container: ContainerSpec,
    reserved_zones: list[ReservedZone] | None = None,
    clearance: float = 0.0,
    preplaced: list[PlacedPiece] | None = None,
    strategy: str = "group_and_size",
) -> tuple[PackingResult, list[SectionReport], list[GroupCompletionReport], dict[str, tuple[int, float]]]:
    """SECTION_KEEP_GROUPS_STAGE2 (+ handoff Stage 2.1) -- punto de entrada
    experimental. Devuelve (result, sections, group_reports)."""
    reserved_zones = reserved_zones or []

    def _pack_fn(items_, container_, mode_, zones_, clearance_, strategy_, preplaced_):
        result_, _stats_ = pack_panels_module_pocket_reuse(items_, container_, mode_, zones_, clearance_, strategy_, preplaced_)
        return result_

    selected_items, group_reports, excluded_items, accepted_order = select_complete_groups(
        items, container, _pack_fn, reserved_zones, clearance, preplaced, verification_strategy=strategy,
    )

    ungrouped = [w for w in selected_items if not (w.group or "").strip()]
    grouped: dict[str, list[WindowItem]] = {}
    for w in selected_items:
        key = (w.group or "").strip()
        if key:
            grouped.setdefault(key, []).append(w)

    all_placed: list[PlacedPiece] = list(preplaced or [])
    all_unloaded: list[UnloadedItem] = []
    sections: list[SectionReport] = []
    section_counter = 1
    current_x1 = container.length
    # Stage 3 hook (seccion 16 del pedido de correccion Stage 2.2):
    # group -> (completion_section_id, completion_frontier_x) -- la
    # Section/limite X mas cercano a la puerta que ese Grupo alcanzo.
    # Stage 3 buscara superficies de soporte z>0 SOLO en o mas alla de
    # este frontier, nunca detras (misma regla, aplicada verticalmente).
    completion_frontiers: dict[str, tuple[int, float]] = {}

    def _section_id_for_module(m_front: float, m_back: float) -> int:
        for s in sections:
            if abs(s.x_front - m_front) < TOL and abs(s.x_back - m_back) < TOL:
                return s.section_id
        return -1

    for i, group_key in enumerate(accepted_order):
        group_items = grouped.get(group_key, [])
        if not group_items:
            continue

        result, stats = pack_panels_module_pocket_reuse(
            group_items, container, OptimizationMode.KEEP_GROUPS, reserved_zones, clearance,
            strategy, all_placed, start_x1=current_x1,
        )
        prior_ids = {p.id for p in all_placed}
        newly_placed = [p for p in result.placed if p.id not in prior_ids]
        all_placed = result.placed
        all_unloaded = all_unloaded + result.unloaded

        # Reporta una Section por cada modulo que ESTA llamada cerro (aun
        # 1:1 con el modulo en este punto -el handoff, mas abajo, puede
        # EXTENDER una Section ya reportada con un segundo group_phase,
        # nunca crear una nueva por el mismo rango fisico).
        for m in stats.modules:
            front = m.depth_x1 - m.depth
            section_items = [p for p in newly_placed if front - 1e-6 <= p.x and p.x + p.dx <= m.depth_x1 + 1e-6]
            section_pockets_before = [
                pr for pr in stats.pocket_reports if front - 1e-6 <= pr.x0 and pr.x1 <= m.depth_x1 + 1e-6
            ]
            largest_pocket = max((pr.area for pr in section_pockets_before), default=0.0)
            sections.append(
                SectionReport(
                    section_id=section_counter, x_back=m.depth_x1, x_front=front, depth=m.depth,
                    group_phases=[group_key], modules=[m.closure_reason],
                    floor_items_by_group={group_key: [p.id for p in section_items]},
                    floor_footprint_utilization_pct=m.fill_pct,
                    residual_pockets_before_handoff=len(section_pockets_before),
                    residual_pockets_after_handoff=len(section_pockets_before),
                    largest_residual_pocket_area=largest_pocket,
                    closed_reason=m.closure_reason,
                )
            )
            section_counter += 1

        if stats.modules:
            last = stats.modules[-1]
            completion_frontiers[group_key] = (section_counter - 1, last.depth_x1 - last.depth)

        # ==================================================================
        # STAGE 2.1 + 2.2 -- In-Section handoff, restringido a la Section
        # de FINALIZACION de A (Chronological Completion Frontier, pedido
        # de correccion Stage 2.2): A ya agoto su PROPIA llamada -incluido
        # su propio reuso de pockets, nunca tocado- antes de que esto
        # corra. Solo se ofrece el ULTIMO modulo que A cerro (el mas
        # cercano a la puerta, `stats.modules[-1]` -la lista ya esta en
        # orden de procesamiento fondo->puerta) al SIGUIENTE Grupo ya
        # seleccionado -NUNCA los modulos anteriores de A, aunque tengan
        # mas espacio libre (bug real corregido en Stage 2.2: buscar hacia
        # atras entre TODOS los modulos de A producia una secuencia
        # espacial A -> B -> A -contradice la progresion BACK -> DOOR
        # monotonica que Keep Groups Together exige, seccion 1/2 del
        # pedido de correccion -'B must NOT travel backward and fill
        # residual pockets in [older Sections]. Those Sections are already
        # behind A's completion frontier'). Ofrecer solo UN candidato
        # (nunca saltar a un Grupo posterior -seccion 8/4 del pedido de
        # Stage 2.1, sin cambios)."""
        next_key = accepted_order[i + 1] if i + 1 < len(accepted_order) else None
        completion_module = stats.modules[-1] if stats.modules else None
        if next_key is not None and grouped.get(next_key) and completion_module is not None:
            candidate_items = grouped[next_key]
            reserved_ids = {p.id for p in all_placed}
            candidate_instances = _expand_instances(candidate_items, reserved_ids)
            candidate_flags = [False] * len(candidate_instances)
            all_placed_boxes = [_piece_box(p) for p in all_placed]

            for m in (completion_module,):
                front = m.depth_x1 - m.depth
                total_weight = sum(p.weight for p in all_placed)  # recalculado por modulo: crece con cada handoff previo
                handoff_placed = _try_backfill_into_module(
                    front, m.depth_x1, candidate_instances, candidate_flags, all_placed, all_placed_boxes,
                    container, reserved_zones, clearance, OptimizationMode.KEEP_GROUPS, total_weight,
                )
                if not handoff_placed:
                    continue
                all_placed = all_placed + handoff_placed
                section_id = _section_id_for_module(front, m.depth_x1)
                if section_id != -1:
                    section = sections[section_id - 1]
                    section.group_phases = section.group_phases + [next_key]
                    section.floor_items_by_group[next_key] = [p.id for p in handoff_placed]
                    placed_in_band_after = [
                        p for p in all_placed
                        if p.z <= TOL and p.x < m.depth_x1 - TOL and p.x + p.dx > front + TOL
                    ]
                    pockets_after = _derive_residual_pockets(front, m.depth_x1, container, placed_in_band_after, reserved_zones, clearance)
                    section.residual_pockets_after_handoff = len(pockets_after)
                    section.largest_residual_pocket_area = max((pr.area for pr in pockets_after), default=0.0)

            backfilled_ids = {id(candidate_instances[k].source) for k in range(len(candidate_instances)) if candidate_flags[k]}
            if backfilled_ids:
                grouped[next_key] = [w for w in grouped[next_key] if id(w) not in backfilled_ids]

        current_x1 = min((p.x for p in all_placed), default=current_x1)

    if ungrouped:
        result, stats = pack_panels_module_pocket_reuse(
            ungrouped, container, OptimizationMode.BEST_SPACE, reserved_zones, clearance,
            strategy, all_placed, start_x1=current_x1,
        )
        prior_ids = {p.id for p in all_placed}
        newly_placed = [p for p in result.placed if p.id not in prior_ids]
        for m in stats.modules:
            front = m.depth_x1 - m.depth
            section_items = [p for p in newly_placed if front - 1e-6 <= p.x and p.x + p.dx <= m.depth_x1 + 1e-6]
            sections.append(
                SectionReport(
                    section_id=section_counter, x_back=m.depth_x1, x_front=front, depth=m.depth,
                    group_phases=[_UNGROUPED_LABEL], modules=[m.closure_reason],
                    floor_items_by_group={_UNGROUPED_LABEL: [p.id for p in section_items]},
                    floor_footprint_utilization_pct=m.fill_pct,
                    residual_pockets_before_handoff=0, residual_pockets_after_handoff=0,
                    largest_residual_pocket_area=0.0, closed_reason=m.closure_reason,
                )
            )
            section_counter += 1
        all_placed = result.placed
        all_unloaded = all_unloaded + result.unloaded

    reserved_ids = {p.id for p in all_placed} | {u.id for u in all_unloaded}
    all_unloaded = all_unloaded + _unloaded_from_excluded(excluded_items, reserved_ids)

    metrics = compute_metrics(container, all_placed, all_unloaded)
    packing_result = PackingResult(container=container, placed=all_placed, unloaded=all_unloaded, metrics=metrics)
    return packing_result, sections, group_reports, completion_frontiers


def compute_group_contiguity_metrics(sections: list[SectionReport]) -> dict[str, dict]:
    """Metricas de contiguidad de Grupo (seccion 11 del pedido de
    correccion Stage 2.2): `group_section_span` (cuantas Sections toco ese
    Grupo), `group_transition_count` (cuantas veces aparece en la
    secuencia CRONOLOGICA aplanada, seccion 12), `group_reentry_count`
    (cuantas veces un Grupo REAPARECE despues de haber dejado de ser el
    'ultimo' Grupo activo -0 para una solucion bien formada, seccion 11:
    'A -> A -> A+B -> B -> B+C -> C: reentry = 0'). Puramente derivado de
    `sections` (geometria/orden ya decidido), nunca influye en el
    packing en si -solo reportable/debug."""
    flat = [g for s in sections for g in s.group_phases]
    spans: dict[str, set[int]] = {}
    for s in sections:
        for g in s.group_phases:
            spans.setdefault(g, set()).add(s.section_id)

    reentry_count: dict[str, int] = {g: 0 for g in spans}
    transition_count: dict[str, int] = {g: 0 for g in spans}
    seen_and_left: set[str] = set()
    prev = None
    for g in flat:
        if g != prev:
            transition_count[g] = transition_count.get(g, 0) + 1
            if g in seen_and_left:
                reentry_count[g] = reentry_count.get(g, 0) + 1
            if prev is not None and prev != g:
                seen_and_left.add(prev)
        prev = g

    return {
        g: {
            "group_section_span": len(spans[g]),
            "group_transition_count": transition_count.get(g, 0),
            "group_reentry_count": reentry_count.get(g, 0),
        }
        for g in spans
    }


def _section_floor_pieces(all_placed: list[PlacedPiece], front: float, back: float) -> list[PlacedPiece]:
    return [p for p in all_placed if p.z <= TOL and p.x < back - TOL and p.x + p.dx > front + TOL]


def _section_all_pieces(all_placed: list[PlacedPiece], front: float, back: float) -> list[PlacedPiece]:
    return [p for p in all_placed if p.x < back - TOL and p.x + p.dx > front + TOL]


def _patch_group_report(group_reports: list[GroupCompletionReport], key: str, total: int, loaded: int) -> None:
    """Actualiza en el lugar el GroupCompletionReport de `key` (viene de
    select_complete_groups, calculado ANTES de cualquier commit posterior
    de esta etapa) para que la tabla de Grupos -fuente de verdad de
    negocio, seccion 22/23 del pedido de Stage 3.1- nunca quede
    desincronizada de `all_placed`. Compartido entre el commit de un
    Grupo COMPLETABLE y el commit del Grupo parcial de cola -mismo
    calculo exacto en ambos casos, nunca dos formulas distintas."""
    status = "COMPLETE" if loaded >= total else ("PARTIAL" if loaded > 0 else "NOT_LOADED")
    for idx, r in enumerate(group_reports):
        if r.group == key:
            group_reports[idx] = GroupCompletionReport(group=key, total_units=total, loaded_units=loaded, status=status)
            return


def _try_complete_group_in_section(
    candidate_items: list[WindowItem],
    front: float,
    back: float,
    all_placed: list[PlacedPiece],
    current_x1: float,
    container: ContainerSpec,
    reserved_zones: list[ReservedZone],
    clearance: float,
    strategy: str,
    group_key: str,
    optimization_mode: OptimizationMode = OptimizationMode.KEEP_GROUPS,
) -> tuple[list[PlacedPiece], list[PlacedPiece], list[SectionReport], float, list[UnloadedItem], list[PlacedPiece]]:
    """Simula el intento COMPLETO de cargar `candidate_items` como si
    fueran el proximo Grupo activo, EXACTAMENTE con el mismo patron que
    el loop principal usa para cualquier Grupo de `accepted_order`
    (handoff a la Section de finalizacion dada -floor y Level 2- seguido
    de Sections nuevas hacia la puerta, Section-first, con lo que
    todavia falte) -- pero SIEMPRE sobre listas PROPIAS (`trial_all_
    placed`, nunca `all_placed` del llamador), asi que nunca muta estado
    compartido. Reusable tanto para EVALUAR de forma barata cuantas
    unidades entrarian de verdad (pedido de correccion "CORRECTNESS GATE",
    seccion 1/3: 'BEFORE choosing a trailing partial, prove whether
    [Group] can fit... against the exact real remaining geometry' /
    seccion 3: 'iterative complete-group search... re-evaluate against
    the NEW real remaining geometry') como para, si el llamador decide
    comprometer a este candidato (complete O parcial), aplicar el
    resultado ya calculado sin volver a correr nada -el calculo es puro/
    deterministico sobre el mismo `all_placed`/`current_x1` real, asi que
    reusar el resultado del trial para el commit real es correcto por
    construccion, nunca una inconsistencia.

    Devuelve (handoff_floor, handoff_level_pieces, new_sections,
    final_current_x1, residual_unloaded, newly_placed_all).
    `handoff_floor`/`handoff_level_pieces` son las piezas que entraron
    DENTRO de la Section (front, back) ya existente del llamador (nunca
    una Section nueva aca -esa bookkeeping le corresponde al llamador,
    que ya tiene el SectionReport real); `new_sections` son Sections
    GENUINAMENTE nuevas para lo que no entro en (front, back).
    `newly_placed_all` es la union plana de las tres fuentes -total de
    unidades realmente colocadas para `candidate_items`, para comparar
    directo contra `len(candidate_items)` (100% completo) sin tener que
    sumar a mano en cada call site."""
    trial_all_placed = list(all_placed)
    newly_placed_all: list[PlacedPiece] = []

    all_placed_boxes = [_piece_box(p) for p in trial_all_placed]
    reserved_ids = {p.id for p in trial_all_placed}
    candidate_instances = _expand_instances(candidate_items, reserved_ids)
    candidate_flags = [False] * len(candidate_instances)
    total_weight = sum(p.weight for p in trial_all_placed)

    handoff_floor = _try_backfill_into_module(
        front, back, candidate_instances, candidate_flags, trial_all_placed, all_placed_boxes,
        container, reserved_zones, clearance, optimization_mode, total_weight,
    )
    if handoff_floor:
        trial_all_placed = trial_all_placed + handoff_floor
        newly_placed_all = newly_placed_all + handoff_floor

    backfilled_ids = {id(candidate_instances[k].source) for k in range(len(candidate_instances)) if candidate_flags[k]}
    remaining_for_upper = [w for w in candidate_items if id(w) not in backfilled_ids]

    handoff_level_pieces: list[PlacedPiece] = []
    if remaining_for_upper and MAX_LEVEL >= 2:
        section_pieces_now = _section_all_pieces(trial_all_placed, front, back)
        newly_upper, remaining_after_level, _level_report = try_place_level_2(
            section_pieces_now, remaining_for_upper, trial_all_placed, container, reserved_zones, clearance,
            group_key_for=lambda _w: group_key,
        )
        if newly_upper:
            trial_all_placed = trial_all_placed + newly_upper
            newly_placed_all = newly_placed_all + newly_upper
            handoff_level_pieces = newly_upper
            remaining_for_upper = remaining_after_level

    trial_current_x1 = min((p.x for p in trial_all_placed), default=current_x1)

    new_sections: list[SectionReport] = []
    residual_unloaded: list[UnloadedItem] = []
    remaining_group_items = remaining_for_upper
    guard = 0
    while remaining_group_items and guard < 10_000:
        guard += 1
        result, stats = pack_panels_module_pocket_reuse(
            remaining_group_items, container, optimization_mode, reserved_zones, clearance,
            strategy, trial_all_placed, start_x1=trial_current_x1,
        )
        if not stats.modules:
            residual_unloaded = residual_unloaded + result.unloaded
            break
        reserved_ids_now = {p.id for p in trial_all_placed}
        mapping_instances = _expand_instances(remaining_group_items, reserved_ids_now)
        id_to_source = {inst.instance_id: id(inst.source) for inst in mapping_instances}
        source_by_obj_id = {id(w): w for w in remaining_group_items}
        first_module = stats.modules[0]
        m_front = first_module.depth_x1 - first_module.depth
        m_back = first_module.depth_x1
        prior_ids = {p.id for p in trial_all_placed}
        new_pieces = [p for p in result.placed if p.id not in prior_ids]
        section_floor_pieces = [p for p in new_pieces if m_front - 1e-6 <= p.x and p.x + p.dx <= m_back + 1e-6]
        deferred_pieces = [p for p in new_pieces if p not in section_floor_pieces]
        trial_all_placed = trial_all_placed + section_floor_pieces
        newly_placed_all = newly_placed_all + section_floor_pieces
        residual_unloaded = residual_unloaded + result.unloaded
        deferred_sources = [
            source_by_obj_id[id_to_source[p.id]] for p in deferred_pieces if p.id in id_to_source and id_to_source[p.id] in source_by_obj_id
        ]
        remaining_group_items = deferred_sources

        section_pockets_before = [pr for pr in stats.pocket_reports if m_front - 1e-6 <= pr.x0 and pr.x1 <= m_back + 1e-6]
        new_section = SectionReport(
            section_id=-1, x_back=m_back, x_front=m_front, depth=first_module.depth,
            group_phases=[group_key], modules=[first_module.closure_reason],
            floor_items_by_group={group_key: [p.id for p in section_floor_pieces]},
            floor_footprint_utilization_pct=first_module.fill_pct,
            residual_pockets_before_handoff=len(section_pockets_before),
            residual_pockets_after_handoff=len(section_pockets_before),
            largest_residual_pocket_area=max((pr.area for pr in section_pockets_before), default=0.0),
            closed_reason=first_module.closure_reason,
        )
        new_sections.append(new_section)

        if remaining_group_items and MAX_LEVEL >= 2:
            newly_upper_d, remaining_after_level_d, level_report_d = try_place_level_2(
                section_floor_pieces, remaining_group_items, trial_all_placed, container, reserved_zones, clearance,
                group_key_for=lambda _w: group_key,
            )
            if newly_upper_d:
                trial_all_placed = trial_all_placed + newly_upper_d
                newly_placed_all = newly_placed_all + newly_upper_d
                remaining_group_items = remaining_after_level_d
                new_section.levels.append(level_report_d)

        trial_current_x1 = min((p.x for p in trial_all_placed), default=trial_current_x1)

    return handoff_floor, handoff_level_pieces, new_sections, trial_current_x1, residual_unloaded, newly_placed_all


def _apply_group_completion_result(
    key: str,
    handoff_floor: list[PlacedPiece],
    handoff_level_pieces: list[PlacedPiece],
    new_sections: list[SectionReport],
    trial_current_x1: float,
    residual_unloaded: list[UnloadedItem],
    newly_placed_all: list[PlacedPiece],
    all_placed: list[PlacedPiece],
    all_unloaded: list[UnloadedItem],
    sections: list[SectionReport],
    section_counter: int,
    last_section: SectionReport,
    front: float,
    back: float,
    container: ContainerSpec,
    reserved_zones: list[ReservedZone],
    clearance: float,
) -> tuple[list[PlacedPiece], list[UnloadedItem], int, float]:
    """Aplica DE VERDAD el resultado ya calculado (puro/determinista) por
    `_try_complete_group_in_section` -- comparte el mismo bookkeeping de
    SectionReport (group_phases/floor_items_by_group/levels/pockets) que
    el loop principal de handoff usa, para un Grupo COMPLETABLE o para el
    Grupo parcial de cola por igual (mismo call site, seccion 4 del
    pedido: 'Use the actual packing objective', nunca una segunda formula
    de bookkeeping). `newly_placed_all` (handoff + Level2 + Sections
    nuevas, YA en ese orden -ver `_try_complete_group_in_section`) es la
    UNICA fuente que se suma a `all_placed`; `handoff_floor`/`handoff_
    level_pieces` solo se reusan aca para el bookkeeping de la Section ya
    existente (`last_section`), nunca se vuelven a sumar (evita el bug
    real encontrado en la primera version de este helper: las piezas de
    `new_sections` nunca llegaban a `all_placed`)."""
    all_placed = all_placed + newly_placed_all
    if handoff_floor or handoff_level_pieces:
        if key not in last_section.group_phases:
            last_section.group_phases = last_section.group_phases + [key]
        if handoff_floor:
            last_section.floor_items_by_group[key] = [p.id for p in handoff_floor]
            placed_in_band_after = _section_floor_pieces(all_placed, front, back)
            pockets_after = _derive_residual_pockets(front, back, container, placed_in_band_after, reserved_zones, clearance)
            last_section.residual_pockets_after_handoff = len(pockets_after)
            last_section.largest_residual_pocket_area = max((pr.area for pr in pockets_after), default=0.0)
        if handoff_level_pieces:
            # LevelReport detallado se descarta a proposito -- ya se
            # calculo una vez dentro del helper solo para decidir cuantas
            # piezas entraban; volver a pedirlo aca duplicaria la llamada
            # a try_place_level_2 (violaria 'no volver a correr nada', ver
            # docstring del helper). Un LevelReport resumido (conteo real
            # de piezas, ids reales) es suficiente para la tabla de
            # Section -nunca se inventa un numero, solo se omiten los
            # contadores de rechazo detallados de esa corrida ya hecha.
            last_section.levels.append(
                LevelReport(
                    level_id=2, support_z=0.0, item_ids=[p.id for p in handoff_level_pieces],
                    items_by_group={key: [p.id for p in handoff_level_pieces]}, candidates_accepted=len(handoff_level_pieces),
                )
            )

    for s in new_sections:
        s.section_id = section_counter
        section_counter += 1
        sections.append(s)

    all_unloaded = all_unloaded + residual_unloaded
    return all_placed, all_unloaded, section_counter, trial_current_x1


def section_cluster_3d(
    items: list[WindowItem],
    container: ContainerSpec,
    reserved_zones: list[ReservedZone] | None = None,
    clearance: float = 0.0,
    preplaced: list[PlacedPiece] | None = None,
    strategy: str = "group_and_size",
    cluster_key=_group_cluster_key,
    optimization_mode: OptimizationMode = OptimizationMode.KEEP_GROUPS,
) -> tuple[PackingResult, list[SectionReport], list[GroupCompletionReport], dict[str, tuple[int, float]]]:
    """STAGE_3_SECTION_3D, generalizado (pedido "KEEP SYSTEMS TOGETHER --
    REUSE THE APPROVED SECTION-FIRST 3D ENGINE", seccion 2/33: "Do NOT
    create a second Section/Level solver... Prefer a shared abstraction").
    Este es el UNICO motor Section-first 3D -Keep Groups Together y Keep
    Systems Together son la MISMA funcion con distinto `cluster_key`
    (`item.group` o `item.system`, seccion 2 del pedido) y distinto
    `optimization_mode` (afecta el `_cluster_key` interno del solver de
    Panels -ver panel_module_solver.py- para que el bilateral pairing
    dentro de un modulo tambien coincida con la politica activa). Toda la
    geometria/soporte/frontier/trailing-partial de abajo es AGNOSTICA al
    significado de `cluster_key` -nunca inspecciona `.group` ni `.system`
    directo, solo llama a `cluster_key(item)` (seccion 3 del pedido: "When
    KEEP_SYSTEMS is active: Group identity must NOT restrict spatial
    placement" -queda garantizado por construccion, ya que el resto del
    coordinador ni siquiera sabe que campo se esta usando).

    Nombres de campo preservados por compatibilidad (seccion 33 del
    pedido, "refactor conservatively"): `SectionReport.group_phases`/
    `floor_items_by_group` y `GroupCompletionReport.group` siguen
    llamandose asi aunque en modo Sistema contengan nombres de Sistema,
    no de Grupo -mismo patron ya establecido, nunca duplicar dataclasses
    casi identicas. `compute_group_contiguity_metrics` (definido mas arriba en este modulo) ya es
    agnostico por construccion (solo lee `s.group_phases` como texto
    plano) y se reusa TAL CUAL para las metricas de Sistema.

    STAGE_2_2_FLOOR_ONLY (section_keep_groups_pocket_reuse, arriba) sigue
    SIN generalizar a proposito (sigue siendo el baseline de comparacion
    de Grupo unicamente, seccion 43 del pedido original de Stage 3: nunca
    tocar ese baseline).

    Diferencia estructural clave (seccion 13 del pedido de Stage 3
    original -'the core acceptance requirement'): Stage 2/2.1/2.2
    procesaban TODAS las Sections de un cluster en una sola llamada al
    solver antes de considerar nada mas. Stage 3 pide exactamente lo
    opuesto -piso Y Level 2 de CADA Section antes de abrir la siguiente
    ('Section-first, not global-layer-first'). Para lograrlo sin tocar
    pack_panels_module_pocket_reuse (Stage 1, seccion 14 del pedido: 'Do
    NOT throw away... Add vertical use as another local Section phase'),
    este coordinador llama al solver UNA vez por Section (no una vez por
    cluster): toma SOLO el primer modulo que esa llamada cierra como la
    Section actual, reencola como 'pendiente' cualquier item que esa misma
    llamada ya habia colocado en un modulo POSTERIOR (se vuelve a intentar
    en la proxima vuelta, con el cursor de profundidad ya avanzado) -nunca
    se pierde ni se duplica una pieza, solo se pospone su intento."""
    reserved_zones = reserved_zones or []

    def _pack_fn(items_, container_, mode_, zones_, clearance_, strategy_, preplaced_):
        result_, _stats_ = pack_panels_module_pocket_reuse(items_, container_, mode_, zones_, clearance_, strategy_, preplaced_)
        return result_

    selected_items, group_reports, excluded_items, accepted_order = select_complete_clusters(
        items, container, _pack_fn, reserved_zones, clearance, preplaced, verification_strategy=strategy,
        cluster_key=cluster_key, optimization_mode=optimization_mode,
    )

    ungrouped = [w for w in selected_items if not cluster_key(w)]
    grouped: dict[str, list[WindowItem]] = {}
    for w in selected_items:
        key = cluster_key(w)
        if key:
            grouped.setdefault(key, []).append(w)

    all_placed: list[PlacedPiece] = list(preplaced or [])
    all_unloaded: list[UnloadedItem] = []
    sections: list[SectionReport] = []
    section_counter = 1
    current_x1 = container.length
    completion_frontiers: dict[str, tuple[int, float]] = {}

    for i, group_key in enumerate(accepted_order):
        remaining_group_items = list(grouped.get(group_key, []))
        group_section_ids: list[int] = []

        # ======================================================================
        # SECTION-FIRST LOOP (seccion 0/1/13 del pedido): piso + Level 2 de
        # CADA Section antes de abrir la siguiente -nunca 'todas las
        # Sections en piso, despues apilar'.
        # ======================================================================
        guard = 0
        while remaining_group_items:
            guard += 1
            if guard > 10_000:
                break  # cinturon de seguridad, nunca deberia dispararse (ver docstring: remaining_group_items decrece SIEMPRE)

            result, stats = pack_panels_module_pocket_reuse(
                remaining_group_items, container, optimization_mode, reserved_zones, clearance,
                strategy, all_placed, start_x1=current_x1,
            )
            if not stats.modules:
                all_unloaded = all_unloaded + result.unloaded
                break

            reserved_ids_now = {p.id for p in all_placed}
            mapping_instances = _expand_instances(remaining_group_items, reserved_ids_now)
            id_to_source = {inst.instance_id: id(inst.source) for inst in mapping_instances}
            source_by_obj_id = {id(w): w for w in remaining_group_items}

            first_module = stats.modules[0]
            front = first_module.depth_x1 - first_module.depth
            back = first_module.depth_x1
            prior_ids = {p.id for p in all_placed}
            new_pieces = [p for p in result.placed if p.id not in prior_ids]
            section_floor_pieces = [p for p in new_pieces if front - 1e-6 <= p.x and p.x + p.dx <= back + 1e-6]
            deferred_pieces = [p for p in new_pieces if p not in section_floor_pieces]

            all_placed = all_placed + section_floor_pieces
            all_unloaded = all_unloaded + result.unloaded
            deferred_sources = [
                source_by_obj_id[id_to_source[p.id]] for p in deferred_pieces if p.id in id_to_source and id_to_source[p.id] in source_by_obj_id
            ]
            remaining_group_items = deferred_sources

            section_id = section_counter
            section_counter += 1
            group_section_ids.append(section_id)
            section_pockets_before = [pr for pr in stats.pocket_reports if front - 1e-6 <= pr.x0 and pr.x1 <= back + 1e-6]
            section = SectionReport(
                section_id=section_id, x_back=back, x_front=front, depth=first_module.depth,
                group_phases=[group_key], modules=[first_module.closure_reason],
                floor_items_by_group={group_key: [p.id for p in section_floor_pieces]},
                floor_footprint_utilization_pct=first_module.fill_pct,
                residual_pockets_before_handoff=len(section_pockets_before),
                residual_pockets_after_handoff=len(section_pockets_before),
                largest_residual_pocket_area=max((pr.area for pr in section_pockets_before), default=0.0),
                closed_reason=first_module.closure_reason,
            )
            sections.append(section)

            # ---- LEVEL 2 (seccion 8 del pedido: el Grupo activo tiene
            # primer derecho al espacio superior de SU PROPIA Section,
            # antes de abrir la siguiente) ----
            if remaining_group_items and MAX_LEVEL >= 2:
                newly_upper, remaining_after_level, level_report = try_place_level_2(
                    section_floor_pieces, remaining_group_items, all_placed, container, reserved_zones, clearance,
                    group_key_for=lambda _w: group_key,
                )
                if newly_upper:
                    all_placed = all_placed + newly_upper
                    remaining_group_items = remaining_after_level
                    section.levels.append(level_report)

            current_x1 = min((p.x for p in all_placed), default=current_x1)

        if group_section_ids:
            completion_section_id = group_section_ids[-1]
            completion_section = sections[completion_section_id - 1]
            completion_frontiers[group_key] = (completion_section_id, completion_section.x_front)

            # ==================================================================
            # HANDOFF (Stage 2.1/2.2, extendido con Level 2 -seccion 9/30 del
            # pedido): SOLO la Section de finalizacion de A, floor Y upper.
            # ==================================================================
            next_key = accepted_order[i + 1] if i + 1 < len(accepted_order) else None
            if next_key is not None and grouped.get(next_key):
                front, back = completion_section.x_front, completion_section.x_back
                candidate_items = grouped[next_key]
                all_placed_boxes = [_piece_box(p) for p in all_placed]
                reserved_ids = {p.id for p in all_placed}
                candidate_instances = _expand_instances(candidate_items, reserved_ids)
                candidate_flags = [False] * len(candidate_instances)
                total_weight = sum(p.weight for p in all_placed)

                handoff_floor = _try_backfill_into_module(
                    front, back, candidate_instances, candidate_flags, all_placed, all_placed_boxes,
                    container, reserved_zones, clearance, optimization_mode, total_weight,
                )
                if handoff_floor:
                    all_placed = all_placed + handoff_floor
                    if next_key not in completion_section.group_phases:
                        completion_section.group_phases = completion_section.group_phases + [next_key]
                    completion_section.floor_items_by_group[next_key] = [p.id for p in handoff_floor]
                    placed_in_band_after = _section_floor_pieces(all_placed, front, back)
                    pockets_after = _derive_residual_pockets(front, back, container, placed_in_band_after, reserved_zones, clearance)
                    completion_section.residual_pockets_after_handoff = len(pockets_after)
                    completion_section.largest_residual_pocket_area = max((pr.area for pr in pockets_after), default=0.0)

                backfilled_ids = {id(candidate_instances[k].source) for k in range(len(candidate_instances)) if candidate_flags[k]}
                remaining_for_upper = [w for w in candidate_items if id(w) not in backfilled_ids]

                if remaining_for_upper and MAX_LEVEL >= 2:
                    section_pieces_now = _section_all_pieces(all_placed, front, back)
                    newly_upper_b, remaining_b, level_report_b = try_place_level_2(
                        section_pieces_now, remaining_for_upper, all_placed, container, reserved_zones, clearance,
                        group_key_for=lambda _w: next_key,
                    )
                    if newly_upper_b:
                        all_placed = all_placed + newly_upper_b
                        if next_key not in completion_section.group_phases:
                            completion_section.group_phases = completion_section.group_phases + [next_key]
                        completion_section.levels.append(level_report_b)
                        used_ids = {id(w) for w in remaining_for_upper} - {id(w) for w in remaining_b}
                        backfilled_ids = backfilled_ids | used_ids

                if backfilled_ids:
                    grouped[next_key] = [w for w in grouped[next_key] if id(w) not in backfilled_ids]

                current_x1 = min((p.x for p in all_placed), default=current_x1)

    # ==========================================================================
    # CORRECTNESS GATE -- COMPLETE-GROUP-BEFORE-PARTIAL (pedido de
    # correccion "CUBOX 2.0 -- KEEP GROUPS TOGETHER -- CORRECTNESS GATE
    # BEFORE FURTHER DEVELOPMENT", secciones 1/3): select_complete_groups
    # YA intenta un Grupo parcial de cola (core/group_selection.py), pero
    # su verificacion es PLANA -un solo repack de TODO el conjunto
    # combinado, sin `start_x1` ni handoff a una Section de finalizacion
    # especifica- y puede SUBESTIMAR lo realmente alcanzable (hallazgo
    # real, plan de 67 lineas: con esa verificacion plana, tanto INFINITE
    # WINDOWS -11 u.- como Window World -2 u.- reportaban 0 unidades
    # adicionales alcanzables). Stage 3.1 ya reforzaba esto con geometria
    # real de la Section de finalizacion, pero tenia un defecto de
    # PRIORIDAD DE NEGOCIO real (seccion 1 del pedido de correccion): el
    # candidato se elegia por CANTIDAD de unidades del handoff (`len(
    # handoff_trial) > best_handoff_units`), sin verificar primero si
    # ALGUN Grupo excluido podia llegar al 100% -eso podia elegir un Grupo
    # que rinde 6/11 PARCIAL por encima de otro que rinde 2/2 COMPLETO,
    # solo porque 6 > 2 (exactamente el caso real INFINITE WINDOWS vs
    # Window World que goitno esta correccion).
    #
    # Correccion (mantiene intacto core/group_selection.py -produccion ya
    # integrada, seccion 3 del pedido original: 'Do not weaken the
    # existing complete-Group behavior'): DOS FASES, nunca mezcladas
    # (seccion 3 del pedido de correccion: 'Do not mix these two
    # phases'):
    #
    #   FASE A -- busqueda ITERATIVA de Grupos COMPLETABLES (seccion 3):
    #   mientras algun Grupo excluido pueda alcanzar EXACTAMENTE el 100%
    #   de sus propias unidades contra la geometria real restante (handoff
    #   a la ultima Section + Level 2 + Sections nuevas hacia la puerta,
    #   el mismo patron EXACTO que el loop principal usa para cualquier
    #   Grupo activo, via _try_complete_group_in_section), se compromete
    #   DE VERDAD y se recomputa la geometria (nueva `last_section`,
    #   nuevo `current_x1`) antes de evaluar el siguiente candidato. Un
    #   Grupo que COMPLETA nunca cuenta como el Grupo parcial de la
    #   seccion 6/16 original ('partial_groups <= 1') -esta fase puede
    #   comprometer TANTOS Grupos completables como la geometria permita.
    #
    #   FASE B -- Grupo parcial de cola (seccion 5, sin cambios de
    #   semantica respecto a Stage 3.1): solo corre cuando la Fase A ya no
    #   encuentra NINGUN Grupo excluido que llegue al 100% -aca SI se
    #   elige por unidades (payload util, seccion 4/5), un solo candidato,
    #   nunca mas de uno.
    #
    # Si `select_complete_groups` YA dejo un Grupo aceptado en PARTIAL
    # (su propia verificacion plana ya eligio un parcial), este bloque
    # entero no corre -seccion 6: nunca mas de un Grupo parcial.
    # ==========================================================================
    already_has_partial = any(r.status == "PARTIAL" for r in group_reports)
    if not already_has_partial and sections:
        excluded_grouped: dict[str, list[WindowItem]] = {}
        for w in excluded_items:
            key = cluster_key(w)
            if key:
                excluded_grouped.setdefault(key, []).append(w)

        # ---- FASE A: agotar clusters COMPLETABLES, uno a la vez,
        # geometria recomputada despues de cada commit (seccion 3 del
        # pedido). ----
        while excluded_grouped:
            last_section = sections[-1]
            front, back = last_section.x_front, last_section.x_back

            completable_key = None
            completable_trial = None
            for key in sorted(excluded_grouped.keys()):
                candidate_items = excluded_grouped[key]
                trial = _try_complete_group_in_section(
                    candidate_items, front, back, all_placed, current_x1,
                    container, reserved_zones, clearance, strategy, key, optimization_mode,
                )
                _handoff_floor, _handoff_level, _new_sections, _trial_x1, _residual_unloaded, newly_placed_all = trial
                units_placed = sum(1 for p in newly_placed_all if cluster_key(p) == key)
                if units_placed >= len(candidate_items):
                    completable_key = key
                    completable_trial = trial
                    break  # cualquier cluster que complete es valido (seccion 1: completar es completar, nunca se comparan candidatos completables por tamano); orden alfabetico solo para determinismo, igual criterio que el resto de este bloque

            if completable_key is None:
                break  # ningun cluster excluido restante llega al 100% -- pasar a la Fase B

            handoff_floor, handoff_level, new_sections, trial_x1, residual_unloaded, newly_placed_all = completable_trial
            all_placed, all_unloaded, section_counter, current_x1 = _apply_group_completion_result(
                completable_key, handoff_floor, handoff_level, new_sections, trial_x1, residual_unloaded, newly_placed_all,
                all_placed, all_unloaded, sections, section_counter, last_section, front, back, container, reserved_zones, clearance,
            )
            completion_frontiers[completable_key] = (sections[-1].section_id, sections[-1].x_front)
            _patch_group_report(group_reports, completable_key, len(excluded_grouped[completable_key]), len(newly_placed_all))
            excluded_items = [w for w in excluded_items if cluster_key(w) != completable_key]
            del excluded_grouped[completable_key]

        # ---- FASE B: cluster parcial de cola, elegido por payload util,
        # SOLO cuando la Fase A ya no encontro ningun cluster completable
        # (seccion 5 del pedido: 'Once no Group can be completed: evaluate
        # each remaining Group independently... No second partial Group
        # may start afterward'). ----
        if excluded_grouped and sections:
            last_section = sections[-1]
            front, back = last_section.x_front, last_section.x_back

            best_key: str | None = None
            best_units = 0
            best_trial = None
            for key in sorted(excluded_grouped.keys()):
                candidate_items = excluded_grouped[key]
                trial = _try_complete_group_in_section(
                    candidate_items, front, back, all_placed, current_x1,
                    container, reserved_zones, clearance, strategy, key, optimization_mode,
                )
                newly_placed_all = trial[5]
                units = sum(1 for p in newly_placed_all if cluster_key(p) == key)
                if units > best_units:
                    best_units = units
                    best_key = key
                    best_trial = trial

            if best_key is not None:
                handoff_floor, handoff_level, new_sections, trial_x1, residual_unloaded, newly_placed_all = best_trial
                all_placed, all_unloaded, section_counter, current_x1 = _apply_group_completion_result(
                    best_key, handoff_floor, handoff_level, new_sections, trial_x1, residual_unloaded, newly_placed_all,
                    all_placed, all_unloaded, sections, section_counter, last_section, front, back, container, reserved_zones, clearance,
                )
                completion_frontiers[best_key] = (sections[-1].section_id, sections[-1].x_front)
                _patch_group_report(group_reports, best_key, len(excluded_grouped[best_key]), len(newly_placed_all))
                excluded_items = [w for w in excluded_items if cluster_key(w) != best_key]

    # Items sin cluster: una pasada final, solo piso (fuera de alcance de
    # Stage 3 extender Level 2 a items sin cohesion que proteger).
    if ungrouped:
        result, stats = pack_panels_module_pocket_reuse(
            ungrouped, container, OptimizationMode.BEST_SPACE, reserved_zones, clearance,
            strategy, all_placed, start_x1=current_x1,
        )
        prior_ids = {p.id for p in all_placed}
        newly_placed = [p for p in result.placed if p.id not in prior_ids]
        for m in stats.modules:
            front = m.depth_x1 - m.depth
            section_items = [p for p in newly_placed if front - 1e-6 <= p.x and p.x + p.dx <= m.depth_x1 + 1e-6]
            sections.append(
                SectionReport(
                    section_id=section_counter, x_back=m.depth_x1, x_front=front, depth=m.depth,
                    group_phases=[_UNGROUPED_LABEL], modules=[m.closure_reason],
                    floor_items_by_group={_UNGROUPED_LABEL: [p.id for p in section_items]},
                    floor_footprint_utilization_pct=m.fill_pct,
                    residual_pockets_before_handoff=0, residual_pockets_after_handoff=0,
                    largest_residual_pocket_area=0.0, closed_reason=m.closure_reason,
                )
            )
            section_counter += 1
        all_placed = result.placed
        all_unloaded = all_unloaded + result.unloaded

    reserved_ids = {p.id for p in all_placed} | {u.id for u in all_unloaded}
    all_unloaded = all_unloaded + _unloaded_from_excluded(excluded_items, reserved_ids)

    metrics = compute_metrics(container, all_placed, all_unloaded)
    packing_result = PackingResult(container=container, placed=all_placed, unloaded=all_unloaded, metrics=metrics)
    return packing_result, sections, group_reports, completion_frontiers


def section_keep_groups_pocket_reuse_stage3(
    items: list[WindowItem],
    container: ContainerSpec,
    reserved_zones: list[ReservedZone] | None = None,
    clearance: float = 0.0,
    preplaced: list[PlacedPiece] | None = None,
    strategy: str = "group_and_size",
) -> tuple[PackingResult, list[SectionReport], list[GroupCompletionReport], dict[str, tuple[int, float]]]:
    """Keep Groups Together -- envoltorio delgado de section_cluster_3d
    con cluster_key=Grupo (pedido "KEEP SYSTEMS TOGETHER", seccion 2/21/
    36: MISMO codigo exacto que corria antes de esa generalizacion, cero
    cambio de comportamiento/coordenadas para el plan real de 67 items ya
    aprobado -ver ese modulo para la logica real). Firma y contrato
    identicos a como existian antes de Keep Systems."""
    return section_cluster_3d(
        items, container, reserved_zones, clearance, preplaced, strategy,
        cluster_key=_group_cluster_key, optimization_mode=OptimizationMode.KEEP_GROUPS,
    )


def section_keep_systems_pocket_reuse_stage3(
    items: list[WindowItem],
    container: ContainerSpec,
    reserved_zones: list[ReservedZone] | None = None,
    clearance: float = 0.0,
    preplaced: list[PlacedPiece] | None = None,
    strategy: str = "group_and_size",
) -> tuple[PackingResult, list[SectionReport], list[GroupCompletionReport], dict[str, tuple[int, float]]]:
    """Keep Systems Together -- envoltorio delgado de section_cluster_3d
    con cluster_key=Sistema (pedido "KEEP SYSTEMS TOGETHER -- REUSE THE
    APPROVED SECTION-FIRST 3D ENGINE"). `SectionReport.group_phases`/
    `floor_items_by_group` y `GroupCompletionReport.group` contienen
    nombres de SISTEMA aca -nombres de campo preservados por
    compatibilidad (seccion 33 del pedido: nunca duplicar dataclasses
    casi identicas). `compute_group_contiguity_metrics` (definido mas arriba en este modulo) se
    reusa TAL CUAL para las metricas de contiguidad de Sistema -ya es
    agnostico por construccion."""
    return section_cluster_3d(
        items, container, reserved_zones, clearance, preplaced, strategy,
        cluster_key=_system_cluster_key, optimization_mode=OptimizationMode.KEEP_SYSTEMS,
    )
