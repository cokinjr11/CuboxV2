"""Regression tests para el prototipo experimental Packing Engine V2
(seccion 22 del pedido de la tarea Stage A). Cubre exactamente la lista
pedida: ancla BACK_RIGHT del primer item, restricciones de orientacion,
contencion, colision, soporte, peso de stack, zonas reservadas, orden
bilateral de Panels, Groups, Systems, Delivery Sequence, Load Priority,
determinismo, geometria canonica (compatible con guardar/reabrir via
PlacedPiece/PackingResult), y generacion de secuencia.

`core/packer_v2.py` sigue siendo un camino EXPERIMENTAL (no se usa en
produccion, ver su docstring) -estos tests solo garantizan que el
prototipo no rompe ninguna regla de negocio existente mientras se evalua
si su arquitectura es mejor (Stage A), nunca reemplazan los tests de
core/packer.py."""

from app.core.packer_v2 import pack_boxes_v2, pack_container_v2, pack_panels_v2
from app.core.reserved_zones import central_aisle_zone
from app.core.sequence import compute_load_steps
from app.models.containers import get_container
from app.models.schemas import ItemType, OptimizationMode, PackingResult, WindowItem

CONTAINER = get_container("40ft_standard")


def _box(code, w=600, h=600, t=600, **kw):
    return WindowItem(
        code=code, description=f"Box {code}", width=w, height=h, thickness=t,
        weight=kw.pop("weight", 20.0), quantity=kw.pop("quantity", 1),
        item_type=ItemType.BOX, stackable=kw.pop("stackable", True), **kw,
    )


def _panel(code, w=400, h=2200, t=40, **kw):
    return WindowItem(
        code=code, description=f"Panel {code}", width=w, height=h, thickness=t,
        weight=kw.pop("weight", 15.0), quantity=1, item_type=ItemType.PANEL,
        stackable=False, **kw,
    )


# ==========================================================================
# Ancla BACK_RIGHT_FLOOR
# ==========================================================================


def test_first_box_anchors_back_right_floor():
    result, _ = pack_boxes_v2([_box("A")], CONTAINER)
    assert len(result.placed) == 1
    p = result.placed[0]
    assert abs((p.x + p.dx) - CONTAINER.length) < 1e-6
    assert abs((p.y + p.dy) - CONTAINER.width) < 1e-6
    assert abs(p.z) < 1e-6


def test_first_panel_anchors_back_right_floor():
    result, _ = pack_panels_v2([_panel("P")], CONTAINER)
    assert len(result.placed) == 1
    p = result.placed[0]
    assert abs((p.x + p.dx) - CONTAINER.length) < 1e-6
    assert abs((p.y + p.dy) - CONTAINER.width) < 1e-6
    assert abs(p.z) < 1e-6


# ==========================================================================
# Orientacion / contencion / colision / soporte / peso de stack
# ==========================================================================


def test_oversized_item_is_unloaded_not_forced():
    huge = _box("HUGE", w=CONTAINER.length + 1000, h=600, t=600)
    result, _ = pack_boxes_v2([huge], CONTAINER)
    assert len(result.placed) == 0
    assert len(result.unloaded) == 1


def test_all_placed_pieces_within_container_bounds():
    items = [_box(f"B{i}", w=500, h=500, t=500) for i in range(60)]
    result, _ = pack_boxes_v2(items, CONTAINER)
    for p in result.placed:
        assert p.x >= -1e-6 and p.x + p.dx <= CONTAINER.length + 1e-6
        assert p.y >= -1e-6 and p.y + p.dy <= CONTAINER.width + 1e-6
        assert p.z >= -1e-6 and p.z + p.dz <= CONTAINER.height + 1e-6


def test_no_pairwise_overlap_between_placed_boxes():
    items = [_box(f"B{i}", w=500, h=500, t=500) for i in range(60)]
    result, _ = pack_boxes_v2(items, CONTAINER)
    placed = result.placed
    for i, a in enumerate(placed):
        for b in placed[i + 1 :]:
            overlap_x = a.x < b.x + b.dx - 1e-6 and b.x < a.x + a.dx - 1e-6
            overlap_y = a.y < b.y + b.dy - 1e-6 and b.y < a.y + a.dy - 1e-6
            overlap_z = a.z < b.z + b.dz - 1e-6 and b.z < a.z + a.dz - 1e-6
            assert not (overlap_x and overlap_y and overlap_z), f"{a.code} overlaps {b.code}"


def test_non_stackable_item_never_gets_something_on_top():
    base = _box("BASE", w=600, h=600, t=600, stackable=False)
    topper = _box("TOP", w=600, h=600, t=600)
    result, _ = pack_boxes_v2([base, topper], CONTAINER)
    base_p = next(p for p in result.placed if p.code == "BASE")
    for p in result.placed:
        if p.code == "BASE":
            continue
        same_xy = abs(p.x - base_p.x) < 1e-6 and abs(p.y - base_p.y) < 1e-6
        assert not (same_xy and p.z > base_p.z), "algo quedo apilado sobre un item non-stackable"


def test_stack_weight_limit_respected():
    heavy_base = _box("HB", w=600, h=600, t=600, weight=5.0, max_stack_weight=1.0)
    heavy_top = _box("HT", w=600, h=600, t=600, weight=50.0)
    result, _ = pack_boxes_v2([heavy_base, heavy_top], CONTAINER)
    base_p = next((p for p in result.placed if p.code == "HB"), None)
    if base_p is None:
        return
    for p in result.placed:
        if p.code == "HB":
            continue
        same_xy = abs(p.x - base_p.x) < 1e-6 and abs(p.y - base_p.y) < 1e-6
        assert not (same_xy and p.z > base_p.z), "stack weight limit violado"


# ==========================================================================
# Zonas reservadas (pasillo central)
# ==========================================================================


def test_reserved_central_aisle_stays_empty():
    aisle = central_aisle_zone(CONTAINER, aisle_width_mm=800.0)
    items = [_box(f"B{i}", w=500, h=500, t=500) for i in range(80)]
    result, _ = pack_boxes_v2(items, CONTAINER, reserved_zones=[aisle])
    for p in result.placed:
        overlaps_x = p.x < aisle.x + aisle.length - 1e-6 and aisle.x < p.x + p.dx - 1e-6
        overlaps_y = p.y < aisle.y + aisle.width - 1e-6 and aisle.y < p.y + p.dy - 1e-6
        assert not (overlaps_x and overlaps_y), f"{p.code} invade el pasillo reservado"


# ==========================================================================
# Panels & Fragile: orden bilateral
# ==========================================================================


def test_panels_bilateral_height_order_no_violations():
    # 2350mm, no 2400mm: la altura interior del contenedor es 2393mm -un
    # panel de 2400mm no puede pararse (Stage A.5, Parte 5 del pedido,
    # verificado en aislamiento contra core/packer.py: comportamiento
    # fisico correcto compartido por ambos motores, no un bug).
    heights = [2350, 2300, 2200, 2000, 1900, 1600, 1200, 1000, 800, 600]
    items = [_panel(f"P{i}", h=h) for i, h in enumerate(heights)]
    result, _ = pack_panels_v2(items, CONTAINER)
    assert len(result.placed) == 10
    half_width = CONTAINER.width / 2
    for side_filter, reverse in ((lambda p: p.y < half_width, False), (lambda p: p.y >= half_width, True)):
        side = sorted([p for p in result.placed if side_filter(p)], key=lambda p: p.y, reverse=reverse)
        heights_seen = [p.dz for p in side]
        assert heights_seen == sorted(heights_seen, reverse=True), "orden alto->bajo hacia el centro violado"


def test_panels_distribute_across_both_walls():
    heights = [2350, 2300, 2200, 2000, 1900, 1600, 1200, 1000, 800, 600]
    items = [_panel(f"P{i}", h=h) for i, h in enumerate(heights)]
    result, _ = pack_panels_v2(items, CONTAINER)
    half_width = CONTAINER.width / 2
    left = [p for p in result.placed if p.y < half_width]
    right = [p for p in result.placed if p.y >= half_width]
    assert left and right, "llenado bilateral no uso ambos lados"


# ==========================================================================
# Groups / Systems / Delivery Sequence / Load Priority (Optimization Modes,
# seccion 21 del pedido -- el `score_fn` es especifico de V2 pero el
# CLUSTERING en si (_cluster_key) y el orden base (build_sort_key) se
# reusan tal cual de core/strategies.py, igual que en core/packer.py).
# ==========================================================================


def test_keep_groups_together_clusters_by_group():
    items = [
        _box("A1", group="A"), _box("B1", group="B"), _box("A2", group="A"),
        _box("B2", group="B"), _box("A3", group="A"),
    ]
    result, _ = pack_boxes_v2(items, CONTAINER, optimization_mode=OptimizationMode.KEEP_GROUPS)
    by_group = {}
    for p in result.placed:
        by_group.setdefault(p.group, []).append(p.x)
    for group, xs in by_group.items():
        assert max(xs) - min(xs) <= 700, f"grupo {group} quedo disperso en X: {xs}"


def test_prioritize_delivery_sequence_orders_by_sequence():
    items = [
        _box("S3", delivery_sequence=3), _box("S1", delivery_sequence=1), _box("S2", delivery_sequence=2),
    ]
    result, _ = pack_boxes_v2(items, CONTAINER, optimization_mode=OptimizationMode.PRIORITIZE_DELIVERY)
    by_seq = {p.delivery_sequence: p.x for p in result.placed}
    # Menor delivery_sequence (se descarga primero) debe quedar mas cerca de
    # la puerta (x menor) para que salga primero -mismo criterio operativo
    # que core/packer.py bajo este Optimization Mode.
    assert by_seq[1] <= by_seq[2] <= by_seq[3]


def test_load_priority_admits_high_priority_first_under_capacity_pressure():
    # Contenedor casi lleno con relleno de baja prioridad, luego un item de
    # alta prioridad que ya no entra a menos que se prefiera sobre algo de
    # baja prioridad -Load Priority solo afecta ADMISION, nunca posicion
    # (ver WindowItem.priority docstring), reusado sin cambios via
    # build_sort_key/scoring.py.
    filler = [_box(f"F{i}", w=CONTAINER.width, h=CONTAINER.height, t=200, priority=5) for i in range(int(CONTAINER.length // 200))]
    vip = _box("VIP", w=CONTAINER.width, h=CONTAINER.height, t=200, priority=1)
    result, _ = pack_boxes_v2(filler + [vip], CONTAINER, optimization_mode=OptimizationMode.BEST_SPACE)
    assert any(p.code == "VIP" for p in result.placed), "item de alta prioridad no fue admitido"


# ==========================================================================
# Determinismo
# ==========================================================================


def test_v2_packing_is_deterministic():
    items = [_box(f"B{i}", w=500 + (i % 4) * 40, h=500, t=500) for i in range(80)]
    items2 = [_box(f"B{i}", w=500 + (i % 4) * 40, h=500, t=500) for i in range(80)]
    r1, _ = pack_boxes_v2(items, CONTAINER)
    r2, _ = pack_boxes_v2(items2, CONTAINER)
    coords1 = sorted((p.code, p.x, p.y, p.z) for p in r1.placed)
    coords2 = sorted((p.code, p.x, p.y, p.z) for p in r2.placed)
    assert coords1 == coords2, "el prototipo V2 no es determinista"


# ==========================================================================
# Geometria canonica (compatible con guardar/reabrir) y generacion de
# secuencia -V2 no esta cableado a persistence.py todavia (STAGE A), pero
# su salida (PlacedPiece/PackingResult) debe ser la MISMA estructura que
# usan esos caminos, sin campos faltantes ni geometria invalida para ellos.
# ==========================================================================


def test_v2_result_round_trips_through_packing_result_schema():
    items = [_box(f"B{i}") for i in range(10)]
    result, _ = pack_boxes_v2(items, CONTAINER)
    dumped = result.model_dump()
    reloaded = PackingResult.model_validate(dumped)
    assert len(reloaded.placed) == len(result.placed)


def test_v2_output_feeds_sequence_engine_without_error():
    items = [_box(f"B{i}") for i in range(15)]
    result, _ = pack_boxes_v2(items, CONTAINER)
    steps = compute_load_steps(result.placed, CONTAINER)
    all_ids = {piece_id for step in steps for piece_id in step}
    assert all_ids == {p.id for p in result.placed}


def test_pack_container_v2_dispatches_by_item_type():
    box_result, _ = pack_container_v2([_box("B1")], CONTAINER)
    assert box_result.placed[0].item_type == ItemType.BOX
    panel_result, _ = pack_container_v2([_panel("P1")], CONTAINER)
    assert panel_result.placed[0].item_type == ItemType.PANEL
