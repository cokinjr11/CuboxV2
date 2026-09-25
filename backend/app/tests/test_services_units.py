"""Integracion NAGSA, A2: tests unitarios de services/ -sin HTTP, sin
sesion, sin SQLite. Complementan la paridad (test_services_parity_legacy.py)
con la semantica propia de cada modulo: que decide, que NO toca y que error
levanta."""

from functools import lru_cache

import pytest

from app.models.schemas import (
    CustomLoadSpaceRequest,
    LoadingAnchor,
    LoadSpaceType,
    OptimizationMode,
    PackRequest,
    PlanHandlingRules,
    WeightBalanceMode,
    WindowItem,
)
from app.services import editing, locked_pieces, packing, plan_config, reporting
from app.services.derivation import EvaluatedPlan, derive_result, evaluate
from app.services.errors import PlanOpError, PlanOpErrorKind
from app.services.load_spaces import resolve_load_space
from app.services.plan_state import deserialize_plan_state, serialize_plan_state


def _boxes(qty=12):
    return [WindowItem(code="B1", width=600, height=400, thickness=400, weight=30, quantity=qty, item_type="box", group="A")]


@lru_cache(maxsize=1)
def _outcome() -> packing.PackOutcome:
    return packing.pack(PackRequest(items=_boxes(), container_id="20ft_standard", enable_central_aisle=True))


# ---------------------------------------------------------------------------
# load_spaces
# ---------------------------------------------------------------------------


def test_custom_load_space_wins_over_catalog():
    custom = CustomLoadSpaceRequest(name="Mi camion", load_space_type=LoadSpaceType.TRUCK, length=6000, width=2400, height=2400, max_weight=9000)
    ls = resolve_load_space("20ft_standard", custom)
    assert ls.name == "Mi camion" and ls.id.startswith("custom-")


def test_unknown_catalog_id_is_not_found():
    with pytest.raises(PlanOpError) as exc:
        resolve_load_space("no-existe", None)
    assert exc.value.kind == PlanOpErrorKind.NOT_FOUND


def test_missing_load_space_is_invalid_input():
    with pytest.raises(PlanOpError) as exc:
        resolve_load_space(None, None)
    assert exc.value.kind == PlanOpErrorKind.INVALID_INPUT


# ---------------------------------------------------------------------------
# packing + derivation
# ---------------------------------------------------------------------------


def test_pack_state_carries_the_requested_configuration():
    state = _outcome().plan.state
    assert state.load_space.id == "20ft_standard"
    assert [z.label for z in state.reserved_zones] == ["central_aisle"]
    assert state.optimization_mode == OptimizationMode.BEST_SPACE
    assert len(state.placed) + len(state.unloaded) == 12


def test_best_result_is_the_first_alternative():
    out = _outcome()
    assert out.alternatives[0].result is out.plan.result


def test_state_for_each_alternative_keeps_config_and_changes_pieces():
    out = _outcome()
    for alt in out.alternatives:
        s = out.state_for(alt.result)
        assert s.load_space == out.plan.state.load_space
        assert s.reserved_zones == out.plan.state.reserved_zones
        assert [p.id for p in s.placed] == [p.id for p in alt.result.placed]


def test_derive_result_equals_the_optimizer_result():
    out = _outcome()
    assert derive_result(out.plan.state).model_dump() == out.plan.result.model_dump()


def test_derived_result_survives_a_serialization_round_trip():
    """El caso del modo integrado: .NET guarda el estado, mas tarde lo
    reenvia y CUBOX recalcula exactamente el mismo resultado."""
    out = _outcome()
    restored = deserialize_plan_state(serialize_plan_state(out.plan.state))
    assert derive_result(restored).model_dump(mode="json") == out.plan.result.model_dump(mode="json")


# ---------------------------------------------------------------------------
# plan_config
# ---------------------------------------------------------------------------


def test_apply_overrides_none_means_keep():
    state = _outcome().plan.state
    same = plan_config.apply_overrides(state)
    assert same == state


def test_apply_overrides_changes_only_what_was_sent_and_never_mutates_input():
    state = _outcome().plan.state
    changed = plan_config.apply_overrides(state, loading_anchor=LoadingAnchor.BACK_LEFT, weight_balance_mode=WeightBalanceMode.IGNORE)
    assert changed.loading_anchor == LoadingAnchor.BACK_LEFT
    assert changed.weight_balance_mode == WeightBalanceMode.IGNORE
    assert changed.optimization_mode == state.optimization_mode
    assert state.loading_anchor == LoadingAnchor.BACK_RIGHT


def test_set_plan_handling_rules_none_clears():
    state = plan_config.apply_overrides(_outcome().plan.state, plan_handling_rules=PlanHandlingRules(default_stackable=False))
    assert state.plan_handling_rules is not None
    assert plan_config.set_plan_handling_rules(state, None).plan_handling_rules is None


# ---------------------------------------------------------------------------
# locked_pieces
# ---------------------------------------------------------------------------


def test_valid_locked_pieces_pass():
    state = editing.lock_piece(_outcome().plan.state, _outcome().plan.state.placed[0].id)
    locked_pieces.validate_locked_pieces(state)  # no levanta


def test_colliding_locked_pieces_are_a_conflict():
    state = _outcome().plan.state
    a, b = state.placed[0].id, state.placed[1].id
    state = editing.lock_piece(editing.lock_piece(state, a), b)
    state.placed[1].x, state.placed[1].y, state.placed[1].z = state.placed[0].x, state.placed[0].y, state.placed[0].z
    with pytest.raises(PlanOpError) as exc:
        locked_pieces.validate_locked_pieces(state)
    assert exc.value.kind == PlanOpErrorKind.CONFLICT
    assert "colisionan" in exc.value.detail


def test_optimize_remaining_keeps_locked_pieces_in_place():
    state = _outcome().plan.state
    first = state.placed[0]
    locked = editing.lock_piece(state, first.id)
    out = packing.optimize_remaining(locked)
    kept = next(p for p in out.plan.state.placed if p.id == first.id)
    assert (kept.x, kept.y, kept.z, kept.dx, kept.dy, kept.dz) == (first.x, first.y, first.z, first.dx, first.dy, first.dz)
    assert kept.locked is True


# ---------------------------------------------------------------------------
# editing
# ---------------------------------------------------------------------------


def test_locked_piece_cannot_be_removed():
    state = _outcome().plan.state
    pid = state.placed[0].id
    with pytest.raises(PlanOpError) as exc:
        editing.remove_piece(editing.lock_piece(state, pid), pid)
    assert exc.value.kind == PlanOpErrorKind.CONFLICT


def test_remove_then_insert_restores_piece_count_and_position():
    state = _outcome().plan.state
    p = state.placed[0]
    removed = editing.remove_piece(state, p.id)
    assert p.id in {u.id for u in removed.unloaded}
    from app.models.schemas import InsertPieceRequest

    back = editing.insert_piece(removed, InsertPieceRequest(unloaded_id=p.id, x=p.x, y=p.y, z=p.z, dx=p.dx, dy=p.dy, dz=p.dz))
    assert len(back.placed) == len(state.placed)
    assert p.id not in {u.id for u in back.unloaded}


def test_unknown_piece_is_not_found():
    with pytest.raises(PlanOpError) as exc:
        editing.lock_piece(_outcome().plan.state, "no-existe")
    assert exc.value.kind == PlanOpErrorKind.NOT_FOUND


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------


def test_container_report_uses_the_given_label_without_any_persistence():
    """H8: el nombre del plan llega como dato -el reporte no sabe que existe
    SQLite."""
    from app.models.schemas import ContainerReportRequest

    plan = evaluate(_outcome().plan.state)
    req = ContainerReportRequest(include_overview_image=False)
    pdf = reporting.build_container_report(plan, req, reporting.PlanLabel(name="Pedido 1885", load_type="Loose Boxes"))
    assert pdf[:4] == b"%PDF"


def test_container_report_requires_overview_image_when_requested():
    from app.models.schemas import ContainerReportRequest

    with pytest.raises(PlanOpError) as exc:
        reporting.build_container_report(evaluate(_outcome().plan.state), ContainerReportRequest(include_overview_image=True))
    assert exc.value.kind == PlanOpErrorKind.INVALID_INPUT


def test_export_override_only_for_not_ready():
    plan = evaluate(_outcome().plan.state)
    validation = reporting.validate_plan(plan)
    assert validation.status.value in ("ready", "ready_with_warnings")
    assert reporting.export_override(validation) is None


def test_group_guide_requires_groups():
    from app.models.schemas import GuideReportRequest

    with pytest.raises(PlanOpError) as exc:
        reporting.require_groups(GuideReportRequest(groups=[]))
    assert exc.value.kind == PlanOpErrorKind.INVALID_INPUT


def test_evaluated_plan_is_immutable():
    plan = evaluate(_outcome().plan.state)
    with pytest.raises(Exception):
        plan.state = None  # type: ignore[misc]
    assert isinstance(plan, EvaluatedPlan)
