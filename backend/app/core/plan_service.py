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

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from app.core.plan_store import SCHEMA_VERSION, CorruptPlanStateError, UnsupportedSchemaVersionError, PlanRow
from app.core.reserved_zones import ReservedZone
from app.models.schemas import (
    ItemType,
    LoadingAnchor,
    LoadSpaceSpec,
    OptimizationMode,
    PackingResult,
    PlacedPiece,
    PlanHandlingRules,
    UnloadedItem,
    WeightBalanceMode,
)

_MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

LOAD_TYPE_LABELS: dict[ItemType, str] = {
    ItemType.BOX: "Loose Boxes",
    ItemType.PALLET: "Palletized Load",
    ItemType.PANEL: "Panels & Fragile",
    ItemType.CUSTOM: "Custom Load",
}
"""Fase 5D: Recent Plans no persiste un 'planning_mode' propio -el ItemType
de las piezas ya lo determina 1 a 1 en el frontend (ver
wizardTypes.ts:PLANNING_MODE_ITEM_TYPE: box<->loose_boxes,
pallet<->palletized_load, panel<->panels_fragile, custom<->custom_load;
build_pallets no tiene item_type propio, es "Coming Soon"). Reutilizar ese
mismo mapeo aca evita guardar un campo redundante que podria desincronizarse
del contenido real del plan."""


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


def _load_type_label(placed: list[PlacedPiece], unloaded: list[UnloadedItem]) -> str:
    first_item_type = placed[0].item_type if placed else (unloaded[0].item_type if unloaded else None)
    if first_item_type is None:
        return "Load Plan"
    return LOAD_TYPE_LABELS.get(first_item_type, "Load Plan")


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
    state = {
        "schema_version": SCHEMA_VERSION,
        "load_space": load_space.model_dump(mode="json"),
        "plan_handling_rules": plan_handling_rules.model_dump(mode="json") if plan_handling_rules is not None else None,
        "clearance": clearance,
        "optimization_mode": optimization_mode.value,
        "weight_balance_mode": weight_balance_mode.value,
        "loading_anchor": loading_anchor.value,
        "reserved_zones": [
            {"x": z.x, "y": z.y, "z": z.z, "length": z.length, "width": z.width, "height": z.height, "label": z.label}
            for z in reserved_zones
        ],
        "placed": [p.model_dump(mode="json") for p in result.placed],
        "unloaded": [u.model_dump(mode="json") for u in result.unloaded],
    }
    return PlanSnapshot(
        state_json=json.dumps(state),
        load_type=_load_type_label(result.placed, result.unloaded),
        load_space_name=load_space.name,
        total_items=len(result.placed) + len(result.unloaded),
        loaded_items=len(result.placed),
        unloaded_items=len(result.unloaded),
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
        data = json.loads(row.state_json)
        load_space = LoadSpaceSpec.model_validate(data["load_space"])
        raw_rules = data.get("plan_handling_rules")
        plan_handling_rules = PlanHandlingRules.model_validate(raw_rules) if raw_rules is not None else None
        placed = [PlacedPiece.model_validate(p) for p in data["placed"]]
        unloaded = [UnloadedItem.model_validate(u) for u in data["unloaded"]]
        reserved_zones = [
            ReservedZone(x=z["x"], y=z["y"], z=z["z"], length=z["length"], width=z["width"], height=z["height"], label=z["label"])
            for z in data.get("reserved_zones", [])
        ]
        clearance = float(data.get("clearance", 0.0))
        optimization_mode = OptimizationMode(data.get("optimization_mode", OptimizationMode.BEST_SPACE.value))
        weight_balance_mode = WeightBalanceMode(data.get("weight_balance_mode", WeightBalanceMode.NORMAL.value))
        loading_anchor = LoadingAnchor(data.get("loading_anchor", LoadingAnchor.BACK_RIGHT.value))
    except (KeyError, ValueError, TypeError) as e:
        raise CorruptPlanStateError(f"Load Plan {row.plan_id} tiene un estado invalido o incompatible: {e}") from e

    return RestoredPlanState(
        load_space=load_space,
        placed=placed,
        unloaded=unloaded,
        plan_handling_rules=plan_handling_rules,
        clearance=clearance,
        optimization_mode=optimization_mode,
        weight_balance_mode=weight_balance_mode,
        loading_anchor=loading_anchor,
        reserved_zones=reserved_zones,
    )
