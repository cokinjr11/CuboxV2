"""CUBOX 2.0 -- Keep Systems Together, pedido "KEEP SYSTEMS TOGETHER --
REUSE THE APPROVED SECTION-FIRST 3D ENGINE -- SYSTEM POLICY INSTEAD OF
GROUP POLICY", seccion 34: SYS-1..14.

Keep Systems Together reusa el MISMO motor Section-first 3D que Keep
Groups Together (core/section_coordinator.py:section_cluster_3d), solo
con `cluster_key=item.system` en vez de `item.group` (seccion 2 del
pedido). Estos tests reusan, deliberadamente, las MISMAS geometrias ya
probadas y verificadas para Keep Groups Together (test_trailing_partial.py
TP-1/2/3/6, test_live_group_integration.py LIVE-GROUP-9/10) -mismos
numeros exactos, solo relabeled de `.group` a `.system`- para demostrar
que es el MISMO algoritmo operando sobre un campo distinto, nunca una
segunda implementacion que pueda divergir."""

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.core.final_validation import validate_for_export
from app.core.geometry import TOL, Box, check_support
from app.core.optimize import run_optimization
from app.core.section_coordinator import (
    _try_complete_group_in_section,
    compute_group_contiguity_metrics,
    section_cluster_3d,
    section_keep_systems_pocket_reuse_stage3,
)
from app.core.sequence import compute_unload_dependencies, detect_sequence_cycle
from app.main import app
from app.models.containers import get_container
from app.models.schemas import ItemType, OptimizationMode, WindowItem
from app.tests.test_panel_pocket_reuse_real_plan_regression import _REAL_67_LINE_PLAN

CONTAINER = get_container("40ft_standard")
CONTAINER_HC = get_container("40ft_high_cube")
client = TestClient(app)


def _panel(code, system, n, w, h, t, priority=1, group="", stackable=False, **kw):
    return [
        WindowItem(
            code=f"{code}{i}", description=f"{code}{i}", width=w, height=h, thickness=t,
            weight=10.0, quantity=1, item_type=ItemType.PANEL, stackable=stackable, system=system, group=group, priority=priority, **kw,
        )
        for i in range(n)
    ]


# ==========================================================================
# SYS-1: System A abarca varias Sections; permanece activo hasta completar
# (multi-outlier, mismo patron que test_section_frontier.py).
# ==========================================================================
def test_sys1_system_spans_multiple_sections_remains_active_until_complete():
    outliers = [
        WindowItem(
            code=f"A-OUT{i}", description=f"A-OUT{i}", width=w, height=300.0, thickness=300.0,
            weight=10.0, quantity=1, item_type=ItemType.PANEL, stackable=False, system="A", priority=1,
        )
        for i, w in enumerate((4300.0, 2500.0, 1500.0), start=1)
    ]
    system_a = outliers + _panel("AC", "A", 90, w=1000.0, h=2300.0, t=150.0)

    result, sections, reports, frontiers = section_keep_systems_pocket_reuse_stage3(system_a, CONTAINER)
    assert reports[0].group == "A" and reports[0].status == "COMPLETE"
    a_sections = [s for s in sections if "A" in s.group_phases]
    assert len(a_sections) > 1, "System A debe abarcar mas de una Section (mismo dataset que fuerza multi-Section en Keep Groups)"
    assert validate_for_export(result, CONTAINER) == []


# ==========================================================================
# SYS-2: A completa a mitad de una Section; B entra en esa MISMA Section
# (handoff, mismo patron que TP-4).
# ==========================================================================
def test_sys2_system_b_starts_in_same_section_a_completes_in():
    system_a = _panel("A", "A", 35, w=1200.0, h=300.0, t=300.0, priority=1)
    system_b = _panel("B", "B", 20, w=1000.0, h=300.0, t=300.0, priority=2)

    result, sections, reports, frontiers = section_keep_systems_pocket_reuse_stage3(system_a + system_b, CONTAINER)
    by_sys = {r.group: r for r in reports}
    assert by_sys["A"].status == "COMPLETE"
    a_completion_id, _ = frontiers["A"]
    completion_section = next(s for s in sections if s.section_id == a_completion_id)
    assert "B" in completion_section.group_phases, "System B debe entrar via handoff en la Section de finalizacion de A"
    assert completion_section.group_phases.index("A") < completion_section.group_phases.index("B"), "cronologia: A antes que B"


# ==========================================================================
# SYS-3: A permanece incompleto y puede usar Level 2 -- A recibe Level 2
# ANTES de que B empiece (mismo Section-first vertical-priority que Keep
# Groups).
# ==========================================================================
def test_sys3_incomplete_system_gets_level_2_before_next_system_starts():
    system_a = _panel("A", "A", 28, w=2352.0, h=300.0, t=294.0, priority=1, stackable=True, stackable_override=True)
    system_b = _panel("B", "B", 2, w=2352.0, h=300.0, t=300.0, priority=2, stackable=True, stackable_override=True)

    result, sections, reports, frontiers = section_keep_systems_pocket_reuse_stage3(system_a + system_b, CONTAINER)
    by_sys = {r.group: r for r in reports}
    assert by_sys["A"].status == "COMPLETE"
    section_1 = sections[0]
    assert len(section_1.levels) > 0 and sum(len(lv.item_ids) for lv in section_1.levels) > 0, "System A debe usar Level 2 en su propia Section antes de que B aparezca"
    assert section_1.group_phases == ["A"], "B no debe aparecer en la Section de Level 2 de A si A todavia no completo ahi"


# ==========================================================================
# SYS-4: A completo; B puede apilarse sobre A en la Section de
# finalizacion (soporte real, canonico).
# ==========================================================================
def test_sys4_system_b_can_stack_on_completed_system_a():
    system_a = _panel("A", "A", 35, w=1200.0, h=300.0, t=300.0, priority=1, stackable=True, stackable_override=True)
    system_b = _panel("B", "B", 20, w=1000.0, h=300.0, t=300.0, priority=2, stackable=True, stackable_override=True)

    result, sections, reports, frontiers = section_keep_systems_pocket_reuse_stage3(system_a + system_b, CONTAINER)
    a_completion_id, _ = frontiers["A"]
    completion_section = next(s for s in sections if s.section_id == a_completion_id)
    b_upper = [lv for lv in completion_section.levels if any(iid.startswith("B") for iid in lv.item_ids)]
    if b_upper:
        boxes = [
            Box(id=p.id, x=p.x, y=p.y, z=p.z, dx=p.dx, dy=p.dy, dz=p.dz, stackable=p.stackable)
            for p in result.placed
        ]
        by_id = {p.id: p for p in result.placed}
        for lv in b_upper:
            for iid in lv.item_ids:
                pbox = next(b for b in boxes if b.id == iid)
                ok, _reason = check_support(pbox, [b for b in boxes if b.id != iid])
                assert ok, f"{iid} (System B, apilado sobre A) debe tener soporte fisico real valido"


# ==========================================================================
# SYS-5: sin reentrada A -> B -> A (metrica canonica).
# ==========================================================================
def test_sys5_no_system_reentry():
    system_a = _panel("A", "A", 28, w=2352.0, h=300.0, t=294.0, priority=1)
    system_b = _panel("B", "B", 2, w=2352.0, h=300.0, t=300.0, priority=2)
    system_c = _panel("C", "C", 11, w=2352.0, h=300.0, t=150.0, priority=3)

    result, sections, reports, frontiers = section_keep_systems_pocket_reuse_stage3(system_a + system_b + system_c, CONTAINER)
    metrics = compute_group_contiguity_metrics(sections)
    assert all(m["group_reentry_count"] == 0 for m in metrics.values())


# ==========================================================================
# SYS-6: diferencias de Group DENTRO del mismo System no dividen el
# cluster -seccion 19/3 del pedido: todos los items de System A (sin
# importar su Group) pertenecen al MISMO cluster activo.
# ==========================================================================
def test_sys6_group_differences_inside_same_system_do_not_split_cluster():
    system_a = (
        _panel("AX", "A", 10, w=1000.0, h=2300.0, t=150.0, group="X")
        + _panel("AY", "A", 8, w=1000.0, h=2300.0, t=150.0, group="Y")
    )
    result, sections, reports, frontiers = section_keep_systems_pocket_reuse_stage3(system_a, CONTAINER)
    assert reports[0].group == "A"
    assert reports[0].loaded_units == 18 and reports[0].status == "COMPLETE"
    # Ambos Group X e Y deben terminar en las MISMAS Sections del cluster
    # System A -nunca separados por su Group (seccion 3 del pedido: "Group
    # identity must NOT restrict spatial placement").
    groups_placed = {p.group for p in result.placed}
    assert groups_placed == {"X", "Y"}
    assert validate_for_export(result, CONTAINER) == []


# ==========================================================================
# SYS-7: System completo le gana a un System parcial de mayor yield crudo
# (mismo hard acceptance case que TP-1, relabeled a System).
# ==========================================================================
def test_sys7_complete_system_beats_higher_yield_partial_system():
    system_a = _panel("A", "A", 28, w=2352.0, h=300.0, t=294.0, priority=1)
    system_b = _panel("B", "B", 2, w=2352.0, h=300.0, t=300.0, priority=2)
    system_c = _panel("C", "C", 11, w=2352.0, h=300.0, t=150.0, priority=3)

    result, sections, reports, frontiers = section_keep_systems_pocket_reuse_stage3(system_a + system_b + system_c, CONTAINER)
    by_sys = {r.group: r for r in reports}
    assert by_sys["A"].status == "COMPLETE" and by_sys["A"].loaded_units == 28
    assert by_sys["B"].status == "COMPLETE" and by_sys["B"].loaded_units == 2, "B (2/2) debe completarse -nunca quedar excluido por el yield crudo mayor de C"
    assert by_sys["C"].status == "PARTIAL" and by_sys["C"].loaded_units == 6


# ==========================================================================
# SYS-8: como maximo UN System parcial de cola.
# ==========================================================================
def test_sys8_at_most_one_trailing_partial_system():
    system_a = _panel("A", "A", 30, w=2352.0, h=300.0, t=294.0, priority=1)
    system_c = _panel("C", "C", 11, w=2352.0, h=300.0, t=150.0, priority=3)
    system_d = _panel("D", "D", 5, w=2352.0, h=300.0, t=1500.0, priority=3)

    result, sections, reports, frontiers = section_keep_systems_pocket_reuse_stage3(system_a + system_c + system_d, CONTAINER)
    partial_count = sum(1 for r in reports if r.status == "PARTIAL")
    assert partial_count <= 1


# ==========================================================================
# SYS-9: el System parcial de cola no puede ser seguido de otro System
# (D permanece excluido aunque una unidad suya fisicamente cupiera).
# ==========================================================================
def test_sys9_partial_system_cannot_be_followed_by_another_system():
    system_a = _panel("A", "A", 30, w=2352.0, h=300.0, t=294.0, priority=1)
    system_c = _panel("C", "C", 11, w=2352.0, h=300.0, t=150.0, priority=3)
    system_d = _panel("D", "D", 2, w=2352.0, h=300.0, t=1700.0, priority=3)

    result, sections, reports, frontiers = section_keep_systems_pocket_reuse_stage3(system_a + system_c + system_d, CONTAINER)
    by_sys = {r.group: r for r in reports}
    assert by_sys["C"].status == "PARTIAL"
    assert by_sys["D"].status == "NOT_LOADED" and by_sys["D"].loaded_units == 0
    assert sum(1 for r in reports if r.status == "PARTIAL") == 1


# ==========================================================================
# SYS-10: Sections historicas congeladas nunca se rellenan hacia atras
# (mismo dataset/decoy de pocket ancho que TP-6).
# ==========================================================================
def test_sys10_frozen_historical_sections_never_backfilled():
    outliers = [
        WindowItem(
            code=f"A-OUT{i}", description=f"A-OUT{i}", width=w, height=300.0, thickness=300.0,
            weight=10.0, quantity=1, item_type=ItemType.PANEL, stackable=False, system="A", priority=1,
        )
        for i, w in enumerate((4300.0, 2500.0, 1500.0), start=1)
    ]
    system_a = outliers + _panel("AC", "A", 90, w=1000.0, h=2300.0, t=150.0)
    result, sections, reports, _frontiers = section_keep_systems_pocket_reuse_stage3(system_a, CONTAINER)
    assert reports[0].status == "COMPLETE"

    all_placed = result.placed
    current_x1 = min(p.x for p in all_placed)
    last_section = sections[-1]
    front, back = last_section.x_front, last_section.x_back

    system_p = _panel("P", "P", 6, w=900.0, h=2000.0, t=80.0, priority=2)
    handoff_floor, _handoff_level, _new_sections, _trial_x1, _residual_unloaded, newly_placed_all = _try_complete_group_in_section(
        system_p, front, back, all_placed, current_x1, CONTAINER, [], 0.0, "group_and_size", "P", OptimizationMode.KEEP_SYSTEMS,
    )
    assert not handoff_floor, "el pocket de la Section FINAL real es demasiado angosto -no debe haber handoff aca"
    older_sections = sections[:-1]
    violations = [
        p for p in newly_placed_all
        for s in older_sections
        if s.x_front - 1e-6 <= p.x and p.x + p.dx <= s.x_back + 1e-6
    ]
    assert violations == []


# ==========================================================================
# SYS-11: guardar y reabrir preserva la geometria apilada por System.
# ==========================================================================
def test_sys11_save_reopen_preserves_system_stacked_geometry(tmp_path):
    from app.api import routes
    from app.core.plan_store import PlanRepository

    repo = PlanRepository(tmp_path / "sys11.db")
    app.dependency_overrides[routes.get_plan_repository] = lambda: repo
    try:
        items_payload = [
            {
                "code": code, "description": code, "width": w, "height": h, "thickness": t,
                "weight": 10.0, "quantity": 1, "item_type": "panel", "stackable": True,
                "stackable_override": True, "group": group, "system": system,
            }
            for code, w, h, t, group, system in _REAL_67_LINE_PLAN
        ]
        body = {
            "items": items_payload, "container_id": "40ft_high_cube", "name": "SYS-11",
            "optimization_mode": "keep_systems",
        }
        created = client.post("/api/plans", json=body).json()
        before = {p["id"]: p for p in created["best"]["placed"]}
        assert len(before) > 0

        reopened = client.get(f"/api/plans/{created['plan_id']}").json()
        after = {p["id"]: p for p in reopened["result"]["placed"]}

        assert set(before) == set(after)
        fields = ["x", "y", "z", "dx", "dy", "dz", "orientation_label", "system", "group", "stackable"]
        for pid, b in before.items():
            for f in fields:
                assert b[f] == after[pid][f], f"{pid}.{f} changed on reopen: {b[f]} -> {after[pid][f]}"

        before_upper = [p for p in before.values() if p["z"] > 1e-6]
        assert before_upper, "esta fixture real debe ejercitar Level 2 de verdad"
        assert all(after[p["id"]]["z"] > 1e-6 for p in before_upper)
    finally:
        app.dependency_overrides.pop(routes.get_plan_repository, None)


# ==========================================================================
# SYS-12: dependencias de secuencia correctas a traves de la frontera de
# System (soporte real, sin ciclo).
# ==========================================================================
def test_sys12_sequence_dependencies_correct_across_system_boundary():
    items = [
        WindowItem(
            code=code, description=code, width=w, height=h, thickness=t, weight=10.0, quantity=1,
            item_type=ItemType.PANEL, stackable=True, stackable_override=True, group=group, system=system,
        )
        for code, w, h, t, group, system in _REAL_67_LINE_PLAN
    ]
    best, _alternatives = run_optimization(items, CONTAINER_HC, OptimizationMode.KEEP_SYSTEMS)
    unload_deps = compute_unload_dependencies(best.placed)
    assert detect_sequence_cycle(unload_deps) is None

    boxes = [
        Box(id=p.id, x=p.x, y=p.y, z=p.z, dx=p.dx, dy=p.dy, dz=p.dz, stackable=p.stackable, max_stack_weight=p.max_stack_weight)
        for p in best.placed
    ]
    by_id_box = {b.id: b for b in boxes}
    upper = [p for p in best.placed if p.z > 1e-6]
    assert upper, "esta fixture debe ejercitar Level 2 de verdad"
    for p in upper:
        pbox = by_id_box[p.id]
        support_ok, _reason = check_support(pbox, [b for b in boxes if b.id != p.id])
        assert support_ok
        real_supporters = [
            b.id for b in boxes
            if b.id != p.id and abs(b.top_z - pbox.z) < TOL
            and max(0.0, min(pbox.x + pbox.dx, b.x + b.dx) - max(pbox.x, b.x)) * max(0.0, min(pbox.y + pbox.dy, b.y + b.dy) - max(pbox.y, b.y)) > TOL
        ]
        assert real_supporters
        for supporter_id in real_supporters:
            assert p.id in unload_deps.get(supporter_id, []), f"{supporter_id} (base) debe depender de {p.id} para la descarga"


# ==========================================================================
# SYS-13: BEST_SPACE_UTILIZATION sigue permitiendo mezclar Systems (A -> B
# -> A permitido) -nunca rutea al motor Section-3D.
# ==========================================================================
def test_sys13_best_space_still_allowed_to_mix_systems():
    items = _panel("A1", "A", 2, w=500, h=1800, t=40) + _panel("B1", "B", 2, w=500, h=1800, t=40)
    with patch(
        "app.core.section_coordinator.section_keep_systems_pocket_reuse_stage3",
        wraps=section_keep_systems_pocket_reuse_stage3,
    ) as spy:
        run_optimization(items, CONTAINER, OptimizationMode.BEST_SPACE)
        assert not spy.called


# ==========================================================================
# SYS-14: el resultado real de Keep Groups Together permanece EXACTAMENTE
# igual (regresion, seccion 21/36 del pedido: "Do not regress it while
# generalizing the engine").
# ==========================================================================
def test_sys14_keep_groups_real_fixture_unchanged():
    from app.core.section_coordinator import section_keep_groups_pocket_reuse_stage3

    items = [
        WindowItem(
            code=code, description=code, width=w, height=h, thickness=t, weight=10.0, quantity=1,
            item_type=ItemType.PANEL, stackable=True, stackable_override=True, group=group, system=system,
        )
        for code, w, h, t, group, system in _REAL_67_LINE_PLAN
    ]
    result, sections, reports, _frontiers = section_keep_groups_pocket_reuse_stage3(items, CONTAINER_HC)
    assert len(result.placed) == 67
    assert len(result.unloaded) == 0
    upper = [p for p in result.placed if p.z > 1e-6]
    assert len(upper) == 8
    assert sum(1 for p in upper if p.group == "GROUP-1") == 6
    assert sum(1 for p in upper if p.group == "GROUP-2") == 2
    used_length = CONTAINER_HC.length - min(p.x for p in result.placed)
    assert abs(used_length - 11318.0) < 1.0
    metrics = compute_group_contiguity_metrics(sections)
    assert all(m["group_reentry_count"] == 0 for m in metrics.values())
    assert validate_for_export(result, CONTAINER_HC) == []
