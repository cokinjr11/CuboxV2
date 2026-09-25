"""CUBOX 2.0 -- Packing Engine Quality Pass (Loose Boxes + Panels &
Fragile). Compactness, Alignment, Gap Reduction.

No redisena persistencia, reportes, el motor de secuencia, Delivery
Sequence, Group, System, Load Priority, validacion, guias PDF ni UI -ver
docstring de core/packer.py para el detalle exacto de que cambio y por
que.

Root causes auditados (ver comentarios en core/packer.py):

1. Loose Boxes usaban first-fit puro sin ranking de calidad entre
   candidatos igualmente profundos -_select_first_fit_candidate sigue
   siendo first-fit (verificado, benchmarking real: pagar el costo de
   evaluar TODOS los candidatos con un costo de compacidad no cambiaba el
   resultado, ver core/packer.py:_box_compactness_cost), pero AHORA existe
   _find_gap_fill_substitute (compartido con Panels) para no abandonar un
   hueco real solo porque el item de turno no entra ahi.
2. Panels & Fragile ya tenia ranking de calidad (heuristica bilateral) pero
   el mismo problema de "orden de items crea huecos" -Part 15/16 del
   pedido: modulo se cerraba prematuramente si el item de turno no entraba,
   sin buscar un item pendiente mas chico que si entrara."""

from app.core.geometry import Box
from app.core.packer import (
    _box_compactness_cost,
    _Candidate,
    _global_shallowest_tier,
    _global_shallowest_tier_slow,
    _sliver_penalty,
    pack_container,
)
from app.core.packing_quality import (
    average_lateral_gap,
    box_quality_report,
    narrow_sliver_count,
    panel_height_order_violations,
    panel_module_count,
    panel_quality_report,
    panel_wall_occupancy,
)
from app.models.containers import get_container
from app.models.schemas import ContainerSpec, ItemType, WindowItem

CONTAINER = get_container("40ft_standard")


def _box(code: str, w: float, h: float, t: float, **overrides) -> WindowItem:
    defaults = dict(
        code=code, description="Box", width=w, height=h, thickness=t, weight=20, quantity=1,
        item_type=ItemType.BOX, stackable=True,
    )
    defaults.update(overrides)
    return WindowItem(**defaults)


def _panel(code: str, height: float, thickness: float = 40, **overrides) -> WindowItem:
    defaults = dict(
        code=code, description=f"Panel {height}", width=400, height=height, thickness=thickness, weight=15,
        quantity=1, item_type=ItemType.PANEL, stackable=False,
    )
    defaults.update(overrides)
    return WindowItem(**defaults)


# ==========================================================================
# Regression datasets A-E (seccion 20 del pedido)
# ==========================================================================


def _dataset_a_mixed_boxes(n_repeats: int = 20) -> list[WindowItem]:
    specs = [(600, 600, 600), (500, 500, 500), (700, 400, 400), (450, 450, 450), (800, 500, 500)]
    return [_box(f"MX{i}", w, h, t) for i, (w, h, t) in enumerate(specs * n_repeats)]


def _dataset_b_same_size_boxes(n: int = 120) -> list[WindowItem]:
    return [_box(f"SS{i}", 600, 600, 600) for i in range(n)]


def _dataset_c_tall_short_panels(n_repeats: int = 3) -> list[WindowItem]:
    heights = [2400, 2300, 2200, 2000, 1900, 1600, 1200, 1000, 800, 600]
    return [_panel(f"PC{i}", h) for i, h in enumerate(heights * n_repeats)]


def _dataset_e_narrow_module() -> tuple[list[WindowItem], ContainerSpec]:
    """Modulo angosto a proposito (seccion 20E del pedido -'greedy order
    creates holes but alternate remaining-item selection fills them'):
    SEED(40mm) toca la pared derecha primero, dejando 65mm libres; BIG
    (70mm, siguiente en orden por volumen) NO entra en esos 65mm; SMALL
    (40mm, procesado DESPUES de BIG) si entra -el motor debe encontrar
    SMALL y usarlo, sin dejar BIG sin cargar."""
    container = ContainerSpec(id="narrow", name="Narrow", length=3000, width=105, height=3200, max_weight=100000)
    items = [
        _panel("SEED", 3000, thickness=40),
        _panel("BIG", 1500, thickness=70),
        _panel("SMALL", 1000, thickness=40),
    ]
    return items, container


# ==========================================================================
# Unit tests -- helpers puros
# ==========================================================================


def test_sliver_penalty_zero_at_flush_and_at_or_above_threshold_is_small():
    assert _sliver_penalty(0.0) == 0.0
    assert _sliver_penalty(5.0) > 0.9  # residuo positivo MUY chico -franja muerta, casi peor caso
    assert _sliver_penalty(149.9) < 0.01  # justo bajo el umbral -ya casi util, penalidad casi nula
    assert _sliver_penalty(150.0) < 0.2  # ya en zona "util", penalidad chica
    assert _sliver_penalty(50.0) > _sliver_penalty(150.0)  # 50mm (franja muerta) siempre peor que un hueco ya util


def test_global_shallowest_tier_fast_path_matches_slow_reference():
    candidates = [
        _Candidate(100.0, 0.0, 0.0, None),
        _Candidate(0.0, 50.0, 0.0, None),
        _Candidate(0.0, 0.0, 200.0, None),
    ]
    candidates.sort(key=lambda c: (c.x, c.z, c.y))
    assert _global_shallowest_tier(candidates) == _global_shallowest_tier_slow(candidates)


def test_global_shallowest_tier_empty_returns_none():
    assert _global_shallowest_tier([]) is None


def test_box_compactness_cost_penalizes_dead_sliver_over_flush_or_open():
    """_box_compactness_cost esta implementada segun las secciones 5-8 del
    pedido (no se borro pese a no estar conectada al loop principal, ver
    docstring de pack_container/_select_first_fit_candidate) -esta prueba
    verifica directamente que su componente de gap SI distingue una franja
    muerta de una posicion flush o de un hueco grande, para que quede
    disponible y correcta si en el futuro un dataset real muestra beneficio
    medible al usarla como criterio primario."""
    container = CONTAINER
    # Vecino a la izquierda ocupando [0,600] al mismo nivel/x-range.
    neighbor = Box(id="n", x=0.0, y=0.0, z=0.0, dx=600.0, dy=600.0, dz=600.0)

    flush = Box(id="a", x=0.0, y=600.0, z=0.0, dx=600.0, dy=600.0, dz=600.0)  # pegado al vecino
    dead_sliver = Box(id="b", x=0.0, y=650.0, z=0.0, dx=600.0, dy=600.0, dz=600.0)  # 50mm de hueco muerto
    cand = _Candidate(0.0, 0.0, 0.0, None)

    cost_flush = _box_compactness_cost(flush, cand, container, [neighbor])
    cost_sliver = _box_compactness_cost(dead_sliver, cand, container, [neighbor])
    # El componente de gap es el ULTIMO campo de la tupla -ver
    # _box_compactness_cost: (from_right, cand.y, gap_cost), posicion
    # primero por diseno (seccion 17/23 del pedido, ver esa funcion).
    assert cost_sliver[-1] > cost_flush[-1], "una franja muerta de 50mm debe costar mas que quedar flush"


# ==========================================================================
# Part 21 -- Boxes: no regression vs. first-fit, gap-fill only helps
# ==========================================================================


def test_boxes_still_start_at_back_right_floor():
    """Regresion: la correccion de ancla de la tarea anterior no debe
    verse afectada por este pase de calidad."""
    result = pack_container([_box("B0", 600, 600, 600)], CONTAINER)
    p = result.placed[0]
    assert (CONTAINER.length - (p.x + p.dx)) <= 1e-6
    assert (CONTAINER.width - (p.y + p.dy)) <= 1e-6
    assert p.z <= 1e-6


def test_boxes_dataset_a_no_capacity_loss_vs_before():
    """Seccion 23 del pedido: la calidad NUNCA debe costar capacidad -0
    unidades sin cargar en un dataset que fisicamente entra completo."""
    result = pack_container(_dataset_a_mixed_boxes(), CONTAINER)
    assert len(result.unloaded) == 0


def test_boxes_dataset_a_is_deterministic():
    items = _dataset_a_mixed_boxes()
    positions_a = [(round(p.x), round(p.y), round(p.z)) for p in sorted(pack_container(items, CONTAINER).placed, key=lambda p: p.id)]
    positions_b = [(round(p.x), round(p.y), round(p.z)) for p in sorted(pack_container(items, CONTAINER).placed, key=lambda p: p.id)]
    assert positions_a == positions_b


def test_boxes_same_size_dataset_unaffected():
    """Regresion explicita: Boxes del mismo tamano no deben cambiar de
    comportamiento -mismo first-fit de siempre, sin huecos que rellenar."""
    result = pack_container(_dataset_b_same_size_boxes(), CONTAINER)
    assert len(result.unloaded) == 0
    assert average_lateral_gap(result.placed, CONTAINER) == 0.0
    assert narrow_sliver_count(result.placed, CONTAINER) == 0


def test_box_quality_report_runs_on_real_layout():
    result = pack_container(_dataset_a_mixed_boxes(4), CONTAINER)
    report = box_quality_report(result.placed, len(result.unloaded), CONTAINER, result.metrics.used_volume_pct)
    assert report.loaded_units == len(result.placed)
    assert 0.0 <= report.wall_contact_ratio <= 1.0
    assert 0.0 <= report.neighbor_contact_ratio <= 1.0
    assert report.average_lateral_gap_mm >= 0.0


# ==========================================================================
# Part 22 -- Panels: bilateral gradient preserved, gap-fill improves capacity
# ==========================================================================


def test_panels_bilateral_gradient_preserved_after_quality_pass():
    result = pack_container(_dataset_c_tall_short_panels(), CONTAINER)
    assert len(result.unloaded) == 0
    violations = panel_height_order_violations(result.placed, CONTAINER)
    total_sides = 2 * panel_module_count(result.placed)
    assert violations <= total_sides  # tolerancia laxa, mismo criterio que test_panels_lateral_heuristic.py
    assert panel_wall_occupancy(result.placed, CONTAINER) > 0.0


def test_panels_gap_fill_prevents_avoidable_unloading():
    """Acceptance test central de la tarea (Part 15/16/23 del pedido): un
    item (BIG) que NO entra en el hueco de turno, pero que un item
    PENDIENTE mas chico (SMALL) si podria usar, no debe quedar sin cargar
    solo porque el orden de la estrategia lo proceso antes que a SMALL."""
    items, container = _dataset_e_narrow_module()
    result = pack_container(items, container, strategy="largest_volume")
    assert len(result.unloaded) == 0, f"gap-fill deberia haber evitado dejar piezas sin cargar: {[u.id for u in result.unloaded]}"
    by_id = {p.id: p for p in result.placed}
    assert "SMALL-001" in by_id and "BIG-001" in by_id and "SEED-001" in by_id


def test_panels_gap_fill_disabled_reference_loses_capacity():
    """Contraprueba: sin gap-fill (monkeypatch a siempre-None), el MISMO
    dataset SI pierde una pieza -confirma que la mejora es real y
    atribuible a _find_gap_fill_substitute, no a otra cosa."""
    from app.core import packer

    items, container = _dataset_e_narrow_module()
    orig = packer._find_gap_fill_substitute
    packer._find_gap_fill_substitute = lambda *a, **k: None
    try:
        result = pack_container(items, container, strategy="largest_volume")
    finally:
        packer._find_gap_fill_substitute = orig
    assert len(result.unloaded) == 1
    assert result.unloaded[0].id == "BIG-001"


def test_panel_quality_report_runs_on_real_layout():
    result = pack_container(_dataset_c_tall_short_panels(), CONTAINER)
    report = panel_quality_report(result.placed, len(result.unloaded), CONTAINER, result.metrics.used_volume_pct)
    assert report.loaded_units == len(result.placed)
    assert report.module_count >= 1
    assert report.total_lateral_gap_mm >= 0.0


# ==========================================================================
# Part 26/27 -- regression: manual validation geometry / sequence untouched
# ==========================================================================


def test_placed_geometry_still_passes_hard_constraints_after_quality_pass():
    """No cambia ninguna regla fisica -solo verifica, sobre un layout real
    generado con el motor nuevo, que sigue siendo geometricamente valido
    (sin colisiones, dentro de limites) usando las mismas funciones de
    geometry.py que la validacion manual reutiliza."""
    from app.core.geometry import Box, boxes_overlap, within_container

    result = pack_container(_dataset_a_mixed_boxes(6), CONTAINER)
    boxes = [Box(id=p.id, x=p.x, y=p.y, z=p.z, dx=p.dx, dy=p.dy, dz=p.dz) for p in result.placed]
    for b in boxes:
        assert within_container(b, CONTAINER.length, CONTAINER.width, CONTAINER.height)
    for i, a in enumerate(boxes):
        for b in boxes[i + 1 :]:
            assert not boxes_overlap(a, b)


def test_sequence_and_steps_still_work_after_quality_pass():
    """Regresion Part 27: BACK_RIGHT anclado, Loading Sequence y Loading
    Steps siguen funcionando sobre un layout generado con el motor de
    calidad nuevo -no se redisena el sequence engine."""
    from app.core.sequence import compute_load_sequence, compute_load_steps, is_at_back_right_floor

    result = pack_container(_dataset_c_tall_short_panels(), CONTAINER)
    by_id = {p.id: p for p in result.placed}
    sequence = compute_load_sequence(result.placed, CONTAINER)
    assert is_at_back_right_floor(by_id[sequence[0]], CONTAINER)
    steps = compute_load_steps(result.placed, CONTAINER)
    assert sequence[0] in steps[0]
