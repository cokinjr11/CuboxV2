"""Integracion NAGSA, A2 (H1, H3): calcular un cubicaje. Unica
responsabilidad: correr el optimizador (core/optimize.py, unico punto de
ruteo de motores) y devolver el estado + resultado de la mejor solucion y
sus alternativas.

NO guarda en sesion, NO reinicia historial, NO persiste: eso lo decide quien
llama (modo local: api/routes.py + local/; modo integrado: el cliente).
Antes todo eso estaba mezclado en api/routes.py:pack/optimize_remaining."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.optimize import run_optimization
from app.core.reserved_zones import ReservedZone, central_aisle_zone
from app.models.schemas import AlternativeSolution, OptimizeResponse, PackingResult, PackRequest
from app.services.derivation import EvaluatedPlan, complete_result
from app.services.load_spaces import resolve_load_space
from app.services.locked_pieces import validate_locked_pieces
from app.services.piece_mapping import placed_to_item, unloaded_to_item
from app.services.plan_state import PlanState, reserved_zones_from_state, reserved_zones_to_state


@dataclass(frozen=True)
class PackOutcome:
    """`plan` = la mejor solucion (estado + resultado). `alternatives` =
    hasta 3 soluciones puntuadas, la primera ES la mejor (mismo objeto
    resultado, igual que siempre devolvio run_optimization)."""

    plan: EvaluatedPlan
    alternatives: list[AlternativeSolution]

    def state_for(self, result: PackingResult) -> PlanState:
        """Estado de cualquiera de las alternativas: misma configuracion,
        otras piezas (el modo integrado lo necesita para que el cliente
        pueda quedarse con una alternativa)."""
        return self.plan.state.model_copy(update={"placed": result.placed, "unloaded": result.unloaded})

    def to_response(self) -> OptimizeResponse:
        return OptimizeResponse(best=self.plan.result, alternatives=self.alternatives)


def _complete_candidates(
    best: PackingResult, alternatives: list[AlternativeSolution], state: PlanState
) -> None:
    zones_out = list(state.reserved_zones)
    complete_result(best, state.load_space, state.loading_anchor, zones_out)
    for alt in alternatives:
        complete_result(alt.result, state.load_space, state.loading_anchor, zones_out)


def pack(request: PackRequest) -> PackOutcome:
    """Cubicaje desde cero a partir de los items y la configuracion del
    pedido."""
    load_space = resolve_load_space(request.container_id, request.custom_load_space)

    zones: list[ReservedZone] = []
    if request.enable_central_aisle:
        zones.append(central_aisle_zone(load_space, request.aisle_width_mm))

    best, alternatives = run_optimization(
        request.items,
        load_space,
        request.optimization_mode,
        zones,
        request.clearance_mm,
        request.weight_balance_mode,
        plan_handling_rules=request.plan_handling_rules,
    )

    state = PlanState(
        load_space=load_space,
        plan_handling_rules=request.plan_handling_rules,
        clearance=request.clearance_mm,
        optimization_mode=request.optimization_mode,
        weight_balance_mode=request.weight_balance_mode,
        loading_anchor=request.loading_anchor,
        reserved_zones=reserved_zones_to_state(zones),
        placed=best.placed,
        unloaded=best.unloaded,
    )
    _complete_candidates(best, alternatives, state)
    return PackOutcome(plan=EvaluatedPlan(state=state, result=best), alternatives=alternatives)


def optimize_remaining(state: PlanState) -> PackOutcome:
    """Reoptimiza todo lo que NO esta Locked; las piezas bloqueadas quedan
    exactamente donde estan (posicion y orientacion intactas). Usa la
    configuracion que YA trae `state` -para cambiarla antes, ver
    plan_config.apply_overrides."""
    validate_locked_pieces(state)

    locked = [p for p in state.placed if p.locked]
    unlocked = [p for p in state.placed if not p.locked]
    remaining_items = [placed_to_item(p) for p in unlocked] + [unloaded_to_item(u) for u in state.unloaded]

    best, alternatives = run_optimization(
        remaining_items,
        state.load_space,
        state.optimization_mode,
        reserved_zones_from_state(state.reserved_zones),
        state.clearance,
        state.weight_balance_mode,
        preplaced=locked,
        plan_handling_rules=state.plan_handling_rules,
    )

    new_state = state.model_copy(update={"placed": best.placed, "unloaded": best.unloaded})
    _complete_candidates(best, alternatives, new_state)
    return PackOutcome(plan=EvaluatedPlan(state=new_state, result=best), alternatives=alternatives)
