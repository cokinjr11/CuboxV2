"""Regression tests para Keep Groups Together -- seleccion de Grupos
COMPLETOS antes del packing espacial (pedido "CUBOX 2.0 -- KEEP GROUPS
TOGETHER -- COMPLETE-GROUP SELECTION BEFORE PACKING", seccion 16)."""

from app.core.final_validation import validate_for_export
from app.core.group_selection import group_completion_report
from app.core.manual_move import validate_move
from app.core.optimize import run_optimization
from app.models.containers import build_custom_load_space, get_container
from app.models.schemas import ItemType, LoadSpaceType, OptimizationMode, PlacedPiece, WindowItem

from .test_panel_pocket_reuse_real_plan_regression import _build_items as _real_67_line_items


def _box(code, group, n, w=400.0, h=400.0, t=250.0, **kw):
    return [
        WindowItem(
            code=f"{code}{i}", description=f"{code}{i}", width=w, height=h, thickness=t,
            weight=20.0, quantity=1, item_type=ItemType.BOX, stackable=True, group=group, **kw,
        )
        for i in range(n)
    ]


def _abc_container(height=750.0):
    # A=30, B=25, C=20 (seccion 8 del pedido) -- headroom para 2 Grupos
    # completos pero no los 3 (2000x2000, floor=25 cajas, height=750=3
    # capas=75 slots; A+C=50 entra limpio, A+C+B=75 no siempre).
    return build_custom_load_space("ABC-TEST", LoadSpaceType.CUSTOM, length=2000.0, width=2000.0, height=height, max_weight=1_000_000.0)


def _abc_items():
    return _box("A", "A", 30) + _box("B", "B", 25) + _box("C", "C", 20)


# ==========================================================================
# 1/2. Dos Grupos completos le ganan a tres Grupos parciales; la
# penalizacion de Grupo parcial domina una ganancia chica de unidades
# sueltas (seccion 4/8 del pedido -caso de aceptacion EXACTO).
# ==========================================================================
def test_two_complete_groups_beat_three_partial_groups():
    container = _abc_container()
    items = _abc_items()

    best, _alts = run_optimization(items, container, OptimizationMode.KEEP_GROUPS)
    reports = {r.group: r for r in group_completion_report(items, best.placed)}

    assert reports["A"].status == "COMPLETE" and reports["A"].loaded_units == 30
    assert reports["C"].status == "COMPLETE" and reports["C"].loaded_units == 20
    assert reports["B"].status == "NOT_LOADED" and reports["B"].loaded_units == 0
    assert len(best.placed) == 50
    assert validate_for_export(best, container) == []


def test_partial_group_penalty_dominates_small_loaded_unit_gain():
    # Mismo dataset, Best Space Utilization: sin la penalizacion de Grupo
    # parcial, el motor SI mezcla libremente y carga MAS piezas totales a
    # costa de dejar un Grupo incompleto -confirma que Keep Groups
    # Together y Best Space Utilization producen resultados MATERIALMENTE
    # distintos sobre el MISMO dataset (seccion 9 del pedido).
    container = _abc_container()
    items = _abc_items()

    keep_groups, _ = run_optimization(items, container, OptimizationMode.KEEP_GROUPS)
    best_space, _ = run_optimization(items, container, OptimizationMode.BEST_SPACE)

    kg_reports = group_completion_report(items, keep_groups.placed)
    bs_reports = group_completion_report(items, best_space.placed)

    kg_complete = sum(1 for r in kg_reports if r.status == "COMPLETE")
    bs_complete = sum(1 for r in bs_reports if r.status == "COMPLETE")

    assert kg_complete >= bs_complete, "Keep Groups Together nunca deberia tener MENOS Grupos completos que Best Space"
    assert len(best_space.placed) >= len(keep_groups.placed), (
        "Best Space puede (y en este dataset, deberia) cargar MAS piezas totales -acepta Grupos parciales, Keep Groups no"
    )
    assert any(r.status == "PARTIAL" for r in bs_reports), "Best Space deberia dejar al menos un Grupo parcial en este dataset"
    assert not any(r.status == "PARTIAL" for r in kg_reports), "Keep Groups Together no deberia dejar NINGUN Grupo parcial en este dataset"


# ==========================================================================
# 3. Otro Grupo completo puede sumarse si entra (seccion 7 del pedido).
# ==========================================================================
def test_another_complete_group_added_when_it_fits():
    # Capacidad generosa (todo entra) -los 3 Grupos deben terminar
    # completos, no solo los primeros que se intentan.
    container = build_custom_load_space("ABUNDANT", LoadSpaceType.CUSTOM, length=2000.0, width=2000.0, height=1250.0, max_weight=1_000_000.0)
    items = _abc_items()
    best, _ = run_optimization(items, container, OptimizationMode.KEEP_GROUPS)
    reports = group_completion_report(items, best.placed)
    assert all(r.status == "COMPLETE" for r in reports)
    assert len(best.placed) == len(items)


# ==========================================================================
# 4. Un Grupo NO seleccionado nunca se filtra a Pocket Reuse (seccion 12
# del pedido) -- verificado con Panels reales (Pocket Reuse es el motor de
# produccion para Panels).
# ==========================================================================
def _panel(code, group, n, w=600.0, h=1800.0, t=40.0, **kw):
    return [
        WindowItem(
            code=f"{code}{i}", description=f"{code}{i}", width=w, height=h, thickness=t,
            weight=15.0, quantity=1, item_type=ItemType.PANEL, stackable=False, group=group, **kw,
        )
        for i in range(n)
    ]


def test_non_selected_group_never_leaks_into_pocket_reuse():
    # Reusa el mismo escenario ya probado (2 Grupos completos ganan sobre
    # 3 parciales, ver test_two_complete_groups_beat_three_partial_groups)
    # -Panels reales via Pocket Reuse (el motor de produccion), no Boxes,
    # para probar especificamente que un Grupo NO seleccionado (B, en este
    # dataset) jamas aparece en `placed`, ni siquiera via un pocket residual.
    #
    # Recalibrado (pedido "FINAL INTEGRATION + LIVE VISUAL ACCEPTANCE",
    # seccion 1: KEEP_GROUPS+PANEL ahora rutea de verdad a section_
    # keep_groups_pocket_reuse_stage3 via run_optimization -antes de esta
    # tarea, este mismo dataset con B=40 unidades ya no fuerza la
    # exclusion total de ningun Grupo: Stage 3 es geometricamente MAS
    # capaz -Section-aware, con handoff y Level 2- que la verificacion
    # plana que usaba el camino KEEP_GROUPS anterior, asi que B=40
    # terminaba completo Y A quedaba PARCIAL (15/26), nunca 0. B=60
    # unidades SI sigue forzando la exclusion total de A (0/26,
    # NOT_LOADED) bajo Stage 3 -mismo invariante que este test siempre
    # protegio (ninguna pieza de un Grupo excluido debe aparecer en
    # placed), solo con un dataset recalibrado para el motor real.
    container = get_container("40ft_standard")
    outlier_a = WindowItem(code="A-OUT", description="A-OUT", width=4300.0, height=300.0, thickness=300.0, weight=10.0,
                            quantity=1, item_type=ItemType.PANEL, stackable=False, group="A")
    group_a = [outlier_a] + _panel("A", "A", 25, w=1000.0, h=2700.0, t=150.0)
    group_b = _panel("B", "B", 60, w=1200.0, h=2000.0, t=150.0)
    items = group_a + group_b

    best, _ = run_optimization(items, container, OptimizationMode.KEEP_GROUPS)
    reports = group_completion_report(items, best.placed)

    not_loaded = [r.group for r in reports if r.status == "NOT_LOADED"]
    assert not_loaded, "este dataset deberia forzar la exclusion de al menos un Grupo entero"
    for group in not_loaded:
        assert not any(p.group == group for p in best.placed), f"ninguna pieza del Grupo {group} (no seleccionado) debe aparecer en placed"
    assert validate_for_export(best, container) == []


# ==========================================================================
# 5. Grupos seleccionados pueden compartir pockets residuales DENTRO del
# mismo Grupo (seccion 12/16 del pedido: Pocket Reuse sigue funcionando
# normalmente para los Grupos que SI fueron seleccionados).
# ==========================================================================
def test_selected_group_still_benefits_from_pocket_reuse():
    container = get_container("40ft_standard")
    # D12-like: un outlier con unica orientacion valida (fuerza modulo
    # profundo) + companeros angostos + items chicos que solo caben
    # reusando el pocket que el outlier deja -TODOS en el MISMO Grupo.
    outlier = WindowItem(code="OUT", description="OUT", width=4300.0, height=300.0, thickness=300.0, weight=10.0,
                          quantity=1, item_type=ItemType.PANEL, stackable=False, group="G1")
    companions = [
        WindowItem(code=f"C{i}", description=f"C{i}", width=1000.0, height=2700.0, thickness=155.0, weight=10.0,
                   quantity=1, item_type=ItemType.PANEL, stackable=False, group="G1")
        for i in range(2)
    ]
    fits = [
        WindowItem(code=f"F{i}", description=f"F{i}", width=600.0, height=900.0, thickness=40.0, weight=10.0,
                   quantity=1, item_type=ItemType.PANEL, stackable=False, group="G1")
        for i in range(3)
    ]
    items = [outlier, *companions, *fits]

    best, _ = run_optimization(items, container, OptimizationMode.KEEP_GROUPS)
    reports = group_completion_report(items, best.placed)
    assert reports[0].status == "COMPLETE"
    assert len(best.placed) == len(items)
    assert validate_for_export(best, container) == []


# ==========================================================================
# 6. Best Space Utilization sigue libre de mezclar Grupos (seccion 9 del
# pedido -- ya cubierto arriba en test_partial_group_penalty_dominates_
# small_loaded_unit_gain, se repite explicito aca por claridad).
# ==========================================================================
def test_best_space_freely_mixes_groups():
    container = _abc_container()
    items = _abc_items()
    best, _ = run_optimization(items, container, OptimizationMode.BEST_SPACE)
    reports = group_completion_report(items, best.placed)
    statuses = {r.group: r.status for r in reports}
    assert set(statuses.values()) != {"NOT_LOADED"}
    assert any(s == "PARTIAL" for s in statuses.values()) or all(s == "COMPLETE" for s in statuses.values())


# ==========================================================================
# 7. Un movimiento manual entre Grupos sigue siendo geometricamente valido
# -Group NUNCA es un chequeo de geometria (seccion 13 del pedido).
# ==========================================================================
def test_manual_cross_group_move_remains_geometrically_valid():
    container = get_container("40ft_standard")
    piece_a = PlacedPiece(
        id="A-001", code="A", description="A", weight=10.0, stackable=False,
        x=0.0, y=0.0, z=0.0, dx=600.0, dy=600.0, dz=1800.0, orientation_label="FIXED",
        source_width=600.0, source_height=1800.0, source_thickness=600.0, item_type=ItemType.PANEL,
        group="GROUP-A",
    )
    piece_b = PlacedPiece(
        id="B-001", code="B", description="B", weight=10.0, stackable=False,
        x=1000.0, y=0.0, z=0.0, dx=600.0, dy=600.0, dz=1800.0, orientation_label="FIXED",
        source_width=600.0, source_height=1800.0, source_thickness=600.0, item_type=ItemType.PANEL,
        group="GROUP-B",
    )
    # Mover A a una posicion fisicamente libre, adyacente a donde esta B
    # (Grupos DISTINTOS) -debe validar OK: la geometria nunca consulta Group.
    ok, reason = validate_move(piece_a, 400.0, 0.0, 0.0, 600.0, 600.0, 1800.0, [piece_b], container)
    assert ok, f"un movimiento geometricamente valido no deberia rechazarse por cruzar Grupos: {reason}"


# ==========================================================================
# 8. La regresion real D12/Pocket Reuse sigue intacta bajo Best Space
# Utilization (el modo que NO cambia con esta tarea), y Keep Groups
# Together sobre el MISMO plan real produce un resultado valido y
# consistente (sin crash, sin errores de validacion), aunque el conteo de
# piezas cargadas ya no sea el objetivo -seccion 11 del pedido: "The
# target is not necessarily 67/67. The target is a better COMPLETE-GROUP
# outcome."
# ==========================================================================
def test_real_plan_best_space_regression_still_intact():
    container = get_container("40ft_high_cube")
    items = _real_67_line_items()
    best, _ = run_optimization(items, container, OptimizationMode.BEST_SPACE)
    assert len(best.placed) == 67
    assert len(best.unloaded) == 0
    assert validate_for_export(best, container) == []


def test_real_plan_keep_groups_together_maximizes_completed_payload():
    # Seccion 32/14/21 del pedido de correccion "COMPLETED GROUP PAYLOAD":
    # el resultado anterior (3 Grupos chicos completos, 14/67, GROUP-1 de
    # 53 unidades totalmente excluido) NO es aceptable solo porque 3 > 1
    # -este es el caso de aceptacion real explicito. GROUP-1 (53 unidades,
    # el mismo Grupo real "Raquel Zaki" que origino el caso D12) debe
    # completar.
    container = get_container("40ft_high_cube")
    items = _real_67_line_items()
    best, _ = run_optimization(items, container, OptimizationMode.KEEP_GROUPS)

    assert validate_for_export(best, container) == []
    reports = {r.group: r for r in group_completion_report(items, best.placed)}
    assert reports["GROUP-1"].status == "COMPLETE" and reports["GROUP-1"].loaded_units == 53, (
        "el Grupo de mayor payload (53 unidades) debe completar, no quedar excluido a favor de Grupos mas chicos"
    )
    assert len(best.placed) > 14, "el resultado no debe volver a colapsar al viejo comportamiento de 14/67"
    reports_list = list(reports.values())
    assert not any(r.status == "PARTIAL" for r in reports_list) or sum(r.status == "PARTIAL" for r in reports_list) <= 1, (
        "como maximo un Grupo parcial de cola (seccion 16 del pedido)"
    )
    accepted_groups = {r.group for r in reports_list if r.status in ("COMPLETE", "PARTIAL")}
    assert all(p.group in accepted_groups for p in best.placed), (
        "solo deberian aparecer piezas de Grupos aceptados (completos o el unico parcial de cola)"
    )


# ==========================================================================
# Seccion 14/21 del pedido: un Grupo grande completo (53 unidades) debe
# ganarle a varios Grupos chicos completos (14 unidades) -el payload
# completado manda, nunca la cantidad de nombres de Grupo. Version
# sintetica y acotada del caso real de arriba, para un regression rapido
# que no dependa de la geometria completa del plan de 67 lineas.
# ==========================================================================
def test_large_complete_group_beats_several_tiny_complete_groups():
    container = build_custom_load_space("PAYLOAD-TEST", LoadSpaceType.CUSTOM, length=2000.0, width=2000.0, height=750.0, max_weight=1_000_000.0)
    big = _box("BIG", "BIG", 53, w=250.0, h=250.0, t=200.0)  # 53 unidades, un solo Grupo grande
    tiny1 = _box("T1", "TINY1", 5, w=250.0, h=250.0, t=200.0)
    tiny2 = _box("T2", "TINY2", 5, w=250.0, h=250.0, t=200.0)
    tiny3 = _box("T3", "TINY3", 4, w=250.0, h=250.0, t=200.0)  # 3 Grupos chicos, 14 unidades totales
    items = big + tiny1 + tiny2 + tiny3

    best, _ = run_optimization(items, container, OptimizationMode.KEEP_GROUPS)
    reports = {r.group: r for r in group_completion_report(items, best.placed)}

    assert reports["BIG"].status == "COMPLETE" and reports["BIG"].loaded_units == 53
    assert len(best.placed) >= 53, "el Grupo grande completo debe ganar, nunca perderse a favor de 3 Grupos chicos por cantidad de nombres"
