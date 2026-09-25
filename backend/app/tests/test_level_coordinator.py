"""Regression tests para Stage 3 -- Support-Aware Level 2 dentro de
Dynamic Sections (pedido "KEEP GROUPS TOGETHER -- STAGE 3 -- SUPPORT-AWARE
3D LEVELS INSIDE DYNAMIC SECTIONS", secciones 29-38: LEVEL-1..3 +
aceptacion sintetica de soporte/peso/altura/multi-soporte/no-global-layer/
frontier+levels/no-reentry)."""

from app.core.final_validation import validate_for_export
from app.core.level_coordinator import try_place_level_2
from app.core.reserved_zones import ReservedZone
from app.core.section_coordinator import (
    compute_group_contiguity_metrics,
    section_keep_groups_pocket_reuse,
    section_keep_groups_pocket_reuse_stage3,
)
from app.core.sequence import compute_operational_warnings
from app.models.containers import get_container
from app.models.schemas import ItemType, PlacedPiece, WindowItem

CONTAINER = get_container("40ft_standard")


def _panel(code, group, n, w=600.0, h=900.0, t=150.0, priority=1, stackable=True, **kw):
    return [
        WindowItem(
            code=f"{code}{i}", description=f"{code}{i}", width=w, height=h, thickness=t,
            weight=10.0, quantity=1, item_type=ItemType.PANEL, stackable=stackable, group=group, priority=priority, **kw,
        )
        for i in range(n)
    ]


def _anchor(id_, x, y, dx, dy, dz, stackable=True, max_stack_weight=None):
    return PlacedPiece(
        id=id_, code=id_, description=id_, weight=10.0, stackable=stackable,
        x=x, y=y, z=0.0, dx=dx, dy=dy, dz=dz, orientation_label="FIXED",
        source_width=1.0, source_height=1.0, source_thickness=1.0, item_type=ItemType.PANEL,
        max_stack_weight=max_stack_weight,
    )


# ==========================================================================
# LEVEL-1: mismo Grupo -- A en piso, A arriba (misma Section, antes de
# abrir otra).
# ==========================================================================
def test_level_1_same_group_stacks_above_itself():
    items = _panel("A", "A", 6)
    result, sections, reports, frontiers = section_keep_groups_pocket_reuse_stage3(items, CONTAINER)

    assert len(result.placed) == 6
    upper = [p for p in result.placed if p.z > 1e-6]
    assert len(upper) >= 1, "al menos una pieza deberia terminar en Level 2"
    assert any(len(s.levels) > 0 for s in sections)
    assert validate_for_export(result, CONTAINER) == []


# ==========================================================================
# LEVEL-2: A completa en el piso; B entra arriba de A, misma Section
# (group_phases = [A, B], B fisicamente sobre A).
# ==========================================================================
def test_level_2_group_b_stacks_above_completed_group_a():
    group_a = _panel("A", "A", 3, priority=1)
    group_b = _panel("B", "B", 3, priority=2)
    result, sections, reports, frontiers = section_keep_groups_pocket_reuse_stage3(group_a + group_b, CONTAINER)

    by_group = {r.group: r for r in reports}
    assert by_group["A"].status == "COMPLETE"
    assert by_group["B"].status == "COMPLETE"
    assert len(sections) == 1
    assert sections[0].group_phases == ["A", "B"]
    upper_ids = {pid for lv in sections[0].levels for pid in lv.item_ids}
    assert any(pid.startswith("B") for pid in upper_ids), "B deberia terminar en Level 2, fisicamente sobre A"
    assert validate_for_export(result, CONTAINER) == []


# ==========================================================================
# LEVEL-3: A y B ambos entrarian en el mismo pocket superior, pero A sigue
# activo/incompleto -A gana, B no puede entrar todavia.
# ==========================================================================
def test_level_3_active_group_wins_over_waiting_group():
    group_a = _panel("A", "A", 90, priority=1)  # pool acotado a 40 -> A sigue activo por varias Sections
    group_b = _panel("B", "B", 3, priority=2)
    result, sections, reports, frontiers = section_keep_groups_pocket_reuse_stage3(group_a + group_b, CONTAINER)

    by_group = {r.group: r for r in reports}
    assert by_group["A"].status == "COMPLETE"
    assert by_group["B"].status == "COMPLETE"
    # Ninguna Section anterior a la de finalizacion de A puede tener items de B
    # (ni en piso ni en Level 2) -A tiene el primer derecho mientras siga activo.
    a_completion_id, _ = frontiers["A"]
    for s in sections:
        if s.section_id >= a_completion_id:
            continue
        assert "B" not in s.group_phases
        for lv in s.levels:
            assert "B" not in lv.items_by_group
    assert validate_for_export(result, CONTAINER) == []


# ==========================================================================
# Soporte insuficiente -> rechazado (unit-level, sobre try_place_level_2).
# ==========================================================================
def test_insufficient_support_is_rejected():
    small_anchor = _anchor("ANCH", 0.0, 0.0, 400.0, 400.0, 300.0)
    too_big = WindowItem(code="BIG", description="BIG", width=1200.0, height=1200.0, thickness=300.0,
                          weight=10.0, quantity=1, item_type=ItemType.PANEL, stackable=True, group="B")
    newly, remaining, report = try_place_level_2([small_anchor], [too_big], [small_anchor], CONTAINER, [], 0.0, group_key_for=lambda w: "B")
    assert report.candidates_accepted == 0
    assert report.rejection_counts.get("INSUFFICIENT_SUPPORT", 0) > 0
    assert not newly


# ==========================================================================
# MaxStackWeight excedido -> rechazado.
# ==========================================================================
def test_stack_weight_exceeded_is_rejected():
    light_anchor = _anchor("ANCH", 0.0, 0.0, 600.0, 900.0, 150.0, max_stack_weight=5.0)
    heavy = WindowItem(code="HEAVY", description="HEAVY", width=600.0, height=900.0, thickness=150.0,
                        weight=10.0, quantity=1, item_type=ItemType.PANEL, stackable=True, group="B")
    newly, remaining, report = try_place_level_2([light_anchor], [heavy], [light_anchor], CONTAINER, [], 0.0, group_key_for=lambda w: "B")
    assert report.candidates_accepted == 0
    assert report.rejection_counts.get("STACK_WEIGHT", 0) > 0
    assert not newly


# ==========================================================================
# Excede la altura del Load Space -> rechazado.
# ==========================================================================
def test_height_exceeded_is_rejected():
    high_anchor = _anchor("ANCH", 0.0, 0.0, 600.0, 900.0, CONTAINER.height - 100.0)
    tall = WindowItem(code="TALL", description="TALL", width=600.0, height=2000.0, thickness=150.0,
                       weight=10.0, quantity=1, item_type=ItemType.PANEL, stackable=True, group="B")
    newly, remaining, report = try_place_level_2([high_anchor], [tall], [high_anchor], CONTAINER, [], 0.0, group_key_for=lambda w: "B")
    assert report.candidates_accepted == 0
    assert report.rejection_counts.get("HEIGHT", 0) > 0
    assert not newly


# ==========================================================================
# Soporte multi-pieza coplanar -- aceptado si el soporte combinado (union
# de contacto real, sin doble conteo) cumple el umbral canonico.
# ==========================================================================
def test_multi_piece_coplanar_support_accepted():
    b1 = _anchor("B1", 0.0, 0.0, 300.0, 600.0, 150.0)
    b2 = _anchor("B2", 0.0, 600.0, 300.0, 600.0, 150.0)
    wide = WindowItem(code="WIDE", description="WIDE", width=1200.0, height=300.0, thickness=100.0,
                       weight=10.0, quantity=1, item_type=ItemType.PANEL, stackable=True, group="B")
    newly, remaining, report = try_place_level_2([b1, b2], [wide], [b1, b2], CONTAINER, [], 0.0, group_key_for=lambda w: "B")
    assert report.candidates_accepted == 1
    assert set(report.supporter_ids) == {"B1", "B2"}, "el candidato deberia registrar AMBAS piezas de soporte, no solo la que disparo el intento"


# ==========================================================================
# "No global layer pass" (seccion 36 del pedido -- test esencial): cuando
# apilar dentro de la Section actual mantiene/mejora el objetivo, Stage 3
# debe preferirlo a abrir una Section nueva en el piso.
# ==========================================================================
def test_no_global_layer_prefers_stacking_over_new_section():
    floor_batch = _panel("AF", "A", 4)
    extra = _panel("AE", "A", 3)
    items = floor_batch + extra

    result_v3, sections_v3, _, _ = section_keep_groups_pocket_reuse_stage3(items, CONTAINER)
    result_v2, sections_v2, _, _ = section_keep_groups_pocket_reuse(items, CONTAINER)

    assert len(result_v3.placed) == len(result_v2.placed) == 7
    assert len(sections_v3) < len(sections_v2), (
        "Stage 3 deberia necesitar MENOS Sections que Stage 2.2 (apila en vez de abrir una Section de piso nueva)"
    )
    assert any(len(s.levels) > 0 for s in sections_v3)
    assert validate_for_export(result_v3, CONTAINER) == []


# ==========================================================================
# Completion frontier tambien aplica en Z (seccion 37 del pedido): B solo
# puede usar espacio superior de la Section de FINALIZACION de A, nunca de
# una Section congelada anterior.
# ==========================================================================
def test_completion_frontier_applies_vertically():
    outliers = [
        WindowItem(code=f"A-OUT{i}", description=f"A-OUT{i}", width=w, height=300.0, thickness=300.0,
                   weight=10.0, quantity=1, item_type=ItemType.PANEL, stackable=True, group="A", priority=1)
        for i, w in enumerate((4300.0, 2500.0), start=1)
    ]
    group_a = outliers + _panel("AC", "A", 55, w=1000.0, h=2300.0, t=150.0, priority=1)
    group_b = _panel("B", "B", 3, priority=2)

    result, sections, reports, frontiers = section_keep_groups_pocket_reuse_stage3(group_a + group_b, CONTAINER)
    by_group = {r.group: r for r in reports}
    assert by_group["A"].status == "COMPLETE"

    a_completion_id, _ = frontiers["A"]
    for s in sections:
        if s.section_id >= a_completion_id:
            continue
        for lv in s.levels:
            assert "B" not in lv.items_by_group, f"B no deberia poder apilar en la Section congelada #{s.section_id}"
    assert validate_for_export(result, CONTAINER) == []


# ==========================================================================
# Sin reentrada de Grupo tras Stage 3 (piso + Level 2 combinados).
# ==========================================================================
def test_no_group_reentry_after_stage3():
    group_a = _panel("A", "A", 90, priority=1)
    group_b = _panel("B", "B", 10, priority=2)
    group_c = _panel("C", "C", 10, priority=3)

    result, sections, reports, frontiers = section_keep_groups_pocket_reuse_stage3(group_a + group_b + group_c, CONTAINER)
    metrics = compute_group_contiguity_metrics(sections)
    for group_key, m in metrics.items():
        assert m["group_reentry_count"] == 0, f"Grupo {group_key} reaparecio tras Stage 3"
    assert validate_for_export(result, CONTAINER) == []


# ==========================================================================
# Secuencia/dependencias reales tras Stage 3: sin ciclos, sin warnings
# ocultos.
# ==========================================================================
def test_sequence_valid_after_stage3():
    group_a = _panel("A", "A", 3, priority=1)
    group_b = _panel("B", "B", 3, priority=2)
    result, sections, reports, frontiers = section_keep_groups_pocket_reuse_stage3(group_a + group_b, CONTAINER)

    warnings = compute_operational_warnings(result.placed, CONTAINER)
    cycle_warnings = [w for w in warnings if w.type.value == "sequence_cycle"]
    assert not cycle_warnings
    assert validate_for_export(result, CONTAINER) == []


# ==========================================================================
# Zona Reservada respetada (piso Y Level 2) tras Stage 3.
# ==========================================================================
def test_reserved_zone_respected_with_levels():
    aisle = ReservedZone(x=6000.0, y=1000.0, z=0.0, length=1000.0, width=352.0, height=CONTAINER.height, label="aisle")
    items = _panel("A", "A", 8)
    result, sections, reports, frontiers = section_keep_groups_pocket_reuse_stage3(items, CONTAINER, reserved_zones=[aisle])

    for p in result.placed:
        overlaps_x = p.x < aisle.x + aisle.length and aisle.x < p.x + p.dx
        overlaps_y = p.y < aisle.y + aisle.width and aisle.y < p.y + p.dy
        assert not (overlaps_x and overlaps_y)
    assert validate_for_export(result, CONTAINER, reserved_zones=[aisle]) == []


# ==========================================================================
# Determinismo.
# ==========================================================================
def test_stage3_is_deterministic():
    items = _panel("A", "A", 20) + _panel("B", "B", 10, priority=2)

    def snapshot(result):
        return [(p.id, round(p.x, 6), round(p.y, 6), round(p.z, 6)) for p in result.placed]

    r1, _, _, _ = section_keep_groups_pocket_reuse_stage3(items, CONTAINER)
    r2, _, _, _ = section_keep_groups_pocket_reuse_stage3(items, CONTAINER)
    assert snapshot(r1) == snapshot(r2)
