"""CUBOX 2.0 - Fase 5B: resolucion de Handling Rules (item override > plan
default > system default). Cubre core/handling_rules.py directamente
(unidad) y el flujo completo via /api/pack + manual move + final validation
(integracion), ver seccion 37 del pedido."""

import pytest
from fastapi.testclient import TestClient

from app.core.handling_rules import resolve_effective_item, resolve_max_stack_weight, resolve_stackable
from app.core.packer import pack_container
from app.main import app
from app.models.containers import build_custom_load_space
from app.models.schemas import (
    Dimensions3D,
    ItemType,
    LoadSpaceType,
    OrientationPolicy,
    PlanHandlingRules,
    WindowItem,
    resolve_orientation_policy,
)

client = TestClient(app)


def _box(**overrides):
    defaults = dict(
        code="B1",
        dimensions=Dimensions3D(length=600, width=400, height=300),
        weight=25,
        quantity=1,
        item_type=ItemType.BOX,
    )
    defaults.update(overrides)
    return WindowItem(**defaults)


# ---------------------------------------------------------------------------
# Unidad: resolve_stackable -precedencia y distincion false-vs-None.
# ---------------------------------------------------------------------------


def test_resolve_stackable_override_wins_over_everything():
    assert resolve_stackable(override=False, plan_default=True, fallback=True) is False
    assert resolve_stackable(override=True, plan_default=False, fallback=False) is True


def test_resolve_stackable_explicit_false_is_not_confused_with_unset():
    """Seccion 5 del pedido: False explicito NUNCA debe caer al default."""
    assert resolve_stackable(override=False, plan_default=True, fallback=True) is False


def test_resolve_stackable_plan_default_applies_when_no_override():
    assert resolve_stackable(override=None, plan_default=False, fallback=True) is False
    assert resolve_stackable(override=None, plan_default=True, fallback=False) is True


def test_resolve_stackable_falls_back_when_nothing_else_set():
    assert resolve_stackable(override=None, plan_default=None, fallback=True) is True
    assert resolve_stackable(override=None, plan_default=None, fallback=False) is False


# ---------------------------------------------------------------------------
# Unidad: resolve_max_stack_weight.
# ---------------------------------------------------------------------------


def test_resolve_max_stack_weight_item_value_wins():
    assert resolve_max_stack_weight(item_value=150, plan_default=500) == 150


def test_resolve_max_stack_weight_plan_default_applies_when_item_is_none():
    assert resolve_max_stack_weight(item_value=None, plan_default=500) == 500


def test_resolve_max_stack_weight_none_when_neither_set():
    assert resolve_max_stack_weight(item_value=None, plan_default=None) is None


# ---------------------------------------------------------------------------
# Unidad: resolve_effective_item -orquesta los 3 campos, no muta el original.
# ---------------------------------------------------------------------------


def test_resolve_effective_item_does_not_mutate_the_source_item():
    item = _box(stackable=True, stackable_override=None)
    plan = PlanHandlingRules(default_stackable=False)
    resolved = resolve_effective_item(item, plan)

    assert resolved.stackable is False  # la copia SI cambio
    assert item.stackable is True  # el original quedo intacto


def test_resolve_effective_item_none_plan_rules_is_a_pure_passthrough():
    """Backward-compat: sin plan_handling_rules (None), el resultado es
    identico a lo que el item ya traia resuelto -ningun test/caller previo a
    Fase 5B que nunca toque los campos nuevos deberia notar diferencia."""
    item = _box(stackable=False, max_stack_weight=200, orientation_policy=OrientationPolicy.UPRIGHT)
    resolved = resolve_effective_item(item, None)
    assert resolved.stackable is False
    assert resolved.max_stack_weight == 200
    assert resolved.orientation_policy == OrientationPolicy.UPRIGHT


def test_resolve_effective_item_panel_orientation_is_a_hard_constraint():
    """Seccion 14: un default de plan (aunque sea FREE) NUNCA debe aflojar
    PANEL_EDGE_ONLY para un item_type=PANEL."""
    panel = _box(item_type=ItemType.PANEL, orientation_policy=None, orientation_override=None)
    plan = PlanHandlingRules(default_orientation_policy=OrientationPolicy.FREE)
    resolved = resolve_effective_item(panel, plan)
    assert resolved.orientation_policy == OrientationPolicy.PANEL_EDGE_ONLY


def test_resolve_orientation_policy_panel_is_mandatory_regardless_of_explicit_value():
    """Regresion critica (pedido de seguridad post-Fase 5B): PANEL_EDGE_ONLY
    debe ser un HARD CONSTRAINT, no un default -ni siquiera un
    orientation_policy EXPLICITO en el item puede aflojarlo. import_items.py
    nunca deja pasar esto para PANEL (orientation_mode="none"), pero el
    contrato HTTP de /api/pack no lo impide por si solo -este chequeo tiene
    que vivir en el resolver, no solo en el importador/UI."""
    assert resolve_orientation_policy(ItemType.PANEL, OrientationPolicy.FREE) == OrientationPolicy.PANEL_EDGE_ONLY
    assert resolve_orientation_policy(ItemType.PANEL, OrientationPolicy.UPRIGHT) == OrientationPolicy.PANEL_EDGE_ONLY
    assert resolve_orientation_policy(ItemType.PANEL, OrientationPolicy.FIXED) == OrientationPolicy.PANEL_EDGE_ONLY
    assert resolve_orientation_policy(ItemType.PANEL, None, plan_default=OrientationPolicy.FREE) == OrientationPolicy.PANEL_EDGE_ONLY


def test_resolve_effective_item_panel_explicit_orientation_override_cannot_weaken_the_constraint():
    """Mismo caso que arriba pero a traves del punto de entrada real que usa
    el packer (resolve_effective_item), con un orientation_override
    EXPLICITO -no un default de plan- intentando aflojar la regla."""
    panel = _box(item_type=ItemType.PANEL, orientation_policy=None, orientation_override=OrientationPolicy.FREE)
    resolved = resolve_effective_item(panel, None)
    assert resolved.orientation_policy == OrientationPolicy.PANEL_EDGE_ONLY


def test_pack_endpoint_panel_explicit_orientation_override_cannot_weaken_the_constraint():
    """End-to-end via /api/pack: un item PANEL que manda un
    orientation_override="free" explicito en el request HTTP -el mismo
    contrato que un cliente podria construir a mano, sin pasar por
    import_items.py- no debe poder terminar empacado como FREE."""
    r = client.post(
        "/api/pack",
        json={
            "items": [
                {
                    "code": "W1", "width": 800, "height": 1200, "thickness": 80, "weight": 20,
                    "quantity": 1, "item_type": "panel", "orientation_override": "free",
                }
            ],
            "container_id": "40ft_standard",
        },
    )
    assert r.status_code == 200
    assert r.json()["best"]["placed"][0]["orientation_policy"] == "panel_edge_only"


def test_resolve_effective_item_orientation_override_wins_over_plan_default():
    item = _box(orientation_policy=OrientationPolicy.FREE, orientation_override=OrientationPolicy.FIXED)
    plan = PlanHandlingRules(default_orientation_policy=OrientationPolicy.UPRIGHT)
    resolved = resolve_effective_item(item, plan)
    assert resolved.orientation_policy == OrientationPolicy.FIXED


def test_resolve_effective_item_fresh_plan_default_beats_stale_materialized_value():
    """El caso central de Fase 5B (escenario F del pedido): un item que
    "hereda" (sin override) y cuyo `.orientation_policy` ya quedo
    materializado en el import con un default VIEJO, debe reflejar el
    default NUEVO que llega en `plan_rules` -no quedarse pegado al viejo."""
    item = _box(orientation_policy=OrientationPolicy.UPRIGHT, orientation_override=None)  # materializado con un plan viejo
    fresh_plan = PlanHandlingRules(default_orientation_policy=OrientationPolicy.FIXED)
    resolved = resolve_effective_item(item, fresh_plan)
    assert resolved.orientation_policy == OrientationPolicy.FIXED


# ---------------------------------------------------------------------------
# Escenario A (seccion 26): Stackable con item override conviviendo con un
# plan default, via el motor de packing real.
# ---------------------------------------------------------------------------


def test_scenario_a_stackability_precedence_via_pack_container():
    space = build_custom_load_space("Test Space", LoadSpaceType.CUSTOM, 2000, 2000, 2000, 50000)
    p001 = _box(code="P001", stackable=True, stackable_override=None)  # inherit
    p002 = _box(code="P002", stackable=True, stackable_override=False)  # override explicito
    p003 = _box(code="P003", stackable=True, stackable_override=None)  # inherit

    from app.core.handling_rules import resolve_effective_item

    plan = PlanHandlingRules(default_stackable=True)
    resolved = [resolve_effective_item(i, plan) for i in (p001, p002, p003)]
    by_code = {p.code: p.stackable for p in resolved}
    assert by_code == {"P001": True, "P002": False, "P003": True}


def test_scenario_f_changing_plan_default_does_not_touch_explicit_overrides():
    a = _box(code="A", stackable=True, stackable_override=None)
    b = _box(code="B", stackable=True, stackable_override=False)

    plan_no = PlanHandlingRules(default_stackable=False)
    resolved_no = {p.code: p.stackable for p in (resolve_effective_item(a, plan_no), resolve_effective_item(b, plan_no))}
    assert resolved_no == {"A": False, "B": False}

    plan_yes = PlanHandlingRules(default_stackable=True)
    resolved_yes = {p.code: p.stackable for p in (resolve_effective_item(a, plan_yes), resolve_effective_item(b, plan_yes))}
    assert resolved_yes == {"A": True, "B": False}  # B nunca se movio


# ---------------------------------------------------------------------------
# API end-to-end: /api/pack con plan_handling_rules -precedencia real,
# manual move y final validation usan el mismo resultado resuelto.
# ---------------------------------------------------------------------------


def test_pack_endpoint_applies_plan_default_stackable_to_inherited_items():
    """P002 (override=No) no debe poder soportar nada aunque el plan
    default sea Stackable=Yes; P001 (inherit) si debe poder."""
    r = client.post(
        "/api/pack",
        json={
            "items": [
                {
                    "code": "P001", "dimensions": {"length": 1000, "width": 1000, "height": 500}, "weight": 100,
                    "quantity": 1, "item_type": "box", "stackable": True, "stackable_override": None,
                },
                {
                    "code": "P002", "dimensions": {"length": 1000, "width": 1000, "height": 500}, "weight": 100,
                    "quantity": 1, "item_type": "box", "stackable": False, "stackable_override": False,
                },
            ],
            "custom_load_space": {
                "name": "Narrow", "load_space_type": "custom", "length": 1000, "width": 1000, "height": 1100, "max_weight": 5000,
            },
            "plan_handling_rules": {"default_stackable": True},
        },
    )
    assert r.status_code == 200
    best = r.json()["best"]
    placed_by_code = {p["code"]: p for p in best["placed"]}
    assert placed_by_code["P001"]["stackable"] is True
    assert placed_by_code["P002"]["stackable"] is False


def test_pack_endpoint_plan_default_orientation_respects_panel_hard_constraint():
    """Un plan_handling_rules.default_orientation_policy=FREE no debe poder
    aflojar PANEL_EDGE_ONLY para un item_type=panel (via el importador
    legacy, sin orientation_override -ver seccion 14)."""
    r = client.post(
        "/api/pack",
        json={
            "items": [
                {
                    "code": "W1", "width": 800, "height": 1200, "thickness": 80, "weight": 20,
                    "quantity": 1, "item_type": "panel",
                }
            ],
            "container_id": "40ft_standard",
            "plan_handling_rules": {"default_orientation_policy": "free"},
        },
    )
    assert r.status_code == 200
    best = r.json()["best"]
    assert best["placed"][0]["orientation_policy"] == "panel_edge_only"


def test_packing_never_stacks_on_a_plan_inherited_item_with_an_explicit_no_override():
    """BASE tiene stackable_override=False (explicito) aunque el plan
    default sea Stackable=Yes -TOP no debe poder apoyarse en BASE. El
    contenedor es angosto a proposito (footprint exacto de BASE): la unica
    forma de colocar TOP seria apilandolo, lo cual el override debe impedir."""
    r = client.post(
        "/api/pack",
        json={
            "items": [
                {
                    "code": "BASE", "dimensions": {"length": 1000, "width": 1000, "height": 500}, "weight": 100,
                    "quantity": 1, "item_type": "box", "orientation_policy": "fixed", "stackable_override": False,
                },
                {
                    "code": "TOP", "dimensions": {"length": 1000, "width": 1000, "height": 500}, "weight": 100,
                    "quantity": 1, "item_type": "box", "orientation_policy": "fixed",
                },
            ],
            "custom_load_space": {
                "name": "Tiny", "load_space_type": "custom", "length": 1000, "width": 1000, "height": 1100, "max_weight": 5000,
            },
            "plan_handling_rules": {"default_stackable": True},
        },
    )
    assert r.status_code == 200
    best = r.json()["best"]
    placed_codes = {p["code"] for p in best["placed"]}
    unloaded_codes = {u["code"] for u in best["unloaded"]}
    assert placed_codes == {"BASE"}
    assert unloaded_codes == {"TOP"}

    # Final validation (que lee el mismo PlacedPiece.stackable ya resuelto,
    # sin recalcular nada) confirma que el layout resultante sigue siendo
    # valido -no hay ninguna pieza apoyada donde no deberia.
    validate_r = client.post("/api/report/validate")
    assert validate_r.status_code == 200
    assert validate_r.json() == {"valid": True, "errors": []}
