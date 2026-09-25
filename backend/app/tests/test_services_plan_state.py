"""Integracion NAGSA, A1: services/plan_state.py + services/plan_summary.py.

Tests unitarios SIN HTTP ni SQLite -justamente lo que la separacion de
responsabilidades permite. El test clave es la equivalencia con el formato
historico de `state_json` (Fase 5D): planes ya guardados en SQLite deben
seguir abriendo igual, y el modo integrado /api/v1 usa este mismo formato."""

import json
from functools import lru_cache

import pytest

from app.core.optimize import run_optimization
from app.core.plan_schema import SCHEMA_VERSION, CorruptPlanStateError, UnsupportedSchemaVersionError
from app.core.plan_service import build_plan_snapshot
from app.core.reserved_zones import central_aisle_zone
from app.models.containers import get_container
from app.models.schemas import (
    LoadingAnchor,
    OptimizationMode,
    OrientationPolicy,
    PlanHandlingRules,
    WeightBalanceMode,
    WindowItem,
)
from app.services.plan_state import (
    PlanState,
    deserialize_plan_state,
    reserved_zones_from_state,
    reserved_zones_to_state,
    serialize_plan_state,
)
from app.services.plan_summary import summarize_plan_state


@lru_cache(maxsize=1)
def _packed_plan():
    """Un plan real (no armado a mano): panels en un 20ft con pasillo
    central -incluye placed y unloaded (W3 no entra de canto: ambas caras
    superan el alto del 20ft) y una zona reservada cuyo x/z son enteros (0),
    el caso borde de formato. Cacheado: se optimiza una sola vez por modulo
    (ningun test lo muta)."""
    container = get_container("20ft_standard")
    zones = [central_aisle_zone(container, 500)]
    items = [
        WindowItem(code="W1", width=1200, height=2000, thickness=80, weight=40, quantity=4, item_type="panel", group="G1"),
        WindowItem(code="W2", width=900, height=1500, thickness=100, weight=25, quantity=4, item_type="panel", group="G2", system="Picture Window"),
        WindowItem(code="W3", width=2500, height=2500, thickness=80, weight=60, quantity=1, item_type="panel", group="G3"),
    ]
    rules = PlanHandlingRules(default_stackable=True, default_orientation_policy=OrientationPolicy.PANEL_EDGE_ONLY)
    best, _alts = run_optimization(items, container, OptimizationMode.BEST_SPACE, zones, 0.0, WeightBalanceMode.IMPORTANT, plan_handling_rules=rules)
    return best, container, zones, rules


def _legacy_state_json(result, load_space, rules, clearance, mode, balance, anchor, zones) -> str:
    """Copia LITERAL de como Fase 5D armaba `state_json` antes de A1 (ver git
    show 6439514:backend/app/core/plan_service.py). Es la referencia contra
    la que se compara el formato nuevo."""
    state = {
        "schema_version": SCHEMA_VERSION,
        "load_space": load_space.model_dump(mode="json"),
        "plan_handling_rules": rules.model_dump(mode="json") if rules is not None else None,
        "clearance": clearance,
        "optimization_mode": mode.value,
        "weight_balance_mode": balance.value,
        "loading_anchor": anchor.value,
        "reserved_zones": [
            {"x": z.x, "y": z.y, "z": z.z, "length": z.length, "width": z.width, "height": z.height, "label": z.label}
            for z in zones
        ],
        "placed": [p.model_dump(mode="json") for p in result.placed],
        "unloaded": [u.model_dump(mode="json") for u in result.unloaded],
    }
    return json.dumps(state)


def _state_from(result, load_space, zones, rules) -> PlanState:
    return PlanState(
        load_space=load_space,
        plan_handling_rules=rules,
        clearance=0.0,
        optimization_mode=OptimizationMode.KEEP_GROUPS,
        weight_balance_mode=WeightBalanceMode.IMPORTANT,
        loading_anchor=LoadingAnchor.BACK_LEFT,
        reserved_zones=reserved_zones_to_state(zones),
        placed=result.placed,
        unloaded=result.unloaded,
    )


# ---------------------------------------------------------------------------
# Equivalencia con el formato historico
# ---------------------------------------------------------------------------


def test_fixture_has_placed_unloaded_and_integer_zone_coordinates():
    result, _container, zones, _rules = _packed_plan()
    assert result.placed and result.unloaded
    assert isinstance(zones[0].x, int)  # el caso borde que motiva comparar "al parsear"


def test_serialized_state_is_equivalent_to_legacy_state_json():
    result, container, zones, rules = _packed_plan()
    state = _state_from(result, container, zones, rules)

    legacy = _legacy_state_json(
        result, container, rules, 0.0, OptimizationMode.KEEP_GROUPS, WeightBalanceMode.IMPORTANT, LoadingAnchor.BACK_LEFT, zones
    )
    assert json.loads(serialize_plan_state(state)) == json.loads(legacy)


def test_serialized_state_keeps_legacy_key_order():
    result, container, zones, rules = _packed_plan()
    keys = list(json.loads(serialize_plan_state(_state_from(result, container, zones, rules))))
    assert keys == [
        "schema_version", "load_space", "plan_handling_rules", "clearance", "optimization_mode",
        "weight_balance_mode", "loading_anchor", "reserved_zones", "placed", "unloaded",
    ]


def test_legacy_state_json_deserializes_to_the_same_state():
    result, container, zones, rules = _packed_plan()
    legacy = _legacy_state_json(
        result, container, rules, 0.0, OptimizationMode.KEEP_GROUPS, WeightBalanceMode.IMPORTANT, LoadingAnchor.BACK_LEFT, zones
    )
    assert deserialize_plan_state(legacy) == _state_from(result, container, zones, rules)


def test_build_plan_snapshot_still_writes_the_legacy_format():
    """H12: build_plan_snapshot ahora solo compone serializar + resumir -su
    salida debe seguir siendo la misma de antes."""
    result, container, zones, rules = _packed_plan()
    snapshot = build_plan_snapshot(
        result=result, load_space=container, plan_handling_rules=rules, clearance=0.0,
        optimization_mode=OptimizationMode.KEEP_GROUPS, weight_balance_mode=WeightBalanceMode.IMPORTANT,
        loading_anchor=LoadingAnchor.BACK_LEFT, reserved_zones=zones,
    )
    legacy = _legacy_state_json(
        result, container, rules, 0.0, OptimizationMode.KEEP_GROUPS, WeightBalanceMode.IMPORTANT, LoadingAnchor.BACK_LEFT, zones
    )
    assert json.loads(snapshot.state_json) == json.loads(legacy)
    assert snapshot.load_type == "Panels & Fragile"
    assert snapshot.load_space_name == container.name
    assert (snapshot.total_items, snapshot.loaded_items, snapshot.unloaded_items) == (
        len(result.placed) + len(result.unloaded), len(result.placed), len(result.unloaded),
    )


# ---------------------------------------------------------------------------
# Ida y vuelta + validacion de entrada
# ---------------------------------------------------------------------------


def test_round_trip_preserves_the_state():
    result, container, zones, rules = _packed_plan()
    state = _state_from(result, container, zones, rules)
    assert deserialize_plan_state(serialize_plan_state(state)) == state


def test_optional_config_fields_fall_back_to_defaults():
    result, container, _zones, _rules = _packed_plan()
    minimal = json.dumps({
        "load_space": container.model_dump(mode="json"),
        "placed": [p.model_dump(mode="json") for p in result.placed],
        "unloaded": [],
    })
    state = deserialize_plan_state(minimal)
    assert state.schema_version == SCHEMA_VERSION
    assert state.clearance == 0.0
    assert state.optimization_mode == OptimizationMode.BEST_SPACE
    assert state.weight_balance_mode == WeightBalanceMode.NORMAL
    assert state.loading_anchor == LoadingAnchor.BACK_RIGHT
    assert state.plan_handling_rules is None
    assert state.reserved_zones == []


@pytest.mark.parametrize(
    "raw",
    [
        "{not json",
        "[]",
        json.dumps({"schema_version": SCHEMA_VERSION}),  # falta load_space/placed/unloaded
        json.dumps({"load_space": {"id": "x"}, "placed": [], "unloaded": []}),  # load_space incompleto
    ],
)
def test_malformed_state_raises_corrupt_plan_state_error(raw):
    with pytest.raises(CorruptPlanStateError):
        deserialize_plan_state(raw)


def test_unknown_schema_version_raises_unsupported():
    result, container, zones, rules = _packed_plan()
    data = json.loads(serialize_plan_state(_state_from(result, container, zones, rules)))
    data["schema_version"] = 999
    with pytest.raises(UnsupportedSchemaVersionError):
        deserialize_plan_state(json.dumps(data))


def test_reserved_zones_round_trip():
    container = get_container("40ft_standard")
    zones = [central_aisle_zone(container, 600)]
    assert reserved_zones_from_state(reserved_zones_to_state(zones)) == zones


# ---------------------------------------------------------------------------
# Resumen (H12)
# ---------------------------------------------------------------------------


def test_summary_counts_and_load_type():
    result, container, zones, rules = _packed_plan()
    summary = summarize_plan_state(_state_from(result, container, zones, rules))
    assert summary.load_type == "Panels & Fragile"
    assert summary.load_space_name == container.name
    assert summary.loaded_items == len(result.placed)
    assert summary.unloaded_items == len(result.unloaded)
    assert summary.total_items == len(result.placed) + len(result.unloaded)


def test_summary_of_empty_plan_uses_generic_label():
    state = PlanState(load_space=get_container("20ft_standard"), placed=[], unloaded=[])
    assert summarize_plan_state(state).load_type == "Load Plan"
