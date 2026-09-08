"""Resolucion de Handling Rules por item (CUBOX 2.0, Fase 5B; Tilt/
Inclination agregado en Fase 5C, convertido a PLAN-LEVEL ONLY en Fase
5C-FINAL).

Precedencia para Stackable/Orientation/MaxStackWeight, documentada aca y en
ningun otro lado:

    item explicito (override)
        -> default del PLAN (PlanHandlingRules)
        -> default de SISTEMA (por ItemType / stackable=True)

Tilt usa una cadena DISTINTA y mas corta (no tiene override de item -ver
`resolve_plan_tilt`):

    PANEL Hard Constraint (solo PANEL puede tenerlo)
        -> Plan Tilt Settings
        -> System Default (allow_tilt=False)

Punto unico de aplicacion: `resolve_effective_item`, llamado una sola vez en
core/optimize.py:run_optimization -ANTES de que pack_container (sin cambios,
sigue recibiendo items ya resueltos) haga ninguna colocacion. El resto del
motor (packer, manual_move, final_validation, sequence, geometry) sigue
leyendo `.stackable`/`.orientation_policy`/`.max_stack_weight`/`.allow_tilt`/
`.max_tilt_angle` tal cual siempre lo hizo -no necesitan saber que existe un
plan default ni un override: para cuando los ven, ya son el valor EFECTIVO.

No se muta el WindowItem original (seccion 15 del pedido de Fase 5C): esta
funcion devuelve una copia (`model_copy`) con esos campos ya resueltos; los
campos `_override` crudos (Stackable/Orientation) se preservan sin cambios
en la copia, para que packer.py pueda copiarlos a PlacedPiece/UnloadedItem
(trazabilidad para el Inspector: "Override" vs "Inherited"). Tilt no tiene
campo `_override`: el Inspector debe mostrar siempre "Plan Default", nunca
un badge de Override (seccion 12/37 del pedido final)."""

from app.models.schemas import TILT_MAX_ANGLE_DEG, ItemType, PlanHandlingRules, WindowItem, resolve_orientation_policy


def resolve_stackable(override: bool | None, plan_default: bool | None, fallback: bool) -> bool:
    """override explicito > default del plan > fallback (system/legacy).
    `fallback` es el `.stackable` YA resuelto al momento del import (ver
    core/import_items.py) -preserva el comportamiento de siempre para
    cualquier caller que nunca toque `stackable_override`/plan_handling_rules
    (todos los tests/paths existentes antes de Fase 5B)."""
    if override is not None:
        return override
    if plan_default is not None:
        return plan_default
    return fallback


def resolve_max_stack_weight(item_value: float | None, plan_default: float | None) -> float | None:
    """item_value explicito > default del plan. None en ambos = sin limite,
    igual que siempre (esta regla no existia como plan default antes de
    Fase 5B, asi que no hay comportamiento previo que romper)."""
    return item_value if item_value is not None else plan_default


def resolve_plan_tilt(item_type: ItemType, plan_rules: PlanHandlingRules | None) -> tuple[bool, float]:
    """Fase 5C-FINAL: Tilt es PLAN-LEVEL ONLY (seccion 2/3 del pedido final)
    -no existe override de item, no hay cadena de 3 pasos como Stackable/
    Orientation. La jerarquia real es:

        PANEL Hard Constraint (solo PANEL puede tener Tilt en esta fase)
            -> Plan Tilt Settings (PlanHandlingRules.default_allow_tilt/
               default_max_tilt_angle)
            -> System Default (allow_tilt=False)

    Devuelve (effective_allow_tilt, effective_max_tilt_angle) -esta ultima
    es SIEMPRE una magnitud >= 0 (el rango firmado real que puede tomar
    tilt_angle es [-max, +max], ver orientation.py/manual_move.py)."""
    if item_type != ItemType.PANEL or plan_rules is None or not plan_rules.default_allow_tilt:
        return False, 0.0
    raw_max = plan_rules.default_max_tilt_angle
    max_tilt = raw_max if raw_max is not None else 0.0
    # Defensa en profundidad: el Field(le=TILT_MAX_ANGLE_DEG) en schemas.py ya
    # rechaza valores crudos fuera de rango; este clamp cubre ademas un
    # futuro caller que construya PlanHandlingRules sin pasar por Pydantic.
    return True, min(max_tilt, TILT_MAX_ANGLE_DEG)


def resolve_effective_item(item: WindowItem, plan_rules: PlanHandlingRules | None) -> WindowItem:
    """Devuelve una COPIA de `item` con stackable/orientation_policy/
    max_stack_weight ya resueltos a su valor efectivo. Los campos crudos
    (`stackable_override`, `orientation_override`) no se tocan -siguen
    representando lo que realmente vino del Excel, para trazabilidad.

    Orientation usa un truco deliberado para no repetir la cadena de
    precedencia dos veces: si el plan ACTUAL (`plan_rules`, el que llega en
    ESTE request) trae un default explicito, ese gana sobre `.orientation_policy`
    -que puede estar desactualizado si el import ya lo habia materializado
    con OTRO default de plan mas viejo (ver import_items.py). Si el plan
    actual no dice nada, `.orientation_policy` (el valor ya resuelto al
    importar) sigue siendo el mejor fallback disponible -exactamente el
    comportamiento de antes de Fase 5B para cualquier caller que nunca use
    `plan_handling_rules`."""
    effective_stackable = resolve_stackable(
        item.stackable_override,
        plan_rules.default_stackable if plan_rules else None,
        item.stackable,
    )
    plan_or_legacy_orientation = (
        plan_rules.default_orientation_policy
        if plan_rules and plan_rules.default_orientation_policy is not None
        else item.orientation_policy
    )
    effective_orientation = resolve_orientation_policy(
        item.item_type,
        item.orientation_override,
        plan_or_legacy_orientation,
    )
    effective_max_stack_weight = resolve_max_stack_weight(
        item.max_stack_weight,
        plan_rules.default_max_stack_weight if plan_rules else None,
    )

    effective_allow_tilt, effective_max_tilt = resolve_plan_tilt(item.item_type, plan_rules)

    return item.model_copy(
        update={
            "stackable": effective_stackable,
            "orientation_policy": effective_orientation,
            "max_stack_weight": effective_max_stack_weight,
            "allow_tilt": effective_allow_tilt,
            "max_tilt_angle": effective_max_tilt,
        }
    )
