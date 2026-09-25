"""Integracion NAGSA, A1 (H12): resumen liviano de un Load Plan -tipo de
carga, nombre del Load Space y contadores. Unica responsabilidad: describir
un estado sin serializarlo.

Antes estaba mezclado dentro de core/plan_service.py:build_plan_snapshot
junto con la serializacion. Lo consumen Recent Plans (modo local, columnas
de resumen de SQLite) y, a futuro, el historico de .NET (modo integrado)."""

from __future__ import annotations

from dataclasses import dataclass

from app.models.schemas import ItemType, PlacedPiece, UnloadedItem
from app.services.plan_state import PlanState

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


@dataclass(frozen=True)
class PlanStateSummary:
    load_type: str
    load_space_name: str
    total_items: int
    loaded_items: int
    unloaded_items: int


def load_type_label(placed: list[PlacedPiece], unloaded: list[UnloadedItem]) -> str:
    first_item_type = placed[0].item_type if placed else (unloaded[0].item_type if unloaded else None)
    if first_item_type is None:
        return "Load Plan"
    return LOAD_TYPE_LABELS.get(first_item_type, "Load Plan")


def summarize_plan_state(state: PlanState) -> PlanStateSummary:
    return PlanStateSummary(
        load_type=load_type_label(state.placed, state.unloaded),
        load_space_name=state.load_space.name,
        total_items=len(state.placed) + len(state.unloaded),
        loaded_items=len(state.placed),
        unloaded_items=len(state.unloaded),
    )
