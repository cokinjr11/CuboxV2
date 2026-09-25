"""Integracion NAGSA, A1 (H5): conversiones entre las 3 formas de una pieza
-PlacedPiece (colocada), UnloadedItem (sin cargar) y WindowItem/LoadItem
(entrada al optimizador). Unica responsabilidad: copiar campos de una forma a
otra, sin validar ni decidir nada.

Antes vivian repartidas en api/routes.py (_window_item_from_placed,
_window_item_from_unloaded y dos construcciones inline de ~20 campos en
remove_piece/insert_piece). Codigo movido tal cual -mismos campos, mismos
criterios de "congelar" overrides- para que los dos modos de la API usen
exactamente las mismas conversiones."""

from __future__ import annotations

from app.core.reasons import UnloadedReason
from app.models.schemas import PlacedPiece, UnloadedItem, WindowItem

MANUAL_REMOVE_REASON = "Removido manualmente"


def placed_to_item(p: PlacedPiece) -> WindowItem:
    """Pieza colocada -> item de entrada para Optimize Remaining."""
    # Fase 5B: stackable_override/orientation_override se "congelan" al valor
    # YA resuelto (p.stackable/p.orientation_policy) en vez de copiar el
    # override crudo original -esta pieza ya paso por una resolucion real
    # (el pack que la coloco), Optimize Remaining no debe hacerla "derivar"
    # a un nuevo default de plan que haya cambiado desde entonces (ver
    # core/handling_rules.py: override no-None siempre gana).
    return WindowItem(
        code=p.code,
        description=p.description,
        width=p.source_width,
        height=p.source_height,
        thickness=p.source_thickness,
        weight=p.weight,
        quantity=1,
        system=p.system,
        group=p.group,
        stackable=p.stackable,
        stackable_override=p.stackable,
        priority=p.priority,
        max_stack_weight=p.max_stack_weight,
        delivery_sequence=p.delivery_sequence,
        boxes_inside=p.boxes_inside,
        item_type=p.item_type,
        orientation_policy=p.orientation_policy,
        orientation_override=p.orientation_policy,
        # Fase 5C-FINAL: Tilt es PLAN-LEVEL ONLY -no hay valor de item que
        # "congelar" (resolve_plan_tilt en handling_rules.py ignora estos
        # campos del LoadItem y re-resuelve directo de item_type + el plan
        # ACTUAL que llega a Optimize Remaining -ver core/optimize.py).
    )


def unloaded_to_item(u: UnloadedItem) -> WindowItem:
    """Item sin cargar -> item de entrada para Optimize Remaining."""
    # Ver comentario de placed_to_item: mismo criterio de "congelar" el valor
    # ya resuelto como override explicito.
    return WindowItem(
        code=u.code,
        description=u.description,
        width=u.width,
        height=u.height,
        thickness=u.thickness,
        weight=u.weight,
        quantity=1,
        system=u.system,
        group=u.group,
        stackable=u.stackable,
        stackable_override=u.stackable,
        priority=u.priority,
        max_stack_weight=u.max_stack_weight,
        delivery_sequence=u.delivery_sequence,
        boxes_inside=u.boxes_inside,
        item_type=u.item_type,
        orientation_policy=u.orientation_policy,
        orientation_override=u.orientation_policy,
        # Fase 5C-FINAL: ver comentario equivalente en placed_to_item.
    )


def placed_to_unloaded(
    piece: PlacedPiece,
    reason: str = MANUAL_REMOVE_REASON,
    reason_code: str = UnloadedReason.MANUAL_REMOVE.value,
) -> UnloadedItem:
    """Pieza colocada -> Unloaded Items (ej. el usuario la quita a mano).
    A diferencia de placed_to_item, conserva los overrides CRUDOS: la pieza
    sigue siendo la misma, solo cambia de lista."""
    return UnloadedItem(
        id=piece.id,
        code=piece.code,
        description=piece.description,
        width=piece.source_width,
        height=piece.source_height,
        thickness=piece.source_thickness,
        weight=piece.weight,
        system=piece.system,
        group=piece.group,
        stackable=piece.stackable,
        priority=piece.priority,
        max_stack_weight=piece.max_stack_weight,
        delivery_sequence=piece.delivery_sequence,
        boxes_inside=piece.boxes_inside,
        reason=reason,
        reason_code=reason_code,
        item_type=piece.item_type,
        orientation_policy=piece.orientation_policy,
        stackable_override=piece.stackable_override,
        orientation_override=piece.orientation_override,
        allow_tilt=piece.allow_tilt,
        max_tilt_angle=piece.max_tilt_angle,
    )


def unloaded_to_placed(
    item: UnloadedItem,
    *,
    x: float,
    y: float,
    z: float,
    dx: float,
    dy: float,
    dz: float,
    orientation_label: str,
    tilt_angle: float,
    tilt_axis: str | None,
    base_dx: float | None,
    base_dy: float | None,
    base_dz: float | None,
) -> PlacedPiece:
    """Item sin cargar -> pieza colocada en una posicion/orientacion YA
    validada por el llamador (ej. insercion manual)."""
    return PlacedPiece(
        id=item.id,
        code=item.code,
        description=item.description,
        system=item.system,
        group=item.group,
        weight=item.weight,
        stackable=item.stackable,
        priority=item.priority,
        max_stack_weight=item.max_stack_weight,
        delivery_sequence=item.delivery_sequence,
        boxes_inside=item.boxes_inside,
        x=x,
        y=y,
        z=z,
        dx=dx,
        dy=dy,
        dz=dz,
        orientation_label=orientation_label,
        source_width=item.width,
        source_height=item.height,
        source_thickness=item.thickness,
        item_type=item.item_type,
        orientation_policy=item.orientation_policy,
        stackable_override=item.stackable_override,
        orientation_override=item.orientation_override,
        allow_tilt=item.allow_tilt,
        max_tilt_angle=item.max_tilt_angle,
        tilt_angle=tilt_angle,
        tilt_axis=tilt_axis,
        base_dx=base_dx,
        base_dy=base_dy,
        base_dz=base_dz,
    )
