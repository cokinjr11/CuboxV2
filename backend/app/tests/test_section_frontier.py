"""Regression tests para la Chronological Completion Frontier, Stage 2.2
(pedido "KEEP GROUPS TOGETHER -- STAGE 2.2 -- CHRONOLOGICAL COMPLETION
FRONTIER", seccion 14: FRONTIER-1..5). Corrige el defecto real de Stage
2.1 (A -> B -> A: el coordinador buscaba hacia atras entre TODAS las
Sections ya cerradas de A, en vez de ofrecer SOLO la ultima -su Section
de finalizacion/frontier- al siguiente Grupo)."""

from app.core.final_validation import validate_for_export
from app.core.reserved_zones import ReservedZone
from app.core.section_coordinator import compute_group_contiguity_metrics, section_keep_groups_pocket_reuse
from app.models.containers import get_container
from app.models.schemas import ItemType, WindowItem

CONTAINER = get_container("40ft_standard")


def _panel(code, group, n, w=1000.0, h=2300.0, t=150.0, priority=1, **kw):
    return [
        WindowItem(
            code=f"{code}{i}", description=f"{code}{i}", width=w, height=h, thickness=t,
            weight=10.0, quantity=1, item_type=ItemType.PANEL, stackable=False, group=group, priority=priority, **kw,
        )
        for i in range(n)
    ]


def _group_a_multi_section():
    # 3 outliers de profundidad decreciente + 90 companeros delgados
    # (pool acotado a 40 -fuerza multiples modulos/Sections, cada uno con
    # su propio pocket residual de tamano DISTINTO) -- Grupo A termina
    # abarcando varias Sections, cada una con geometria de pocket propia.
    outliers = [
        WindowItem(
            code=f"A-OUT{i}", description=f"A-OUT{i}", width=w, height=300.0, thickness=300.0,
            weight=10.0, quantity=1, item_type=ItemType.PANEL, stackable=False, group="A", priority=1,
        )
        for i, w in enumerate((4300.0, 2500.0, 1500.0), start=1)
    ]
    return outliers + _panel("AC", "A", 90)


def _group_a_two_sections():
    outliers = [
        WindowItem(
            code=f"A-OUT{i}", description=f"A-OUT{i}", width=w, height=300.0, thickness=300.0,
            weight=10.0, quantity=1, item_type=ItemType.PANEL, stackable=False, group="A", priority=1,
        )
        for i, w in enumerate((4300.0, 2500.0), start=1)
    ]
    return outliers + _panel("AC", "A", 55)


# ==========================================================================
# FRONTIER-1: A abarca varias Sections; B entra en la Section FINAL de A
# (su frontier de finalizacion). Esperado: ...A | A+B.
# ==========================================================================
def test_frontier_1_group_b_enters_final_a_section():
    group_a = _group_a_multi_section()
    group_b = _panel("B", "B", 2, w=800.0, h=2000.0, t=80.0, priority=2)

    result, sections, reports, frontiers = section_keep_groups_pocket_reuse(group_a + group_b, CONTAINER)
    by_group = {r.group: r for r in reports}

    assert by_group["A"].status == "COMPLETE"
    assert by_group["B"].status == "COMPLETE"
    a_completion_section_id, _ = frontiers["A"]
    final_a_section = next(s for s in sections if s.section_id == a_completion_section_id)
    assert "B" in final_a_section.group_phases, "B deberia entrar en la Section de finalizacion de A"
    assert all(
        "B" not in s.floor_items_by_group for s in sections if s.section_id < a_completion_section_id
    ), "B no deberia aparecer en NINGUNA Section ANTERIOR a la de finalizacion de A (puede seguir en Sections propias mas alla de esa)"
    assert validate_for_export(result, CONTAINER) == []


# ==========================================================================
# FRONTIER-2: B fisicamente entra en el pocket de una Section VIEJA de A,
# pero NO en la Section de finalizacion -no debe hacer backfill hacia
# atras; debe abrir una Section nueva.
# ==========================================================================
def test_frontier_2_never_backfills_older_section_opens_new_one():
    group_a = _group_a_multi_section()
    # entra en el pocket angosto de la PRIMERA Section (depth~4150,
    # width~52 o depth~100,width~1052) pero NO en el pocket chico de la
    # Section final (depth=300, width=852) -dy=900 > 852.
    group_b = _panel("B", "B", 2, w=900.0, h=2000.0, t=80.0, priority=2)

    result, sections, reports, frontiers = section_keep_groups_pocket_reuse(group_a + group_b, CONTAINER)
    by_group = {r.group: r for r in reports}

    assert by_group["A"].status == "COMPLETE"
    assert by_group["B"].status == "COMPLETE"
    a_completion_section_id, _ = frontiers["A"]
    assert all(
        "B" not in s.floor_items_by_group for s in sections if s.section_id <= a_completion_section_id
    ), "B nunca deberia hacer backfill hacia atras en ninguna Section de A, ni siquiera la de finalizacion si no entra fisicamente"
    b_sections = [s for s in sections if "B" in s.group_phases]
    assert all(s.section_id > a_completion_section_id for s in b_sections), "B deberia abrir una Section NUEVA, mas alla del frontier de A"
    assert validate_for_export(result, CONTAINER) == []


# ==========================================================================
# FRONTIER-3: A completa en su Section 2 (abarca solo 2); B entra ahi
# mismo. Esperado: A | A+B.
# ==========================================================================
def test_frontier_3_group_b_enters_section_where_a_completes():
    group_a = _group_a_two_sections()
    group_b = _panel("B", "B", 2, w=900.0, h=2000.0, t=150.0, priority=2)

    result, sections, reports, frontiers = section_keep_groups_pocket_reuse(group_a + group_b, CONTAINER)
    by_group = {r.group: r for r in reports}

    assert by_group["A"].status == "COMPLETE"
    assert by_group["B"].status == "COMPLETE"
    assert len(sections) == 2, "A completa en su segunda Section -B deberia entrar ahi, sin abrir una tercera"
    assert sections[0].group_phases == ["A"]
    assert sections[1].group_phases == ["A", "B"]
    assert validate_for_export(result, CONTAINER) == []


# ==========================================================================
# FRONTIER-4: A -> B -> C, sin reentrada de ningun Grupo.
# ==========================================================================
def test_frontier_4_no_group_reentry_across_three_groups():
    group_a = _group_a_multi_section()
    group_b = _panel("B4", "B4", 5, priority=2)
    group_c = _panel("C", "C", 5, priority=3)

    result, sections, reports, frontiers = section_keep_groups_pocket_reuse(group_a + group_b + group_c, CONTAINER)
    metrics = compute_group_contiguity_metrics(sections)

    for group_key, m in metrics.items():
        assert m["group_reentry_count"] == 0, f"Grupo {group_key} reaparecio despues de haber dejado de ser el ultimo activo"
    assert validate_for_export(result, CONTAINER) == []


# ==========================================================================
# FRONTIER-5: una Zona Reservada dentro de la Section de finalizacion de
# A -B solo usa el intervalo lateral legal, nunca invade la zona.
# ==========================================================================
def test_frontier_5_reserved_zone_in_completion_section():
    aisle = ReservedZone(x=6000.0, y=1000.0, z=0.0, length=1000.0, width=352.0, height=CONTAINER.height, label="aisle")
    group_a = _group_a_two_sections()
    group_b = _panel("B3", "B3", 2, w=900.0, h=2000.0, t=150.0, priority=2)

    result, sections, reports, frontiers = section_keep_groups_pocket_reuse(group_a + group_b, CONTAINER, reserved_zones=[aisle])
    by_group = {r.group: r for r in reports}

    assert by_group["A"].status == "COMPLETE"
    assert by_group["B3"].status == "COMPLETE"
    for p in result.placed:
        overlaps_x = p.x < aisle.x + aisle.length and aisle.x < p.x + p.dx
        overlaps_y = p.y < aisle.y + aisle.width and aisle.y < p.y + p.dy
        assert not (overlaps_x and overlaps_y), f"{p.id} invade la zona reservada dentro de la Section de finalizacion"
    assert validate_for_export(result, CONTAINER, reserved_zones=[aisle]) == []
