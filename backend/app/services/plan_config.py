"""Integracion NAGSA, A2 (H3, H7): cambios de CONFIGURACION de un plan
(modo de optimizacion, balance de peso, punto de inicio de carga, Plan
Handling Rules). Unica responsabilidad: devolver un estado nuevo con la
configuracion cambiada -nunca optimiza, nunca guarda, nunca toca piezas.

Antes estos cambios estaban mezclados dentro de optimize_remaining() (que
ademas optimizaba) y save_plan() (que ademas persistia)."""

from __future__ import annotations

from app.models.schemas import LoadingAnchor, OptimizationMode, PlanHandlingRules, WeightBalanceMode
from app.services.plan_state import PlanState


def apply_overrides(
    state: PlanState,
    *,
    optimization_mode: OptimizationMode | None = None,
    weight_balance_mode: WeightBalanceMode | None = None,
    loading_anchor: LoadingAnchor | None = None,
    plan_handling_rules: PlanHandlingRules | None = None,
) -> PlanState:
    """None = "no cambiar" (semantica de OptimizeRemainingRequest: si se
    mandan, reemplazan lo guardado ANTES de reoptimizar -asi Keep Groups/
    Keep Systems/Weight Balance elegidos en la UI realmente se respetan
    aunque no se haya vuelto a correr /api/pack)."""
    update = {}
    if optimization_mode is not None:
        update["optimization_mode"] = optimization_mode
    if weight_balance_mode is not None:
        update["weight_balance_mode"] = weight_balance_mode
    if loading_anchor is not None:
        update["loading_anchor"] = loading_anchor
    if plan_handling_rules is not None:
        update["plan_handling_rules"] = plan_handling_rules
    return state.model_copy(update=update)


def set_plan_handling_rules(state: PlanState, rules: PlanHandlingRules | None) -> PlanState:
    """Reemplazo EXPLICITO de las Plan Handling Rules -None aca significa
    "sin reglas de plan", no "no cambiar" (semantica del autosave de Handling
    Rules, Fase 5D correccion final: guardar configuracion nunca recalcula la
    colocacion)."""
    return state.model_copy(update={"plan_handling_rules": rules})
