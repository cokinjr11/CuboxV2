"""Regression tests para el Section Coordinator, Stage 2 (Keep Groups
Together -- pedido "DYNAMIC SECTION ENGINE, FLOOR + POCKET ONLY", seccion
22: SECTION-1..8). Experimental (SECTION_KEEP_GROUPS_STAGE2) -- NUNCA
wireado a produccion en esta tarea (seccion 27 del pedido)."""

from app.core.final_validation import validate_for_export
from app.core.reserved_zones import central_aisle_zone
from app.core.section_coordinator import section_keep_groups_pocket_reuse
from app.models.containers import get_container
from app.models.schemas import ItemType, WindowItem

from .test_panel_pocket_reuse_real_plan_regression import _build_items as _real_67_line_items

CONTAINER = get_container("40ft_standard")


def _panel(code, group, n, w=1000.0, h=2700.0, t=150.0, **kw):
    return [
        WindowItem(
            code=f"{code}{i}", description=f"{code}{i}", width=w, height=h, thickness=t,
            weight=10.0, quantity=1, item_type=ItemType.PANEL, stackable=False, group=group, **kw,
        )
        for i in range(n)
    ]


def _no_real_overlap(placed) -> bool:
    for i in range(len(placed)):
        a = placed[i]
        for b in placed[i + 1 :]:
            ox = a.x < b.x + b.dx - 1e-6 and b.x < a.x + a.dx - 1e-6
            oy = a.y < b.y + b.dy - 1e-6 and b.y < a.y + a.dy - 1e-6
            oz = a.z < b.z + b.dz - 1e-6 and b.z < a.z + a.dz - 1e-6
            if ox and oy and oz:
                return False
    return True


def _contiguous(sections) -> bool:
    return all(abs(sections[i].x_front - sections[i + 1].x_back) < 1e-6 for i in range(len(sections) - 1))


# ==========================================================================
# SECTION-1: un Grupo abarca multiples Sections (pool acotado a 40 -45
# items fuerza mas de un modulo/Section).
# ==========================================================================
def test_section_1_one_group_spans_multiple_sections():
    items = _panel("A", "A", 45)
    result, sections, reports, frontiers = section_keep_groups_pocket_reuse(items, CONTAINER)

    assert len(sections) >= 2, "45 items en un pool acotado a 40 deberian abrir mas de una Section"
    assert all(s.group_phases == ["A"] for s in sections), "todas las Sections deberian pertenecer al mismo Grupo activo (un solo Grupo, sin handoff -no hay Grupo B)"
    assert len(result.placed) == 45
    assert validate_for_export(result, CONTAINER) == []
    assert _contiguous(sections)


# ==========================================================================
# SECTION-2: A completa, B arranca inmediatamente (sin hueco artificial) --
# ver docstring de section_coordinator.py: la transicion queda en el
# LIMITE entre Sections (contiguo, x_front de una = x_back de la
# siguiente), no una reutilizacion espacial DENTRO de una Section.
# ==========================================================================
def test_section_2_group_transition_has_no_artificial_gap():
    items = _panel("A", "A", 45) + _panel("B", "B", 10)
    result, sections, reports, frontiers = section_keep_groups_pocket_reuse(items, CONTAINER)

    assert _contiguous(sections), "no deberia existir ningun hueco artificial entre Sections de Grupos distintos"
    # cada Grupo debe aparecer en un bloque CONTIGUO de Sections (nunca
    # intercalado, saltando a otra Section y volviendo despues) -un
    # Grupo puede aparecer junto a OTRO en la MISMA Section (handoff,
    # Stage 2.1) pero nunca reaparecer en una Section posterior tras
    # haber empezado a ceder lugar a otro Grupo.
    groups_flat_in_order = [g for s in sections for g in s.group_phases]
    seen = set()
    prev = None
    for g in groups_flat_in_order:
        if g != prev:
            assert g not in seen, f"Grupo {g} reaparecio despues de haber cambiado de Grupo activo -no deberia fragmentarse"
            seen.add(g)
        prev = g
    assert validate_for_export(result, CONTAINER) == []


# ==========================================================================
# SECTION-3: un item del Grupo activo no entra en la Section actual, pero
# otro item del MISMO Grupo si -no debe cerrar temprano ni perder los que
# si entran.
# ==========================================================================
def test_section_3_does_not_close_early_when_one_item_does_not_fit():
    fitting = _panel("M", "M", 10)
    oversized = WindowItem(
        code="HUGE", description="HUGE", width=5000.0, height=5000.0, thickness=5000.0,
        weight=10.0, quantity=1, item_type=ItemType.PANEL, stackable=False, group="M",
    )
    items = fitting + [oversized]
    result, sections, reports, frontiers = section_keep_groups_pocket_reuse(items, CONTAINER)

    report = next(r for r in reports if r.group == "M")
    assert report.loaded_units == 10, "los 10 items que SI entran deberian cargarse aunque el HUGE no entre"
    assert validate_for_export(result, CONTAINER) == []


# ==========================================================================
# SECTION-4: pasillo central reservado -- las Sections deben respetar el
# tallado de Zona Reservada ya existente (Stage 1, sin tocar).
# ==========================================================================
def test_section_4_reserved_central_aisle_respected():
    aisle = central_aisle_zone(CONTAINER, 800.0)
    items = _panel("A", "A", 20)
    result, sections, reports, frontiers = section_keep_groups_pocket_reuse(items, CONTAINER, reserved_zones=[aisle])

    assert validate_for_export(result, CONTAINER, reserved_zones=[aisle]) == []
    for p in result.placed:
        overlaps_x = p.x < aisle.x + aisle.length and aisle.x < p.x + p.dx
        overlaps_y = p.y < aisle.y + aisle.width and aisle.y < p.y + p.dy
        assert not (overlaps_x and overlaps_y), f"{p.id} invade el pasillo central reservado"


# ==========================================================================
# SECTION-5: un Grupo grande completo le gana a varios Grupos chicos
# completos (COMPLETED GROUP PAYLOAD, seccion 14/21 del pedido de
# correccion), verificado a traves del coordinador de Secciones completo.
# ==========================================================================
def test_section_5_large_group_payload_beats_tiny_groups():
    items = _panel("BIG", "BIG", 40) + _panel("T1", "T1", 5) + _panel("T2", "T2", 5) + _panel("T3", "T3", 4)
    result, sections, reports, frontiers = section_keep_groups_pocket_reuse(items, CONTAINER)

    by_group = {r.group: r for r in reports}
    assert by_group["BIG"].status == "COMPLETE" and by_group["BIG"].loaded_units == 40
    assert len(result.placed) >= 40
    assert validate_for_export(result, CONTAINER) == []


# ==========================================================================
# SECTION-6: como maximo UN Grupo parcial de cola.
# ==========================================================================
def test_section_6_at_most_one_trailing_partial_group():
    items = _panel("A", "A", 45) + _panel("B", "B", 30)
    result, sections, reports, frontiers = section_keep_groups_pocket_reuse(items, CONTAINER)

    partial = [r for r in reports if r.status == "PARTIAL"]
    assert len(partial) <= 1, "no deberia haber mas de un Grupo parcial de cola"
    assert validate_for_export(result, CONTAINER) == []


# ==========================================================================
# SECTION-7: geometria fraccionaria -- protege el fix de produccion real
# (redondeo del knapsack nunca genera colision fisica), a traves del
# coordinador de Secciones.
# ==========================================================================
def test_section_7_fractional_geometry_never_collides():
    items = _panel("AF", "AF", 10, w=1000.37, h=2700.11, t=150.29)
    result, sections, reports, frontiers = section_keep_groups_pocket_reuse(items, CONTAINER)

    assert _no_real_overlap(result.placed)
    assert validate_for_export(result, CONTAINER) == []


# ==========================================================================
# SECTION-8: fixture real de 67 lineas, anonimizada (el mismo caso D12 /
# Grupo de 53 unidades que origino esta correccion).
# ==========================================================================
def test_section_8_real_67_line_fixture():
    container = get_container("40ft_high_cube")
    items = _real_67_line_items()
    result, sections, reports, frontiers = section_keep_groups_pocket_reuse(items, container)

    by_group = {r.group: r for r in reports}
    assert by_group["GROUP-1"].status == "COMPLETE" and by_group["GROUP-1"].loaded_units == 53, (
        "el Grupo de 53 unidades debe completar bajo el coordinador de Secciones, igual que bajo la correccion de payload"
    )
    assert len(result.placed) >= 54, "no deberia regresionar respecto del baseline corregido (54/67)"
    assert validate_for_export(result, container) == []
    assert _contiguous(sections)
    assert all(set(s.group_phases) <= {"GROUP-1", "GROUP-4", "(sin Grupo)"} for s in sections)


# ==========================================================================
# Determinismo (misma disciplina que Pocket Reuse) y regresion de no-
# colision real, sobre el dataset real completo.
# ==========================================================================
def test_section_coordinator_is_deterministic():
    container = get_container("40ft_high_cube")
    items = _real_67_line_items()

    def snapshot(result):
        return [(p.id, round(p.x, 6), round(p.y, 6), round(p.z, 6)) for p in result.placed]

    r1, _, _, _ = section_keep_groups_pocket_reuse(items, container)
    r2, _, _, _ = section_keep_groups_pocket_reuse(items, container)
    assert snapshot(r1) == snapshot(r2)
