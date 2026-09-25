"""Orquestador de multiples estrategias (secciones 3, 26-28 de V2).

Corre cada estrategia de core/strategies.py sobre el motor de colocacion que
corresponda por Load Type (ver _select_pack_fn, Stage B seccion 1: BOX ->
pack_boxes_v2, PANEL -> pack_panels_module_pocket_reuse (Pocket Reuse, ver
seccion 1 del pedido de integracion de Pocket Reuse) salvo Tilt/clearance
activos, PALLET/CUSTOM -> core/packer.pack_container sin cambios), puntua cada
resultado (core/scoring.py) y elige la de mejor score. Guarda hasta 3
soluciones completas (deduplicadas) para poder mostrarlas como
alternativas -esto se preserva IGUAL sea cual sea el motor elegido, nunca
se reduce a una sola pasada para los motores experimentales.

Ruteo unico (seccion 1 del pedido Stage B: "Do not duplicate routing logic
across frontend/backend endpoints"): este es el UNICO lugar que decide que
motor de colocacion se usa. api/routes.py y cualquier otro caller siguen
llamando a run_optimization() exactamente igual que antes -CERO cambio de
firma, CERO cambio semantico visible (seccion 3: "No UI Semantic Change")."""

import logging

from app.core.geometry import TOL
from app.core.group_selection import _unloaded_from_excluded, select_complete_groups
from app.core.handling_rules import resolve_effective_item
from app.core.packer import compute_metrics, pack_container
from app.core.packer_v2 import pack_boxes_v2
from app.core.panel_pocket_reuse import pack_panels_module_pocket_reuse
from app.core.reserved_zones import ReservedZone
from app.core.scoring import score_breakdown, score_solution
from app.core.strategies import STRATEGIES, STRATEGY_LABELS
from app.models.schemas import (
    AlternativeSolution,
    ContainerSpec,
    ItemType,
    OptimizationMode,
    PackingResult,
    PlacedPiece,
    PlanHandlingRules,
    WeightBalanceMode,
    WindowItem,
)

logger = logging.getLogger("cubox.optimize")

MAX_ALTERNATIVES = 3

# Identificadores de motor para debugging (seccion 12 del pedido Stage B:
# "Prefer runtime/debug metadata or logs. Do not require persistence schema
# changes unless genuinely necessary" -nunca se persisten, solo se loguean;
# ver _select_pack_fn)."""
ENGINE_BOX_EMS_V2 = "BOX_EMS_V2"
ENGINE_BOX_LEGACY_CLEARANCE_FALLBACK = "BOX_LEGACY_CLEARANCE_FALLBACK"
# PANEL_MODULE_V1 (Stage A.6/B, core/panel_module_solver.py:pack_panels_module)
# ya no es el motor de produccion -reemplazado por PANEL_MODULE_POCKET_V2
# (Pocket Reuse, seccion 1 del pedido de integracion). El nombre de la
# constante se conserva IGUAL (no se borra ni se reusa el string) porque
# panel_module_solver.py sigue vivo (seccion 16: "Do not delete... They
# remain useful for regression and rollback") y varios tests/fixtures lo
# siguen invocando DIRECTO (sin pasar por _select_pack_fn) -un rollback de
# este ruteo es cambiar UNA linea mas abajo, nunca reconstruir el string.
ENGINE_PANEL_MODULE_V1 = "PANEL_MODULE_V1"
ENGINE_PANEL_MODULE_POCKET_V2 = "PANEL_MODULE_POCKET_V2"
ENGINE_PANEL_LEGACY_TILT_FALLBACK = "PANEL_LEGACY_TILT_FALLBACK"
ENGINE_PANEL_LEGACY_CLEARANCE_FALLBACK = "PANEL_LEGACY_CLEARANCE_FALLBACK"
# Keep Groups Together, pedido "FINAL INTEGRATION + LIVE VISUAL
# ACCEPTANCE", seccion 2: tag de motor explicito para la ruta viva de
# core/section_coordinator.py:section_keep_groups_pocket_reuse_stage3
# (Dynamic Section + Level-2 architecture) -nunca se persiste (mismo
# criterio que el resto de ENGINE_*, seccion 12 del pedido Stage B
# original: "Prefer runtime/debug metadata or logs"), solo se loguea via
# logger.info en run_optimization para poder confirmar que el Workspace
# recibio de verdad el resultado de Stage 3.
ENGINE_PANEL_SECTION_GROUP_3D_V3 = "PANEL_SECTION_GROUP_3D_V3"
# Keep Systems Together, pedido "KEEP SYSTEMS TOGETHER -- REUSE THE
# APPROVED SECTION-FIRST 3D ENGINE", seccion 31: MISMO motor Section-
# first 3D que ENGINE_PANEL_SECTION_GROUP_3D_V3 (core/section_
# coordinator.py:section_cluster_3d), cluster_key=Sistema en vez de
# Grupo -tag distinto solo para debugging/confirmacion, nunca un
# segundo solver.
ENGINE_PANEL_SECTION_SYSTEM_3D_V3 = "PANEL_SECTION_SYSTEM_3D_V3"
ENGINE_PALLET_LEGACY = "PALLET_LEGACY"
ENGINE_CUSTOM_LEGACY = "CUSTOM_LEGACY"

LOG_TAG_PANEL_MODULE_FALLBACK_TILT = "PANEL_MODULE_FALLBACK_TILT"

# Post-Stage-B incidente real (reportado por el usuario contra datos reales
# en produccion, no un fixture sintetico): el EMS de Boxes y el Module
# Solver de Panels verifican clearance REACTIVAMENTE (rechazan un
# candidato si viola clearance contra una pieza ya colocada) pero NUNCA
# tallan el espacio libre/el ancho de modulo por adelantado para dejarle
# lugar -a diferencia de las zonas reservadas, que SI se tallan bien via
# zone_conflict_with_clearance con la zona inflada. Efecto medido: Boxes
# deja franjas enormes sin usar pegadas a una pared (confirmado con datos
# reales: 0/392 piezas tocaban y=0, un hueco sistematico de ~100mm en TODO
# el largo del contenedor); Panels es peor -el Module Solver ni siquiera
# intenta respetar clearance entre paneles, asi que el resultado sale con
# decenas de violaciones de clearance en la validacion final. Mientras
# esto no se resuelva (tallado de espacio libre real, no solo rechazo
# reactivo), CUALQUIER clearance>0 hace caer el ruteo al motor de
# produccion existente para AMBOS Load Types -mismo patron que zonas
# reservadas/Tilt, nunca un resultado silenciosamente peor o invalido.
LOG_TAG_BOX_FALLBACK_CLEARANCE = "BOX_MODULE_FALLBACK_CLEARANCE"
LOG_TAG_PANEL_MODULE_FALLBACK_CLEARANCE = "PANEL_MODULE_FALLBACK_CLEARANCE"


def _select_pack_fn(items: list[WindowItem], reserved_zones: list[ReservedZone] | None, clearance: float = 0.0):
    """Devuelve (pack_fn, engine_name) segun el Load Type de `items` (Stage
    B seccion 1) -mismo criterio de "un plan siempre es homogeneo en
    item_type" que ya usa core/packer_v2.py:pack_container_v2 y
    core/import_items.py. `pack_fn` siempre tiene la MISMA firma posicional
    que core/packer.pack_container (items, container, optimization_mode,
    reserved_zones, clearance, strategy, preplaced) y devuelve un
    PackingResult crudo -_adapt_v2 normaliza el caso de los motores V2
    (que devuelven (PackingResult, stats)) a esta misma forma.

    Panel Pocket Reuse (produccion desde la integracion de Pocket Reuse):
    pack_panels_module_pocket_reuse (core/panel_pocket_reuse.py) reemplaza a
    pack_panels_module como motor de produccion para Panels & Fragile
    -Stage 1 (seleccion de modulo/orden bilateral/ancla/tallado de zona
    reservada) queda IDENTICO, importado sin cambios; Stage 2 agrega reuso
    de pockets residuales entre modulos (ver ese archivo). Tilt/clearance
    activos siguen cayendo al motor legacy exactamente igual que antes -
    Pocket Reuse hereda esos mismos 2 fallbacks, ninguno nuevo.
    panel_module_solver.py (PANEL_MODULE_V1) sigue disponible para
    regresion/rollback pero ya no es alcanzado por este ruteo.

    Reserved Zones + Panels (RESUELTO -ya no hace falta el fallback de
    ruteo que existio hasta aca): el Module Solver ahora talla el ancho
    lateral disponible de cada modulo en INTERVALOS usables alrededor de
    cualquier zona reservada activa (ver panel_module_solver.py:
    _usable_lateral_intervals/_effective_orientation_for_capacity), en
    vez de perder el modulo entero por una sola colision -verificado
    contra el mismo caso real que perdia capacidad en Stage A.6.1 (42 ->
    10 piezas): ahora carga 42/42, identico al motor legacy en el mismo
    escenario. El fallback de ruteo por zona reservada se elimino
    -zonas reservadas activas ya NO empujan a Panels & Fragile al motor
    legacy."""
    if not items:
        return pack_container, ENGINE_CUSTOM_LEGACY
    item_type = items[0].item_type
    if item_type == ItemType.BOX:
        if clearance > TOL:
            logger.info(
                "%s: clearance=%.1fmm activo -usando motor de produccion existente para Loose Boxes "
                "(el EMS todavia no talla el espacio libre por clearance, solo lo verifica reactivamente)",
                LOG_TAG_BOX_FALLBACK_CLEARANCE, clearance,
            )
            return pack_container, ENGINE_BOX_LEGACY_CLEARANCE_FALLBACK
        return _adapt_v2(pack_boxes_v2), ENGINE_BOX_EMS_V2
    if item_type == ItemType.PANEL:
        if clearance > TOL:
            logger.info(
                "%s: clearance=%.1fmm activo -usando motor de produccion existente para Panels & Fragile "
                "(el Module Solver todavia no aplica clearance entre paneles)",
                LOG_TAG_PANEL_MODULE_FALLBACK_CLEARANCE, clearance,
            )
            return pack_container, ENGINE_PANEL_LEGACY_CLEARANCE_FALLBACK
        if any(w.allow_tilt and (w.max_tilt_angle or 0.0) > TOL for w in items):
            logger.info(
                "%s: Tilt activo -usando motor de produccion existente para Panels & Fragile "
                "(el Module Solver todavia no genera orientaciones con Tilt)",
                LOG_TAG_PANEL_MODULE_FALLBACK_TILT,
            )
            return pack_container, ENGINE_PANEL_LEGACY_TILT_FALLBACK
        return _adapt_v2(pack_panels_module_pocket_reuse), ENGINE_PANEL_MODULE_POCKET_V2
    if item_type == ItemType.PALLET:
        return pack_container, ENGINE_PALLET_LEGACY
    return pack_container, ENGINE_CUSTOM_LEGACY


def _adapt_v2(pack_fn_v2):
    """Envuelve pack_boxes_v2/pack_panels_module (devuelven (PackingResult,
    stats)) para que tengan la MISMA forma de retorno que pack_container
    (PackingResult solo) -asi el loop de estrategias de run_optimization
    no necesita saber que motor esta llamando en realidad."""

    def wrapped(items, container, optimization_mode, reserved_zones, clearance, strategy, preplaced):
        result, _stats = pack_fn_v2(items, container, optimization_mode, reserved_zones, clearance, strategy, preplaced)
        return result

    return wrapped


def _signature(result: PackingResult) -> tuple:
    return (result.metrics.loaded_pieces, round(result.metrics.used_volume_pct, 1))


def run_optimization(
    items: list[WindowItem],
    container: ContainerSpec,
    optimization_mode: OptimizationMode,
    reserved_zones: list[ReservedZone] | None = None,
    clearance: float = 0.0,
    weight_balance_mode: WeightBalanceMode = WeightBalanceMode.NORMAL,
    preplaced: list[PlacedPiece] | None = None,
    plan_handling_rules: PlanHandlingRules | None = None,
) -> tuple[PackingResult, list[AlternativeSolution]]:
    # Fase 5B: resuelto UNA sola vez aca (item override > plan default >
    # system default, ver core/handling_rules.py) -pack_container recibe
    # items ya con stackable/orientation_policy/max_stack_weight efectivos,
    # sin cambiar su propia firma ni su logica interna en absoluto.
    items = [resolve_effective_item(item, plan_handling_rules) for item in items]
    candidates: list[AlternativeSolution] = []

    pack_fn, engine_name = _select_pack_fn(items, reserved_zones, clearance)

    # Keep Groups Together, pedido "FINAL INTEGRATION + LIVE VISUAL
    # ACCEPTANCE", seccion 1: rutear a la arquitectura dinamica de
    # Section + Level 2 (section_keep_groups_pocket_reuse_stage3) SOLO
    # para la configuracion que esa etapa fue construida y verificada
    # para soportar -exactamente la MISMA que ya decidio `_select_pack_fn`
    # que puede usar Pocket Reuse (engine_name == ENGINE_PANEL_MODULE_
    # POCKET_V2, es decir: Panels & Fragile, sin Tilt activo, sin
    # Clearance>0 -ver _select_pack_fn arriba). Nunca se reimplementa esa
    # decision aca -reusar el engine_name ya calculado es lo que garantiza
    # (seccion 3 del pedido: "Do not force the Stage-3 engine into cases
    # it was not built to support") que Tilt/Clearance sigan cayendo
    # exactamente al mismo fallback legacy de siempre, sin ningun cambio:
    # si `_select_pack_fn` ya decidio ENGINE_PANEL_LEGACY_TILT_FALLBACK o
    # ENGINE_PANEL_LEGACY_CLEARANCE_FALLBACK, este bloque ni siquiera se
    # evalua, y el resto de la funcion sigue el camino Keep-Groups-Together
    # ya integrado (select_complete_groups + pack_fn=pack_container) tal
    # cual estaba antes de esta tarea.
    # Keep Systems Together, pedido "KEEP SYSTEMS TOGETHER -- REUSE THE
    # APPROVED SECTION-FIRST 3D ENGINE", seccion 31/32: MISMA compuerta
    # exacta que Keep Groups Together (engine_name == ENGINE_PANEL_MODULE_
    # POCKET_V2 -_select_pack_fn no distingue KEEP_GROUPS de KEEP_SYSTEMS,
    # solo Tilt/Clearance, asi que reusar ese mismo chequeo cubre AMBOS
    # modos identicamente, nunca una segunda decision de ruteo que pueda
    # divergir), solo cambia la funcion/tag/cluster_key.
    if optimization_mode in (OptimizationMode.KEEP_GROUPS, OptimizationMode.KEEP_SYSTEMS) and engine_name == ENGINE_PANEL_MODULE_POCKET_V2:
        from app.core.section_coordinator import (
            section_keep_groups_pocket_reuse_stage3,
            section_keep_systems_pocket_reuse_stage3,
        )

        if optimization_mode == OptimizationMode.KEEP_GROUPS:
            cluster_fn = section_keep_groups_pocket_reuse_stage3
            engine_tag = ENGINE_PANEL_SECTION_GROUP_3D_V3
            cluster_label = "Groups"
        else:
            cluster_fn = section_keep_systems_pocket_reuse_stage3
            engine_tag = ENGINE_PANEL_SECTION_SYSTEM_3D_V3
            cluster_label = "Systems"

        result, sections, cluster_reports, _completion_frontiers = cluster_fn(
            items, container, reserved_zones, clearance, preplaced,
        )
        logger.info(
            "Optimization engine selected: %s (%d items, %d Sections, %d/%d %s complete)",
            engine_tag, len(items), len(sections),
            sum(1 for r in cluster_reports if r.status == "COMPLETE"), len(cluster_reports), cluster_label,
        )
        score = score_solution(result, optimization_mode, weight_balance_mode)
        breakdown = score_breakdown(result, optimization_mode, weight_balance_mode)
        solo = AlternativeSolution(strategy=STRATEGY_LABELS["group_and_size"], score=score, breakdown=breakdown, result=result)
        return result, [solo]

    logger.info("Optimization engine selected: %s (%d items)", engine_name, len(items))

    # Keep Groups Together, seccion 2 del pedido de correccion semantica:
    # etapa de seleccion de Grupos COMPLETOS ANTES del loop de 7
    # estrategias -decide que Grupos intentar cargar completos (verificado
    # con el motor real, acotado por cantidad de Grupos, nunca 2^N) y
    # filtra `items` a ese subconjunto. El loop de abajo queda IDENTICO,
    # solo corre sobre menos items -Pocket Reuse/EMS/legacy nunca ven los
    # items de un Grupo no seleccionado (seccion 12: "must not pull
    # individual items from Groups that were not selected", garantizado
    # por construccion). excluded_items se reintegra a `unloaded` DESPUES
    # (con PRIORITY_DISPLACED) para que loaded_pieces/total_pieces sigan
    # reflejando el plan completo, nunca solo el subconjunto seleccionado.
    #
    # Nota (seccion 1 del pedido de integracion): este camino ahora SOLO
    # se alcanza para KEEP_GROUPS cuando la configuracion NO califica para
    # Stage 3 arriba (Tilt/Clearance activos, u otro item_type distinto de
    # PANEL) -preserva el comportamiento exacto que ya tenia antes de esta
    # tarea para esos casos, nunca un resultado nuevo o distinto.
    excluded_items: list[WindowItem] = []
    if optimization_mode == OptimizationMode.KEEP_GROUPS:
        items, _group_reports, excluded_items, _accepted_order = select_complete_groups(
            items, container, pack_fn, reserved_zones, clearance, preplaced
        )
        logger.info(
            "Keep Groups Together: %d Groups seleccionados, %d items excluidos (Grupos no seleccionados)",
            len({w.group for w in items if w.group}), len(excluded_items),
        )

    for strategy in STRATEGIES:
        result = pack_fn(
            items, container, optimization_mode, reserved_zones, clearance, strategy, preplaced
        )
        score = score_solution(result, optimization_mode, weight_balance_mode)
        breakdown = score_breakdown(result, optimization_mode, weight_balance_mode)
        logger.debug(
            "Optimization Strategy: %s -> score=%.4f loaded=%d/%d volume=%.1f%%",
            STRATEGY_LABELS[strategy],
            score,
            result.metrics.loaded_pieces,
            result.metrics.total_pieces,
            result.metrics.used_volume_pct,
        )
        candidates.append(
            AlternativeSolution(strategy=STRATEGY_LABELS[strategy], score=score, breakdown=breakdown, result=result)
        )

    if excluded_items:
        reserved_ids: set[str] = {p.id for p in preplaced or []}
        for c in candidates:
            reserved_ids |= {p.id for p in c.result.placed} | {u.id for u in c.result.unloaded}
        excluded_unloaded = _unloaded_from_excluded(excluded_items, reserved_ids)
        for c in candidates:
            c.result.unloaded = c.result.unloaded + excluded_unloaded
            c.result.metrics = compute_metrics(container, c.result.placed, c.result.unloaded)

    candidates.sort(key=lambda c: c.score, reverse=True)

    deduped: list[AlternativeSolution] = []
    seen_signatures: set[tuple] = set()
    for c in candidates:
        sig = _signature(c.result)
        if sig in seen_signatures:
            continue
        seen_signatures.add(sig)
        deduped.append(c)
        if len(deduped) >= MAX_ALTERNATIVES:
            break

    if not deduped:
        deduped = candidates[:1]

    best = deduped[0].result
    return best, deduped
