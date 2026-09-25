"""Regression tests para el handoff en-Section, Stage 2.1 (pedido "KEEP
GROUPS TOGETHER -- STAGE 2.1 -- IN-SECTION GROUP HANDOFF", seccion 11:
HANDOFF-1..5). Grupo "A" siempre con priority=1 (procesa primero) y "B"
con priority=2 -sin esto el orden real de activacion lo decide
select_complete_groups (volumen ascendente), no el nombre del Grupo."""

from app.core.final_validation import validate_for_export
from app.core.reserved_zones import ReservedZone
from app.core.section_coordinator import section_keep_groups_pocket_reuse
from app.models.containers import get_container
from app.models.schemas import ItemType, WindowItem

CONTAINER = get_container("40ft_standard")


def _panel(code, group, n, w=1000.0, h=2300.0, t=150.0, priority=0, **kw):
    return [
        WindowItem(
            code=f"{code}{i}", description=f"{code}{i}", width=w, height=h, thickness=t,
            weight=10.0, quantity=1, item_type=ItemType.PANEL, stackable=False, group=group, priority=priority, **kw,
        )
        for i in range(n)
    ]


def _group_a():
    # D12-like: unica orientacion valida (P1-a, dx=4300) -- fuerza un
    # modulo mucho mas profundo que sus companeros (P1-b, dx=150), dejando
    # un pocket residual grande (~4000mm de profundidad) dentro del mismo
    # modulo, DESPUES de que A ya se completo.
    outlier = WindowItem(
        code="A-OUT", description="A-OUT", width=4300.0, height=300.0, thickness=300.0,
        weight=10.0, quantity=1, item_type=ItemType.PANEL, stackable=False, group="A", priority=1,
    )
    return [outlier] + _panel("A", "A", 3, priority=1)


# ==========================================================================
# HANDOFF-1: A completa; B tiene items que fisicamente entran en el pocket
# residual de A -B debe empezar en la MISMA Section.
# ==========================================================================
def test_handoff_1_group_b_enters_same_section_when_it_physically_fits():
    group_a = _group_a()
    group_b = _panel("B", "B", 2, w=600.0, h=900.0, t=40.0, priority=2)
    items = group_a + group_b

    result, sections, reports, frontiers = section_keep_groups_pocket_reuse(items, CONTAINER)
    by_group = {r.group: r for r in reports}

    assert by_group["A"].status == "COMPLETE"
    assert by_group["B"].status == "COMPLETE"
    assert len(sections) == 1, "B deberia entrar en la MISMA Section que A, no abrir una nueva"
    assert sections[0].group_phases == ["A", "B"]
    assert validate_for_export(result, CONTAINER) == []


# ==========================================================================
# HANDOFF-2: mismo A, pero B NO entra fisicamente en el pocket residual
# (necesita mas profundidad de la que el pocket tiene) -debe abrir una
# Section nueva, nunca forzarse.
# ==========================================================================
def test_handoff_2_group_b_opens_new_section_when_it_does_not_fit():
    group_a = _group_a()
    # width=4200 (unica orientacion valida fuerza dx=4200) -excede el
    # pocket residual real de A (~4000mm de profundidad).
    group_b = _panel("B2", "B2", 2, w=4200.0, h=2300.0, t=150.0, priority=2)
    items = group_a + group_b

    result, sections, reports, frontiers = section_keep_groups_pocket_reuse(items, CONTAINER)
    by_group = {r.group: r for r in reports}

    assert by_group["A"].status == "COMPLETE"
    assert by_group["B2"].status == "COMPLETE"
    assert len(sections) == 2, "B2 no entra en el pocket de A -deberia abrir una Section nueva"
    assert sections[0].group_phases == ["A"]
    assert sections[1].group_phases == ["B2"]
    assert validate_for_export(result, CONTAINER) == []


# ==========================================================================
# HANDOFF-3: A todavia tiene un item propio que entra en el pocket -A se
# lo queda (via su propio reuso de pockets, nunca tocado); B no puede
# entrar antes de que A este realmente completo.
# ==========================================================================
def test_handoff_3_group_a_claims_its_own_pocket_item_first():
    outlier = WindowItem(
        code="A-OUT", description="A-OUT", width=4300.0, height=300.0, thickness=300.0,
        weight=10.0, quantity=1, item_type=ItemType.PANEL, stackable=False, group="A", priority=1,
    )
    companions = _panel("A", "A", 3, priority=1)
    a_small = WindowItem(
        code="A-SMALL", description="A-SMALL", width=600.0, height=900.0, thickness=40.0,
        weight=10.0, quantity=1, item_type=ItemType.PANEL, stackable=False, group="A", priority=1,
    )
    group_a = [outlier] + companions + [a_small]
    group_b = _panel("B", "B", 2, w=600.0, h=900.0, t=40.0, priority=2)
    items = group_a + group_b

    result, sections, reports, frontiers = section_keep_groups_pocket_reuse(items, CONTAINER)
    by_group = {r.group: r for r in reports}

    assert by_group["A"].status == "COMPLETE" and by_group["A"].loaded_units == 5
    assert any(p.id.startswith("A-SMALL") for p in result.placed), "A-SMALL es del Grupo activo -A se lo queda, nunca queda disponible para B en su lugar"
    assert by_group["B"].loaded_units >= 1, "B deberia poder usar lo que quede del pocket despues de que A reclamo lo suyo"
    assert validate_for_export(result, CONTAINER) == []


# ==========================================================================
# HANDOFF-4: el orden de Grupos ya seleccionado (accepted_order) nunca se
# viola -B solo puede entrar en el pocket del Grupo INMEDIATAMENTE
# siguiente en ese orden, nunca de uno posterior "porque calza mejor".
# ==========================================================================
def test_handoff_4_never_skips_ahead_in_accepted_group_order():
    group_a = _group_a()
    # C (priority=3, el ULTIMO en orden) tendria items que calzarian
    # perfecto en el pocket de A -pero el handoff solo se le ofrece al
    # SIGUIENTE Grupo en el orden ya decidido (B), nunca a C directamente.
    group_b = _panel("B", "B", 2, w=4200.0, h=2300.0, t=150.0, priority=2)  # no entra en el pocket de A
    group_c = _panel("C", "C", 2, w=600.0, h=900.0, t=40.0, priority=3)  # SI entraria en el pocket de A

    result, sections, reports, frontiers = section_keep_groups_pocket_reuse(group_a + group_b + group_c, CONTAINER)
    by_group = {r.group: r for r in reports}

    assert by_group["A"].status == "COMPLETE"
    a_section = sections[0]
    assert a_section.group_phases == ["A"], "C nunca deberia aparecer en la Section de A -no es el Grupo siguiente en el orden"
    assert "C" not in a_section.floor_items_by_group
    assert validate_for_export(result, CONTAINER) == []


# ==========================================================================
# HANDOFF-5: una Zona Reservada divide el pocket residual -B solo usa el
# intervalo legal, nunca invade la zona.
# ==========================================================================
def test_handoff_5_reserved_zone_splits_pocket_b_uses_only_legal_interval():
    aisle = ReservedZone(x=8000.0, y=1000.0, z=0.0, length=1000.0, width=352.0, height=CONTAINER.height, label="aisle")
    group_a = _group_a()
    group_b = _panel("B", "B", 2, w=600.0, h=900.0, t=40.0, priority=2)
    items = group_a + group_b

    result, sections, reports, frontiers = section_keep_groups_pocket_reuse(items, CONTAINER, reserved_zones=[aisle])
    by_group = {r.group: r for r in reports}

    assert by_group["A"].status == "COMPLETE"
    assert by_group["B"].status == "COMPLETE"
    for p in result.placed:
        overlaps_x = p.x < aisle.x + aisle.length and aisle.x < p.x + p.dx
        overlaps_y = p.y < aisle.y + aisle.width and aisle.y < p.y + p.dy
        assert not (overlaps_x and overlaps_y), f"{p.id} invade la zona reservada durante el handoff"
    assert validate_for_export(result, CONTAINER, reserved_zones=[aisle]) == []
