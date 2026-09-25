"""Fase 5D: traduce entre el estado de dominio (Pydantic, ver
models/schemas.py) y PlanRow (persistente, ver core/plan_store.py -que es
deliberadamente ajeno a Pydantic). Unico lugar donde se decide que campos
entran al blob `state_json` persistido y como se reconstruyen al reabrir un
plan.

Deliberadamente NO se persisten los campos DERIVADOS de PackingResult
(metrics, load_sequence, unload_sequence, load_sequence_warnings,
road_weight, y desde Fase 6A tambien operational_warnings/blocked_by): todos
se recalculan frescos al reabrir un plan, exactamente como ya hace
api/routes.py:_refresh_derived despues de cada edicion manual -son
puramente una funcion de (placed, unloaded, load_space,
plan_handling_rules/loading_anchor), nunca estado independiente que pueda
desincronizarse."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from app.core.plan_store import SCHEMA_VERSION, CorruptPlanStateError, UnsupportedSchemaVersionError, PlanRow
from app.core.reserved_zones import ReservedZone
from app.models.schemas import (
    LoadingAnchor,
    LoadSpaceSpec,
    OptimizationMode,
    PackingResult,
    PlacedPiece,
    PlanHandlingRules,
    UnloadedItem,
    WeightBalanceMode,
)

# Integracion NAGSA, A1: el FORMATO del estado (PlanState + serializacion) y
# el resumen de listado viven en services/. Este modulo queda como traductor
# PlanRow <-> estado de dominio para el modo local (se mueve a local/ en A3;
# hasta entonces es la unica dependencia core -> services, ver
# docs/CHECKLIST_INTEGRACION.md).
from app.services.plan_state import PlanState, deserialize_plan_state, reserved_zones_from_state, reserved_zones_to_state, serialize_plan_state
from app.services.plan_summary import summarize_plan_state

_MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def new_plan_id() -> str:
    """UUID (seccion 6 del pedido) -nunca un indice de array ni un
    timestamp."""
    return uuid4().hex


def utc_now_iso() -> str:
    """Fase 5D, seccion 39 del pedido: timestamps SIEMPRE en UTC
    internamente; el frontend decide como mostrarlos en la zona horaria del
    usuario."""
    return datetime.now(timezone.utc).isoformat()


def default_plan_name(now: datetime | None = None) -> str:
    """Seccion 8 del pedido: nombre usable sin que el usuario tenga que
    nombrar el plan antes de crearlo. No se usa strftime("%-d", ...) -ese
    flag no es portable en Windows (donde corre este backend en desarrollo);
    se arma el string a mano."""
    now = now or datetime.now(timezone.utc)
    return f"Load Plan - {_MONTH_ABBR[now.month - 1]} {now.day}, {now.year}"


@dataclass
class PlanSnapshot:
    """Lo que hace falta para escribir (crear O actualizar) una fila
    `plans` -sin `plan_id`/`name`/`created_at`, que decide el llamador
    (create_plan asigna un id nuevo; save_plan preserva el nombre/fecha de
    creacion existentes, ver api/routes.py)."""

    state_json: str
    load_type: str
    load_space_name: str
    total_items: int
    loaded_items: int
    unloaded_items: int


def build_plan_snapshot(
    *,
    result: PackingResult,
    load_space: LoadSpaceSpec,
    plan_handling_rules: PlanHandlingRules | None,
    clearance: float,
    optimization_mode: OptimizationMode,
    weight_balance_mode: WeightBalanceMode,
    loading_anchor: LoadingAnchor,
    reserved_zones: list[ReservedZone],
) -> PlanSnapshot:
    """Construye el blob `state_json` a partir del estado de dominio vivo
    (tipicamente `_current_state`, ver api/routes.py) -seccion 1/15/16/18/
    19/20/21 del pedido: placed/unloaded COMPLETOS (incluyendo
    stackable_override/orientation_override CRUDOS, nunca solo el valor ya
    resuelto -para que la herencia de Handling Rules siga funcionando
    despues de reabrir, ver core/handling_rules.py), Load Space completo
    (catalogo o custom, sin fallback a catalogo), Plan Handling Rules,
    config activa (clearance/optimization/weight balance/loading anchor) y
    las zonas reservadas reales."""
    # Integracion NAGSA, A1 (H12): dos responsabilidades separadas -armar/
    # serializar el estado (services/plan_state.py) y resumirlo para las
    # columnas de Recent Plans (services/plan_summary.py). Esta funcion solo
    # las compone; su firma y su resultado no cambian.
    state = PlanState(
        load_space=load_space,
        plan_handling_rules=plan_handling_rules,
        clearance=clearance,
        optimization_mode=optimization_mode,
        weight_balance_mode=weight_balance_mode,
        loading_anchor=loading_anchor,
        reserved_zones=reserved_zones_to_state(reserved_zones),
        placed=result.placed,
        unloaded=result.unloaded,
    )
    summary = summarize_plan_state(state)
    return PlanSnapshot(
        state_json=serialize_plan_state(state),
        load_type=summary.load_type,
        load_space_name=summary.load_space_name,
        total_items=summary.total_items,
        loaded_items=summary.loaded_items,
        unloaded_items=summary.unloaded_items,
    )


@dataclass
class RestoredPlanState:
    """Lo que hace falta para repoblar `_current_state` por completo al
    abrir un plan (seccion 13/14 del pedido: SIN volver a correr el
    optimizador)."""

    load_space: LoadSpaceSpec
    placed: list[PlacedPiece]
    unloaded: list[UnloadedItem]
    plan_handling_rules: PlanHandlingRules | None
    clearance: float
    optimization_mode: OptimizationMode
    weight_balance_mode: WeightBalanceMode
    loading_anchor: LoadingAnchor
    reserved_zones: list[ReservedZone]


def parse_plan_state(row: PlanRow) -> RestoredPlanState:
    """Inverso de build_plan_snapshot. Seccion 41/42 del pedido: NUNCA
    confia ciegamente en el JSON guardado -valida contra los schemas
    Pydantic actuales y contra schema_version; cualquier fallo se traduce a
    una excepcion clara (UnsupportedSchemaVersionError /
    CorruptPlanStateError), nunca un crash sin mensaje ni una
    reinterpretacion silenciosa de datos incompatibles."""
    if row.schema_version != SCHEMA_VERSION:
        raise UnsupportedSchemaVersionError(row.schema_version)

    try:
        state = deserialize_plan_state(row.state_json)
    except CorruptPlanStateError as e:
        raise CorruptPlanStateError(f"Load Plan {row.plan_id} tiene un estado invalido o incompatible: {e}") from e

    return RestoredPlanState(
        load_space=state.load_space,
        placed=state.placed,
        unloaded=state.unloaded,
        plan_handling_rules=state.plan_handling_rules,
        clearance=state.clearance,
        optimization_mode=state.optimization_mode,
        weight_balance_mode=state.weight_balance_mode,
        loading_anchor=state.loading_anchor,
        reserved_zones=reserved_zones_from_state(state.reserved_zones),
    )
