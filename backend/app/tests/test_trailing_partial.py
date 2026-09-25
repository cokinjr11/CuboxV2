"""CUBOX 2.0 -- Keep Groups Together, pedido de correccion "CORRECTNESS
GATE BEFORE FURTHER DEVELOPMENT -- COMPLETE-GROUP-BEFORE-PARTIAL +
SEQUENCE FILTER SAFETY", secciones 1-6: TP-1..TP-7. Prueban que la busqueda
de Grupo parcial de cola en core/section_coordinator.py (section_keep_
groups_pocket_reuse_stage3) agota TODOS los Grupos excluidos que pueden
llegar al 100% (Fase A, iterativa) ANTES de elegir un unico Grupo parcial
por payload (Fase B) -- nunca al reves, nunca por CANTIDAD de unidades
entre un candidato completable y uno parcial (bug real corregido: Stage
3.1 elegia INFINITE WINDOWS -6/11 parcial- sobre Window World -2/2
completo- solo porque 6 > 2).

TP-1 es tambien el "hard acceptance case" de la seccion 2 del pedido de
correccion (A completo, B=2/2 completable, C=6/11 con mayor yield crudo
-> B debe completarse antes de que C se considere parcial)."""

from app.core.final_validation import validate_for_export
from app.core.section_coordinator import (
    _try_complete_group_in_section,
    compute_group_contiguity_metrics,
    section_keep_groups_pocket_reuse_stage3,
)
from app.models.containers import get_container
from app.models.schemas import ItemType, WindowItem

CONTAINER = get_container("40ft_standard")


def _panel(code, group, n, w, h, t, priority=1, **kw):
    return [
        WindowItem(
            code=f"{code}{i}", description=f"{code}{i}", width=w, height=h, thickness=t,
            weight=10.0, quantity=1, item_type=ItemType.PANEL, stackable=False, group=group, priority=priority, **kw,
        )
        for i in range(n)
    ]


def _reports_by_group(reports):
    return {r.group: r for r in reports}


# ==========================================================================
# TP-1 / Hard acceptance case (seccion 2 del pedido): A completo, B=2/2
# completable, C=11 con yield crudo mayor (6) que B (2) si se comparara
# solo por cantidad. Esperado: B se completa ANTES de que C se considere
# parcial -nunca al reves solo porque 6 > 2.
# ==========================================================================
def test_tp1_hard_acceptance_group_b_completes_before_group_c_partial():
    group_a = _panel("A", "A", 28, w=2352.0, h=300.0, t=294.0, priority=1)
    group_b = _panel("B", "B", 2, w=2352.0, h=300.0, t=300.0, priority=2)
    group_c = _panel("C", "C", 11, w=2352.0, h=300.0, t=150.0, priority=3)

    result, sections, reports, _frontiers = section_keep_groups_pocket_reuse_stage3(group_a + group_b + group_c, CONTAINER)
    by_group = _reports_by_group(reports)

    assert by_group["A"].status == "COMPLETE" and by_group["A"].loaded_units == 28
    assert by_group["B"].status == "COMPLETE" and by_group["B"].loaded_units == 2, "B (2/2) debe completarse -nunca quedar excluido por el yield crudo mayor de C"
    assert by_group["C"].status == "PARTIAL" and by_group["C"].loaded_units == 6, "C sigue siendo el Grupo parcial de cola DESPUES de que B ya completo, usando lo que quede"
    metrics = compute_group_contiguity_metrics(sections)
    assert all(m["group_reentry_count"] == 0 for m in metrics.values())
    assert validate_for_export(result, CONTAINER) == []


# ==========================================================================
# TP-2: ningun Grupo excluido puede completar (C yields 6/11, D yields
# 2/5). Esperado: C elegido como UNICO parcial (mayor payload util), D
# permanece totalmente excluido.
# ==========================================================================
def test_tp2_no_group_can_complete_c_chosen_as_sole_partial():
    group_a = _panel("A", "A", 30, w=2352.0, h=300.0, t=294.0, priority=1)
    group_c = _panel("C", "C", 11, w=2352.0, h=300.0, t=150.0, priority=3)
    group_d = _panel("D", "D", 5, w=2352.0, h=300.0, t=1500.0, priority=3)

    result, sections, reports, _frontiers = section_keep_groups_pocket_reuse_stage3(group_a + group_c + group_d, CONTAINER)
    by_group = _reports_by_group(reports)

    assert by_group["A"].status == "COMPLETE" and by_group["A"].loaded_units == 30
    assert by_group["C"].status == "PARTIAL" and by_group["C"].loaded_units == 6
    assert by_group["D"].status == "NOT_LOADED" and by_group["D"].loaded_units == 0, "D (max 2/5) nunca debe elegirse -C aporta mas payload util"
    metrics = compute_group_contiguity_metrics(sections)
    assert all(m["group_reentry_count"] == 0 for m in metrics.values())
    assert validate_for_export(result, CONTAINER) == []


# ==========================================================================
# TP-3: C ya fue elegido como el Grupo parcial de cola. D tiene un item
# (de 2) que fisicamente cabria en la geometria restante -pero debe
# permanecer EXCLUIDO por completo (nunca un SEGUNDO Grupo parcial,
# aunque sea de una sola pieza).
# ==========================================================================
def test_tp3_second_group_stays_excluded_even_if_one_item_would_fit():
    group_a = _panel("A", "A", 30, w=2352.0, h=300.0, t=294.0, priority=1)
    group_c = _panel("C", "C", 11, w=2352.0, h=300.0, t=150.0, priority=3)
    group_d = _panel("D", "D", 2, w=2352.0, h=300.0, t=1700.0, priority=3)  # solo 1 de 2 cabria en la geometria restante, evaluado solo

    result, sections, reports, _frontiers = section_keep_groups_pocket_reuse_stage3(group_a + group_c + group_d, CONTAINER)
    by_group = _reports_by_group(reports)

    assert by_group["C"].status == "PARTIAL" and by_group["C"].loaded_units == 6
    assert by_group["D"].status == "NOT_LOADED" and by_group["D"].loaded_units == 0, "D debe quedar completamente excluido -ya hay un Grupo parcial (C), nunca un segundo"
    assert sum(1 for r in reports if r.status == "PARTIAL") == 1, "partial_groups <= 1 (seccion 5/6 del pedido)"
    assert validate_for_export(result, CONTAINER) == []


# ==========================================================================
# TP-4: el Grupo parcial elegido usa el pocket lateral de la Section de
# finalizacion (handoff) -no solo Sections nuevas.
# ==========================================================================
def test_tp4_partial_group_uses_completion_frontier_pocket():
    group_a = _panel("A", "A", 35, w=1200.0, h=300.0, t=300.0, priority=1)
    group_p = _panel("P", "P", 20, w=1000.0, h=300.0, t=300.0, priority=2)

    result, sections, reports, frontiers = section_keep_groups_pocket_reuse_stage3(group_a + group_p, CONTAINER)
    by_group = _reports_by_group(reports)

    assert by_group["A"].status == "COMPLETE"
    assert by_group["P"].status == "PARTIAL" and by_group["P"].loaded_units == 4
    a_completion_id, _ = frontiers["A"]
    completion_section = next(s for s in sections if s.section_id == a_completion_id)
    assert "P" in completion_section.group_phases, "P debe entrar via handoff al pocket lateral de la Section de finalizacion de A"
    assert "P" in completion_section.floor_items_by_group and completion_section.floor_items_by_group["P"]
    assert validate_for_export(result, CONTAINER) == []


# ==========================================================================
# TP-5: lo que no entro via handoff sigue en Sections NUEVAS hacia la
# puerta (mismo patron Section-first que cualquier Grupo activo).
# ==========================================================================
def test_tp5_partial_group_continues_into_new_sections_toward_door():
    group_a = _panel("A", "A", 35, w=1200.0, h=300.0, t=300.0, priority=1)
    group_p = _panel("P", "P", 20, w=1000.0, h=300.0, t=300.0, priority=2)

    result, sections, reports, frontiers = section_keep_groups_pocket_reuse_stage3(group_a + group_p, CONTAINER)
    a_completion_id, _ = frontiers["A"]
    p_only_sections = [s for s in sections if s.group_phases == ["P"]]
    assert p_only_sections, "P debe abrir al menos una Section NUEVA (solo P, no compartida con A) mas alla del handoff"
    assert all(s.section_id > a_completion_id for s in p_only_sections), "las Sections nuevas de P deben estar mas alla del frontier de A, hacia la puerta"
    assert validate_for_export(result, CONTAINER) == []


# ==========================================================================
# TP-6: el Grupo parcial NUNCA hace backfill hacia una Section historica
# ya congelada, aunque tenga un pocket mucho mas grande que la Section de
# finalizacion real (misma regla de Stage 2.2 -Chronological Completion
# Frontier- aplicada a la busqueda de parcial corregida).
# ==========================================================================
def test_tp6_partial_group_never_backfills_frozen_historical_section():
    def group_a_multi_section():
        outliers = [
            WindowItem(
                code=f"A-OUT{i}", description=f"A-OUT{i}", width=w, height=300.0, thickness=300.0,
                weight=10.0, quantity=1, item_type=ItemType.PANEL, stackable=False, group="A", priority=1,
            )
            for i, w in enumerate((4300.0, 2500.0, 1500.0), start=1)
        ]
        return outliers + _panel("AC", "A", 90, w=1000.0, h=2300.0, t=150.0)

    group_a = group_a_multi_section()
    result, sections, reports, _frontiers = section_keep_groups_pocket_reuse_stage3(group_a, CONTAINER)
    assert reports[0].status == "COMPLETE"

    all_placed = result.placed
    current_x1 = min(p.x for p in all_placed)
    last_section = sections[-1]
    front, back = last_section.x_front, last_section.x_back

    # P: entra en el pocket ANCHO de la PRIMERA Section (outlier de
    # 4300mm de profundidad deja ~mucho ancho libre lateral) pero NO en
    # el pocket angosto de la Section FINAL real (dy=900 > lo que queda
    # tras el outlier de 1500mm).
    group_p = _panel("P", "P", 6, w=900.0, h=2000.0, t=80.0, priority=2)
    handoff_floor, _handoff_level, _new_sections, _trial_x1, _residual_unloaded, newly_placed_all = _try_complete_group_in_section(
        group_p, front, back, all_placed, current_x1, CONTAINER, [], 0.0, "group_and_size", "P",
    )
    assert not handoff_floor, "el pocket de la Section FINAL real es demasiado angosto -no debe haber handoff aca"
    assert len(newly_placed_all) == 6

    older_sections = sections[:-1]
    violations = [
        p for p in newly_placed_all
        for s in older_sections
        if s.x_front - 1e-6 <= p.x and p.x + p.dx <= s.x_back + 1e-6
    ]
    assert violations == [], "P nunca debe aterrizar dentro del rango X de una Section historica ya congelada, aunque tenga mas espacio libre"


# ==========================================================================
# TP-7: group_reentry_count permanece 0 en un escenario con multiples
# Grupos completables + 1 parcial de cola (Fase A + Fase B combinadas).
# ==========================================================================
def test_tp7_group_reentry_count_remains_zero():
    group_a = _panel("A", "A", 28, w=2352.0, h=300.0, t=294.0, priority=1)
    group_b = _panel("B", "B", 2, w=2352.0, h=300.0, t=300.0, priority=2)
    group_c = _panel("C", "C", 11, w=2352.0, h=300.0, t=150.0, priority=3)

    result, sections, reports, _frontiers = section_keep_groups_pocket_reuse_stage3(group_a + group_b + group_c, CONTAINER)
    metrics = compute_group_contiguity_metrics(sections)
    for group_key, m in metrics.items():
        assert m["group_reentry_count"] == 0, f"Grupo {group_key} reaparecio despues de haber dejado de ser el ultimo activo"
    assert validate_for_export(result, CONTAINER) == []
