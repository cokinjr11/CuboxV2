"""Orquestador de multiples estrategias (secciones 3, 26-28 de V2).

Corre cada estrategia de core/strategies.py sobre el mismo motor de
colocacion (core/packer.pack_container), puntua cada resultado
(core/scoring.py) y elige la de mejor score. Guarda hasta 3 soluciones
completas (deduplicadas) para poder mostrarlas como alternativas.
"""

import logging

from app.core.handling_rules import resolve_effective_item
from app.core.packer import pack_container
from app.core.reserved_zones import ReservedZone
from app.core.scoring import score_breakdown, score_solution
from app.core.strategies import STRATEGIES, STRATEGY_LABELS
from app.models.schemas import (
    AlternativeSolution,
    ContainerSpec,
    OptimizationMode,
    PackingResult,
    PlacedPiece,
    PlanHandlingRules,
    WeightBalanceMode,
    WindowItem,
)

logger = logging.getLogger("cubox.optimize")

MAX_ALTERNATIVES = 3


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

    for strategy in STRATEGIES:
        result = pack_container(
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
