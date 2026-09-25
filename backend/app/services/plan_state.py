"""Integracion NAGSA, A1: el estado canonico de un Load Plan y su
serializacion. Unica responsabilidad: definir QUE es el estado de un plan y
como se convierte a/desde JSON.

Es exactamente el mismo contenido que Fase 5D ya guardaba en `state_json`
(ver core/plan_service.py): Load Space, Plan Handling Rules, configuracion
activa (clearance/optimization mode/weight balance/loading anchor), zonas
reservadas, piezas colocadas y no colocadas. Por eso un plan guardado en el
SQLite local y un plan guardado por .NET (modo integrado) tienen el MISMO
formato.

Deliberadamente NO incluye los campos DERIVADOS de PackingResult (metrics,
secuencias, warnings, blocked_by, road_weight): se recalculan siempre desde
este estado, nunca viajan como fuente de verdad (mismo criterio que Fase
5D). Tampoco incluye nada de sesion (historial de undo/redo, plan_id): eso
es del modo local."""

from __future__ import annotations

import json

from pydantic import BaseModel, ValidationError

from app.core.plan_schema import SCHEMA_VERSION, CorruptPlanStateError, UnsupportedSchemaVersionError
from app.core.reserved_zones import ReservedZone
from app.models.schemas import (
    LoadingAnchor,
    LoadSpaceSpec,
    OptimizationMode,
    PlacedPiece,
    PlanHandlingRules,
    ReservedZoneOut,
    UnloadedItem,
    WeightBalanceMode,
)


class PlanState(BaseModel):
    """Orden de campos = orden de claves del `state_json` historico (Fase
    5D). Placed/unloaded/load_space son obligatorios: un estado sin ellos es
    corrupto (mismo criterio que parse_plan_state, seccion 41 del pedido)."""

    schema_version: int = SCHEMA_VERSION
    load_space: LoadSpaceSpec
    plan_handling_rules: PlanHandlingRules | None = None
    clearance: float = 0.0
    optimization_mode: OptimizationMode = OptimizationMode.BEST_SPACE
    weight_balance_mode: WeightBalanceMode = WeightBalanceMode.NORMAL
    loading_anchor: LoadingAnchor = LoadingAnchor.BACK_RIGHT
    reserved_zones: list[ReservedZoneOut] = []
    placed: list[PlacedPiece]
    unloaded: list[UnloadedItem]


def serialize_plan_state(state: PlanState) -> str:
    """JSON del estado -mismo formato que `state_json` de Fase 5D."""
    return json.dumps(state.model_dump(mode="json"))


def deserialize_plan_state(state_json: str) -> PlanState:
    """Inverso de serialize_plan_state. Nunca confia ciegamente en el JSON
    (seccion 41/42 de Fase 5D): una version desconocida levanta
    UnsupportedSchemaVersionError; cualquier otro problema de forma,
    CorruptPlanStateError."""
    try:
        data = json.loads(state_json)
    except ValueError as e:
        raise CorruptPlanStateError(f"JSON invalido: {e}") from e
    if not isinstance(data, dict):
        raise CorruptPlanStateError("El estado del plan debe ser un objeto JSON")

    version = data.get("schema_version", SCHEMA_VERSION)
    if version != SCHEMA_VERSION:
        raise UnsupportedSchemaVersionError(version)

    try:
        return PlanState.model_validate(data)
    except ValidationError as e:
        raise CorruptPlanStateError(str(e)) from e


def reserved_zones_to_state(zones: list[ReservedZone]) -> list[ReservedZoneOut]:
    """core.reserved_zones.ReservedZone (dataclass que usan los motores) ->
    version serializable que vive en el estado y en PackingResult (ex
    api/routes.py:_reserved_zones_out)."""
    return [ReservedZoneOut(x=z.x, y=z.y, z=z.z, length=z.length, width=z.width, height=z.height, label=z.label) for z in zones]


def reserved_zones_from_state(zones: list[ReservedZoneOut]) -> list[ReservedZone]:
    """Inverso de reserved_zones_to_state: lo que necesitan los motores y
    validadores de core/ (optimize, manual_move, final_validation)."""
    return [ReservedZone(x=z.x, y=z.y, z=z.z, length=z.length, width=z.width, height=z.height, label=z.label) for z in zones]
