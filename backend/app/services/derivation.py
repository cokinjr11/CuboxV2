"""Integracion NAGSA, A2 (H9): campos DERIVADOS de un plan. Unica
responsabilidad: calcular, a partir de las piezas y la configuracion, todo
lo que nunca se persiste (Fase 5D) -metricas, secuencias de carga/descarga,
warnings operativos, blocked_by, peso por eje y zonas reservadas expuestas.

Antes: api/routes.py:_apply_sequence_fields/_refresh_derived/_road_weight_for,
que leian la configuracion desde `_current_state`. Ahora reciben todo
explicito, asi los usan igual el modo local y el integrado."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.packer import compute_metrics
from app.core.road_weight import evaluate_road_weight, weight_point_from_placed
from app.core.sequence import (
    compute_load_sequence,
    compute_operational_warnings,
    compute_unload_dependencies,
    compute_unload_sequence,
)
from app.models.schemas import LoadingAnchor, LoadSpaceSpec, PackingResult, PlacedPiece, ReservedZoneOut
from app.services.plan_state import PlanState


@dataclass(frozen=True)
class EvaluatedPlan:
    """Un estado junto con su resultado ya calculado (`result` =
    derive_result(state), o el resultado del optimizador para ese mismo
    estado). Es lo que consumen validacion y reportes: evita recalcular y
    evita pasar estado y resultado sueltos que podrian no corresponderse."""

    state: PlanState
    result: PackingResult


def road_weight_for(load_space: LoadSpaceSpec, placed: list[PlacedPiece]):
    """Metricas de distribucion de peso longitudinal (Fase 2B) -None si el
    LoadSpace no tiene RoadWeightConfig habilitado (Container/Truck/Trailer
    legacy, comportamiento Fase 2A intacto)."""
    return evaluate_road_weight(load_space.road_weight_config, (weight_point_from_placed(p) for p in placed))


def apply_sequence_fields(result: PackingResult, load_space: LoadSpaceSpec, anchor: LoadingAnchor) -> None:
    """Fase 6A: unico lugar que llena load_sequence/unload_sequence/
    operational_warnings/blocked_by -TODOS derivados, nunca persistidos (ver
    core/plan_service.py). load_sequence_warnings se mantiene como la
    proyeccion en texto plano de operational_warnings por compatibilidad con
    Excel y con cualquier consumidor que todavia lea el campo viejo."""
    result.load_sequence = compute_load_sequence(result.placed, load_space, anchor)
    result.unload_sequence = compute_unload_sequence(result.placed)
    result.operational_warnings = compute_operational_warnings(result.placed, load_space, anchor)
    result.load_sequence_warnings = [w.message for w in result.operational_warnings]
    result.blocked_by = compute_unload_dependencies(result.placed)


def complete_result(
    result: PackingResult, load_space: LoadSpaceSpec, anchor: LoadingAnchor, reserved_zones: list[ReservedZoneOut]
) -> None:
    """Completa un PackingResult que ya trae metricas (lo que devuelven los
    motores de core/): secuencias, warnings, peso por eje y zonas reservadas.
    Muta `result` -se usa sobre resultados recien creados por el optimizador
    o por derive_result, nunca sobre uno ajeno."""
    apply_sequence_fields(result, load_space, anchor)
    result.road_weight = road_weight_for(load_space, result.placed)
    result.reserved_zones = reserved_zones


def derive_result(state: PlanState) -> PackingResult:
    """El resultado completo de un estado, recalculado desde cero. Mismos
    valores que produce el optimizador para esas mismas piezas: todos los
    motores terminan en compute_metrics(container, placed, unloaded)."""
    result = PackingResult(
        container=state.load_space,
        placed=state.placed,
        unloaded=state.unloaded,
        metrics=compute_metrics(state.load_space, state.placed, state.unloaded),
    )
    complete_result(result, state.load_space, state.loading_anchor, list(state.reserved_zones))
    return result


def evaluate(state: PlanState) -> EvaluatedPlan:
    return EvaluatedPlan(state=state, result=derive_result(state))
