"""Stage B: regression coverage para el ruteo de motores en
core/optimize.py -BOX -> pack_boxes_v2, PANEL -> pack_panels_module salvo
zonas reservadas activas (fallback al motor legacy), PALLET/CUSTOM sin
cambios. Unico lugar que decide el motor (seccion 1 del pedido); estos
tests verifican que run_optimization() -el camino REAL de produccion, el
mismo que llama api/routes.py- rutea correctamente y preserva toda la
semantica existente (Optimization Modes, Load Priority, BACK_RIGHT_FLOOR,
validacion, secuencia)."""

from app.core.final_validation import validate_for_export
from app.core.optimize import (
    ENGINE_BOX_EMS_V2,
    ENGINE_BOX_LEGACY_CLEARANCE_FALLBACK,
    ENGINE_CUSTOM_LEGACY,
    ENGINE_PALLET_LEGACY,
    ENGINE_PANEL_LEGACY_CLEARANCE_FALLBACK,
    ENGINE_PANEL_LEGACY_TILT_FALLBACK,
    ENGINE_PANEL_MODULE_POCKET_V2,
    _select_pack_fn,
    run_optimization,
)
from app.core.reserved_zones import central_aisle_zone
from app.core.sequence import compute_load_steps
from app.models.containers import get_container
from app.models.schemas import ItemType, OptimizationMode, WindowItem

CONTAINER = get_container("40ft_standard")


def _box(code, w=600, h=600, t=600, **kw):
    return WindowItem(
        code=code, description=f"Box {code}", width=w, height=h, thickness=t,
        weight=kw.pop("weight", 20.0), quantity=1, item_type=ItemType.BOX, stackable=True, **kw,
    )


def _panel(code, w=500, h=1800, t=40, **kw):
    return WindowItem(
        code=code, description=f"Panel {code}", width=w, height=h, thickness=t,
        weight=kw.pop("weight", 15.0), quantity=1, item_type=ItemType.PANEL, stackable=False, **kw,
    )


def _pallet(code, w=1000, h=1200, t=1000, **kw):
    return WindowItem(
        code=code, description=f"Pallet {code}", width=w, height=h, thickness=t,
        weight=kw.pop("weight", 300.0), quantity=1, item_type=ItemType.PALLET, stackable=True, **kw,
    )


def _custom(code, w=500, h=500, t=500, **kw):
    return WindowItem(
        code=code, description=f"Custom {code}", width=w, height=h, thickness=t,
        weight=kw.pop("weight", 50.0), quantity=1, item_type=ItemType.CUSTOM, stackable=True, **kw,
    )


# ==========================================================================
# Seleccion de motor por Load Type (seccion 1/13 del pedido)
# ==========================================================================


def test_box_routes_to_ems_v2():
    fn, engine = _select_pack_fn([_box("A")], None)
    assert engine == ENGINE_BOX_EMS_V2


def test_panel_without_reserved_zones_routes_to_module_solver():
    fn, engine = _select_pack_fn([_panel("A")], None)
    assert engine == ENGINE_PANEL_MODULE_POCKET_V2


def test_panel_with_reserved_zones_still_uses_module_solver():
    # Ya NO cae a legacy (RESUELTO -el Module Solver talla el ancho por
    # intervalos alrededor de la zona, ver panel_module_solver.py:
    # _usable_lateral_intervals). Antes de este fix, cualquier zona
    # reservada activa forzaba el fallback.
    aisle = central_aisle_zone(CONTAINER, 800.0)
    fn, engine = _select_pack_fn([_panel("A")], [aisle])
    assert engine == ENGINE_PANEL_MODULE_POCKET_V2


def test_panel_with_empty_reserved_zones_list_still_uses_module_solver():
    # Lista vacia (sin zonas activas) NUNCA debe disparar el fallback -solo
    # zonas realmente presentes.
    fn, engine = _select_pack_fn([_panel("A")], [])
    assert engine == ENGINE_PANEL_MODULE_POCKET_V2


def test_panel_with_active_tilt_falls_back_to_legacy():
    # panel_module_solver.py nunca genera orientaciones con Tilt (bug real
    # encontrado en el backend suite completo -8 tests de Fase 5C fallaban
    # bajo el ruteo nuevo). Cualquier item con Tilt realmente activo debe
    # caer al motor existente, igual que las zonas reservadas.
    tilted = _panel("A", allow_tilt=True, max_tilt_angle=20.0)
    fn, engine = _select_pack_fn([tilted], None)
    assert engine == ENGINE_PANEL_LEGACY_TILT_FALLBACK


def test_panel_with_allow_tilt_but_zero_angle_still_uses_module_solver():
    # allow_tilt=True con max_tilt_angle=0 no es Tilt REALMENTE activo
    # (mismo criterio que core/packer_v2.py:_orientation_passes_for) -no
    # debe disparar el fallback innecesariamente.
    not_really_tilted = _panel("A", allow_tilt=True, max_tilt_angle=0.0)
    fn, engine = _select_pack_fn([not_really_tilted], None)
    assert engine == ENGINE_PANEL_MODULE_POCKET_V2


def test_box_with_active_clearance_falls_back_to_legacy():
    # Incidente real post-Stage-B (reportado por el usuario contra datos
    # reales): el EMS de Boxes verifica clearance reactivamente pero
    # nunca talla el espacio libre por adelantado -deja franjas enormes
    # sin usar (0/392 piezas tocaban una pared en el caso real). Hasta
    # que eso se resuelva, clearance>0 cae al motor existente.
    fn, engine = _select_pack_fn([_box("A")], None, clearance=100.0)
    assert engine == ENGINE_BOX_LEGACY_CLEARANCE_FALLBACK


def test_box_with_zero_clearance_still_uses_ems_v2():
    fn, engine = _select_pack_fn([_box("A")], None, clearance=0.0)
    assert engine == ENGINE_BOX_EMS_V2


def test_panel_with_active_clearance_falls_back_to_legacy():
    # Mismo incidente, panels: el Module Solver ni siquiera intenta
    # respetar clearance entre paneles -confirmado que rompe la
    # validacion final con decenas de violaciones.
    fn, engine = _select_pack_fn([_panel("A")], None, clearance=100.0)
    assert engine == ENGINE_PANEL_LEGACY_CLEARANCE_FALLBACK


def test_panel_with_zero_clearance_still_uses_module_solver():
    fn, engine = _select_pack_fn([_panel("A")], None, clearance=0.0)
    assert engine == ENGINE_PANEL_MODULE_POCKET_V2


def test_box_clearance_fallback_via_real_optimize_path_passes_validation():
    items = [_box(f"B{i}") for i in range(20)]
    best, _alts = run_optimization(items, CONTAINER, OptimizationMode.BEST_SPACE, clearance=100.0)
    assert len(best.placed) == 20
    assert validate_for_export(best, CONTAINER, clearance=100.0) == []


def test_panel_clearance_fallback_via_real_optimize_path_passes_validation():
    items = [_panel(f"P{i}") for i in range(20)]
    best, _alts = run_optimization(items, CONTAINER, OptimizationMode.BEST_SPACE, clearance=100.0)
    assert len(best.placed) == 20
    assert validate_for_export(best, CONTAINER, clearance=100.0) == []


def test_pallet_routes_to_legacy():
    fn, engine = _select_pack_fn([_pallet("A")], None)
    assert engine == ENGINE_PALLET_LEGACY


def test_custom_routes_to_legacy():
    fn, engine = _select_pack_fn([_custom("A")], None)
    assert engine == ENGINE_CUSTOM_LEGACY


def test_empty_items_does_not_crash_routing():
    fn, engine = _select_pack_fn([], None)
    assert engine == ENGINE_CUSTOM_LEGACY


# ==========================================================================
# Fallback preserva capacidad -seccion 13: "Do not silently load only
# 10/42 just because the new solver is conservative"
# ==========================================================================


def test_reserved_zone_capacity_preserved_via_real_optimize_path():
    # El Module Solver (ya no el fallback legacy) debe cargar TODO, no
    # solo "razonablemente bien" -este es exactamente el caso que perdia
    # capacidad severa (42 -> 10) en Stage A.6.1, resuelto por
    # _usable_lateral_intervals.
    items = [_panel(f"P{i}", w=400 + (i % 5) * 60, h=1200 + (i % 6) * 150) for i in range(40)]
    aisle = central_aisle_zone(CONTAINER, 800.0)
    best, _alts = run_optimization(items, CONTAINER, OptimizationMode.BEST_SPACE, reserved_zones=[aisle])
    assert len(best.placed) == len(items)
    issues = validate_for_export(best, CONTAINER, reserved_zones=[aisle])
    assert issues == []


# ==========================================================================
# BACK_RIGHT_FLOOR a traves del camino REAL de produccion (seccion 6)
# ==========================================================================


def _back_right_floor_touched(placed, container):
    return any(
        abs((p.x + p.dx) - container.length) <= 1.0 and abs((p.y + p.dy) - container.width) <= 1.0 and abs(p.z) <= 1.0
        for p in placed
    )


def test_box_back_right_floor_via_production_path():
    items = [_box(f"B{i}") for i in range(10)]
    best, _ = run_optimization(items, CONTAINER, OptimizationMode.BEST_SPACE)
    assert _back_right_floor_touched(best.placed, CONTAINER)


def test_panel_back_right_floor_via_production_path():
    items = [_panel(f"P{i}", h=1800 + i * 50) for i in range(6)]
    best, _ = run_optimization(items, CONTAINER, OptimizationMode.BEST_SPACE)
    assert _back_right_floor_touched(best.placed, CONTAINER)


def test_pallet_back_right_floor_unchanged():
    items = [_pallet(f"PL{i}") for i in range(5)]
    best, _ = run_optimization(items, CONTAINER, OptimizationMode.BEST_SPACE)
    assert _back_right_floor_touched(best.placed, CONTAINER)


def test_custom_back_right_floor_unchanged():
    items = [_custom(f"C{i}") for i in range(5)]
    best, _ = run_optimization(items, CONTAINER, OptimizationMode.BEST_SPACE)
    assert _back_right_floor_touched(best.placed, CONTAINER)


# ==========================================================================
# Optimization Modes preservados a traves del camino real (seccion 4)
# ==========================================================================


def test_box_keep_groups_together_via_production_path():
    items = [
        _box("A1", group="A"), _box("B1", group="B"), _box("A2", group="A"),
        _box("B2", group="B"), _box("A3", group="A"),
    ]
    best, _ = run_optimization(items, CONTAINER, OptimizationMode.KEEP_GROUPS)
    by_group = {}
    for p in best.placed:
        by_group.setdefault(p.group, []).append(p.x)
    for group, xs in by_group.items():
        assert max(xs) - min(xs) <= 700, f"grupo {group} disperso: {xs}"


def test_panel_keep_groups_together_via_production_path():
    # Recalibrado (pedido "FINAL INTEGRATION + LIVE VISUAL ACCEPTANCE",
    # seccion 1): KEEP_GROUPS+PANEL sin Tilt/Clearance ahora rutea de
    # verdad a section_keep_groups_pocket_reuse_stage3 via run_optimization.
    # El invariante "un modulo nunca mezcla Grupos" era el de Stage 1
    # (panel_module_solver.py:_cluster_key) -Stage 2.1/2.2/3 agregan
    # DELIBERADAMENTE "In-Section Group Handoff" (ver test_section_
    # frontier.py FRONTIER-1/3, test_trailing_partial.py TP-4): una vez
    # que un Grupo completa, el SIGUIENTE Grupo ya seleccionado puede
    # entrar al pocket lateral de esa MISMA Section/modulo -exactamente lo
    # que esta fixture (A completo con espacio lateral de sobra, B chico)
    # ahora ejercita en produccion. El invariante real que Stage 3
    # protege -verificado extensamente- ya no es "nunca compartir modulo"
    # sino cronologia de Grupo monotonica (nunca A -> B -> A).
    items = [
        _panel("A1", w=650, group="A"), _panel("B1", w=650, group="B"), _panel("A2", w=600, group="A"),
        _panel("B2", w=600, group="B"),
    ]
    best, _ = run_optimization(items, CONTAINER, OptimizationMode.KEEP_GROUPS)
    assert len(best.placed) == 4
    assert validate_for_export(best, CONTAINER) == []

    # Cronologia monotonica: ordenando por x descendente (fondo -> puerta,
    # mismo orden que Stage 3 procesa), ningun Grupo debe reaparecer
    # despues de haber dejado de ser el ultimo activo (mismo criterio que
    # compute_group_contiguity_metrics:group_reentry_count, aplicado aca
    # directo sobre `placed` porque run_optimization no expone `sections`).
    ordered = sorted(best.placed, key=lambda p: (-p.x, p.id))
    seen_and_left: set[str] = set()
    prev = None
    for p in ordered:
        if p.group != prev:
            assert p.group not in seen_and_left, f"Grupo {p.group} reaparecio (reentry) -secuencia: {[q.group for q in ordered]}"
            if prev is not None:
                seen_and_left.add(prev)
        prev = p.group


def test_box_prioritize_delivery_sequence_via_production_path():
    items = [_box("S3", delivery_sequence=3), _box("S1", delivery_sequence=1), _box("S2", delivery_sequence=2)]
    best, _ = run_optimization(items, CONTAINER, OptimizationMode.PRIORITIZE_DELIVERY)
    by_seq = {p.delivery_sequence: p.x for p in best.placed}
    assert by_seq[1] <= by_seq[2] <= by_seq[3]


# ==========================================================================
# Load Priority (seccion 5): decide ADMISION bajo restriccion de capacidad,
# nunca layout espacial cuando todo entra.
# ==========================================================================


def test_box_load_priority_admits_high_priority_under_capacity_pressure():
    filler = [_box(f"F{i}", w=CONTAINER.width, h=CONTAINER.height, t=200, priority=5) for i in range(int(CONTAINER.length // 200))]
    vip = _box("VIP", w=CONTAINER.width, h=CONTAINER.height, t=200, priority=1)
    best, _ = run_optimization(filler + [vip], CONTAINER, OptimizationMode.BEST_SPACE)
    assert any(p.code == "VIP" for p in best.placed)


# ==========================================================================
# Secuencia recomputada normalmente (seccion 8) -nunca el orden interno del
# solver experimental como autoritativo.
# ==========================================================================


def test_box_sequence_recomputes_normally():
    items = [_box(f"B{i}") for i in range(15)]
    best, _ = run_optimization(items, CONTAINER, OptimizationMode.BEST_SPACE)
    steps = compute_load_steps(best.placed, CONTAINER)
    all_ids = {piece_id for step in steps for piece_id in step}
    assert all_ids == {p.id for p in best.placed}


def test_panel_sequence_recomputes_normally():
    items = [_panel(f"P{i}", h=1500 + (i % 4) * 200) for i in range(20)]
    best, _ = run_optimization(items, CONTAINER, OptimizationMode.BEST_SPACE)
    steps = compute_load_steps(best.placed, CONTAINER)
    all_ids = {piece_id for step in steps for piece_id in step}
    assert all_ids == {p.id for p in best.placed}
