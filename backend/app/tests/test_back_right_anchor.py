"""CUBOX 2.0 -- BACK_RIGHT Loading Anchor Regression Audit & Fix.

Convencion de coordenadas (documentada tambien al inicio de core/
sequence.py, no una segunda definicion): puerta en x=0, fondo en x=length;
RIGHT = y grande (cerca de width), LEFT = y chico (cerca de 0), igual
criterio que compute_metrics (left_weight/right_weight); z=0 es el piso.

Root cause (dos bugs distintos, ambos confirmados con coordenadas reales,
nunca por inspeccion visual):

1. packer.py: BOX/PALLET/CUSTOM solo tenian un seed inicial IZQUIERDO -el
   camino first-fit filtraba explicitamente `if cand.from_right: continue`,
   asi que la primera pieza de CUALQUIER plan (no solo Panels & Fragile)
   quedaba en BACK_LEFT_FLOOR. Para Panels & Fragile especificamente, AMBOS
   seeds (izquierdo y derecho) empataban a costo 0 en _panel_lateral_cost
   para la primera pieza, y el desempate de `min()` de Python (primer
   elemento en orden de insercion) favorecia sistematicamente al seed
   izquierdo (insertado primero).

2. sequence.py: `_anchored_load_order_with_warnings` usaba `-p.x` (borde
   CERCANO) como criterio primario de profundidad -para piezas de distinto
   grosor en el eje X que de todos modos tocan la MISMA pared del fondo,
   esto podia hacer que Step 1 se anclara en una pieza que ni tocaba el
   fondo, salteando la pieza real de la esquina.

Fix: el seed canonico (from_right=True, BACK_RIGHT_FLOOR) es ahora el UNICO
seed inicial para TODO item_type; el seed izquierdo solo se agrega para
planes de Panels & Fragile (habilita el llenado bilateral), y el desempate
de costo lateral empatado ahora prefiere from_right=True explicitamente.
`back_depth` en sequence.py ahora mide distancia real a la pared del fondo
(container.length - (x+dx)), no `-x` crudo."""

from app.core.packer import pack_container
from app.core.reserved_zones import ReservedZone
from app.core.sequence import (
    compute_load_dependencies,
    compute_load_sequence,
    compute_load_steps,
    is_at_back_right_floor,
)
from app.models.containers import get_container
from app.models.schemas import ItemType, WindowItem


def _panel(code: str, height: float, **overrides) -> WindowItem:
    defaults = dict(
        code=code, description=f"Panel {height}", width=400, height=height, thickness=40, weight=15,
        quantity=1, item_type=ItemType.PANEL, stackable=False,
    )
    defaults.update(overrides)
    return WindowItem(**defaults)


def _box(code: str, **overrides) -> WindowItem:
    defaults = dict(
        code=code, description="Box", width=600, height=600, thickness=600, weight=20,
        quantity=1, item_type=ItemType.BOX, stackable=True,
    )
    defaults.update(overrides)
    return WindowItem(**defaults)


def _pallet(code: str, **overrides) -> WindowItem:
    defaults = dict(
        code=code, description="Pallet", width=1200, height=1000, thickness=1000, weight=300,
        quantity=1, item_type=ItemType.PALLET, stackable=True,
    )
    defaults.update(overrides)
    return WindowItem(**defaults)


def _custom(code: str, **overrides) -> WindowItem:
    defaults = dict(
        code=code, description="Custom", width=800, height=800, thickness=800, weight=50,
        quantity=1, item_type=ItemType.CUSTOM, stackable=True,
    )
    defaults.update(overrides)
    return WindowItem(**defaults)


CONTAINER = get_container("40ft_standard")


# ==========================================================================
# Acceptance test 11 -- single item, every Load Type
# ==========================================================================


def test_first_panel_at_back_right_floor():
    result = pack_container([_panel("P0", 2400)], CONTAINER)
    assert len(result.unloaded) == 0
    assert is_at_back_right_floor(result.placed[0], CONTAINER)


def test_first_box_at_back_right_floor():
    result = pack_container([_box("B0")], CONTAINER)
    assert len(result.unloaded) == 0
    assert is_at_back_right_floor(result.placed[0], CONTAINER)


def test_first_pallet_at_back_right_floor():
    result = pack_container([_pallet("PL0")], CONTAINER)
    assert len(result.unloaded) == 0
    assert is_at_back_right_floor(result.placed[0], CONTAINER)


def test_first_custom_at_back_right_floor():
    result = pack_container([_custom("C0")], CONTAINER)
    assert len(result.unloaded) == 0
    assert is_at_back_right_floor(result.placed[0], CONTAINER)


# ==========================================================================
# Acceptance test 12 -- multiple panels, bilateral heuristic AFTER anchor
# ==========================================================================


def test_first_of_multiple_panels_still_at_back_right_floor():
    heights = [2400, 2300, 2200, 2000, 1900, 1600, 1200, 1000, 800, 600]
    items = [_panel(f"P{i}", h) for i, h in enumerate(heights)]
    result = pack_container(items, CONTAINER)
    assert len(result.unloaded) == 0
    assert is_at_back_right_floor(result.placed[0], CONTAINER), (
        "la primera pieza colocada (packer insertion order) debe seguir anclada a BACK_RIGHT_FLOOR "
        "incluso con el heuristico bilateral de Panels & Fragile activo"
    )


def test_panel_bilateral_gradient_still_reaches_both_walls_after_anchor_fix():
    """Regresion explicita del pedido: 'Confirm both-wall gradient remains'
    -el fix del ancla NUNCA debe remover el llenado bilateral (seccion 5:
    'The bilateral height heuristic remains required. Do not remove it')."""
    heights = [2400, 2300, 2200, 2000, 1900, 1600, 1200, 1000, 800, 600]
    items = [_panel(f"P{i}", h) for i, h in enumerate(heights)]
    result = pack_container(items, CONTAINER)
    assert len(result.unloaded) == 0

    half_width = CONTAINER.width / 2
    left_side = [p for p in result.placed if p.y < half_width]
    right_side = [p for p in result.placed if p.y >= half_width]
    assert len(left_side) >= 3, "la pared izquierda debe seguir recibiendo piezas (llenado bilateral)"
    assert len(right_side) >= 3, "la pared derecha debe seguir recibiendo piezas"


def test_panel_tie_break_prefers_back_right_not_insertion_order():
    """Root cause especifico confirmado (seccion 3 del pedido): para la
    PRIMERA pieza, ambos seeds (izquierdo y derecho) empatan a costo 0 en
    _panel_lateral_cost -el desempate debe preferir from_right=True
    (BACK_RIGHT), nunca "quien se inserto primero en la lista"."""
    result = pack_container([_panel("SOLO", 2400)], CONTAINER)
    assert len(result.unloaded) == 0
    p = result.placed[0]
    assert (CONTAINER.width - (p.y + p.dy)) <= 1e-6, "debe tocar la pared DERECHA, no la izquierda"


# ==========================================================================
# Reserved-zone fallback (seccion 7 del pedido -- tested separately)
# ==========================================================================


def test_reserved_zone_blocking_right_side_falls_back_but_stays_anchored():
    """BACK_RIGHT_FLOOR puede genuinamente no estar disponible (zona
    reservada) -no debe fallar el packing, y el fallback debe seguir
    tocando fondo+piso (misma direccion de ancla), aunque no la pared
    derecha exacta."""
    zone = ReservedZone(
        x=0, y=CONTAINER.width - 500, z=0, length=CONTAINER.length, width=500, height=CONTAINER.height,
    )
    items = [_box(f"B{i}") for i in range(10)]
    result = pack_container(items, CONTAINER, reserved_zones=[zone])
    assert len(result.unloaded) == 0, "no debe fallar el packing solo porque la esquina exacta no esta disponible"
    first = result.placed[0]
    assert (CONTAINER.length - (first.x + first.dx)) <= 1e-6, "debe seguir tocando la pared del fondo"
    assert first.z <= 1e-6, "debe seguir tocando el piso"


def test_reserved_zone_does_not_regress_when_corner_is_free():
    """Regresion: una zona reservada que NO toca la esquina BACK_RIGHT_FLOOR
    no debe impedir que la primera pieza siga anclando ahi."""
    zone = ReservedZone(x=0, y=0, z=0, length=CONTAINER.length, width=300, height=CONTAINER.height)
    items = [_box(f"B{i}") for i in range(6)]
    result = pack_container(items, CONTAINER, reserved_zones=[zone])
    assert len(result.unloaded) == 0
    assert is_at_back_right_floor(result.placed[0], CONTAINER)


# ==========================================================================
# Packer placement -> load_sequence -> load_steps (seccion 8/9 del pedido)
# ==========================================================================


def test_step1_contains_the_canonical_anchor_piece_boxes():
    items = [_box(f"B{i}") for i in range(20)]
    result = pack_container(items, CONTAINER)
    assert len(result.unloaded) == 0
    by_id = {p.id: p for p in result.placed}

    sequence = compute_load_sequence(result.placed, CONTAINER)
    anchor_piece = by_id[sequence[0]]
    assert is_at_back_right_floor(anchor_piece, CONTAINER), "el primer piece de load_sequence debe ser el ancla real"

    steps = compute_load_steps(result.placed, CONTAINER)
    assert sequence[0] in steps[0], "Step 1 debe contener la pieza ancla"


def test_step1_contains_the_canonical_anchor_piece_panels():
    heights = [2400, 2300, 2200, 2000, 1900, 1600, 1200, 1000, 800, 600]
    items = [_panel(f"P{i}", h) for i, h in enumerate(heights)]
    result = pack_container(items, CONTAINER)
    assert len(result.unloaded) == 0
    by_id = {p.id: p for p in result.placed}

    sequence = compute_load_sequence(result.placed, CONTAINER)
    anchor_piece = by_id[sequence[0]]
    assert is_at_back_right_floor(anchor_piece, CONTAINER)

    steps = compute_load_steps(result.placed, CONTAINER)
    assert sequence[0] in steps[0]


# ==========================================================================
# Support invariant (seccion 10 del pedido)
# ==========================================================================


def test_no_step_piece_depends_on_support_from_a_future_step():
    """Para CADA step (no solo Step 1): cada pieza esta en el piso, o su
    dependencia de soporte ya aparecio antes -en el mismo step o en uno
    anterior- nunca en un step posterior."""
    items = [_pallet(f"PL{i}") for i in range(16)]
    result = pack_container(items, CONTAINER)
    assert len(result.unloaded) == 0

    boxes_by_id = {p.id: p for p in result.placed}
    deps = compute_load_dependencies(result.placed)
    steps = compute_load_steps(result.placed, CONTAINER)

    step_index_by_piece: dict[str, int] = {}
    position_within_step: dict[str, int] = {}
    for step_idx, step_ids in enumerate(steps):
        for pos, pid in enumerate(step_ids):
            step_index_by_piece[pid] = step_idx
            position_within_step[pid] = pos

    for pid, piece_deps in deps.items():
        if pid not in step_index_by_piece:
            continue
        p = boxes_by_id[pid]
        if p.z <= 1e-6:
            continue  # en el piso, no depende de nada
        for dep_id in piece_deps:
            assert dep_id in step_index_by_piece, f"{pid} depende de {dep_id}, que no aparece en ningun step"
            dep_step = step_index_by_piece[dep_id]
            own_step = step_index_by_piece[pid]
            assert dep_step <= own_step, f"{pid} (step {own_step}) depende de {dep_id} de un step FUTURO ({dep_step})"
            if dep_step == own_step:
                assert position_within_step[dep_id] < position_within_step[pid], (
                    f"{pid} depende de {dep_id} en el MISMO step, pero {dep_id} aparece despues"
                )


def test_step1_never_loads_onto_future_support_panels_and_boxes_mixed():
    items = [_box(f"B{i}") for i in range(6)] + [_pallet(f"PL{i}") for i in range(6)]
    result = pack_container(items, CONTAINER)
    assert len(result.unloaded) == 0

    boxes_by_id = {p.id: p for p in result.placed}
    deps = compute_load_dependencies(result.placed)
    steps = compute_load_steps(result.placed, CONTAINER)
    step1_ids = set(steps[0])

    for idx, pid in enumerate(steps[0]):
        p = boxes_by_id[pid]
        if p.z <= 1e-6:
            continue
        for dep_id in deps.get(pid, []):
            assert dep_id in step1_ids and steps[0].index(dep_id) < idx, (
                f"Step 1 piece {pid} depends on {dep_id}, which is not loaded earlier within Step 1"
            )


# ==========================================================================
# Deterministic first anchor across repeated runs (seccion 15 del pedido)
# ==========================================================================


def test_first_anchor_is_deterministic_across_repeated_runs_boxes():
    items = [_box(f"B{i}") for i in range(15)]
    positions = set()
    for _ in range(5):
        result = pack_container(items, CONTAINER)
        assert len(result.unloaded) == 0
        p = result.placed[0]
        positions.add((round(p.x, 3), round(p.y, 3), round(p.z, 3)))
    assert len(positions) == 1, f"la primera pieza debe caer SIEMPRE en la misma posicion, dio: {positions}"


def test_first_anchor_is_deterministic_across_repeated_runs_panels():
    heights = [2400, 2300, 2200, 2000, 1900, 1600, 1200, 1000, 800, 600]
    items = [_panel(f"P{i}", h) for i, h in enumerate(heights)]
    positions = set()
    for _ in range(5):
        result = pack_container(items, CONTAINER)
        assert len(result.unloaded) == 0
        p = result.placed[0]
        positions.add((round(p.x, 3), round(p.y, 3), round(p.z, 3)))
    assert len(positions) == 1, f"la primera pieza debe caer SIEMPRE en la misma posicion, dio: {positions}"


# ==========================================================================
# Global Load-Type audit (seccion 6 del pedido) -- helper itself
# ==========================================================================


def test_is_at_back_right_floor_rejects_left_wall_piece():
    """El helper debe distinguir correctamente BACK_LEFT de BACK_RIGHT -no
    solo "toca alguna pared"."""
    from app.core.geometry import Box

    left_piece = Box(id="x", x=CONTAINER.length - 600, y=0, z=0, dx=600, dy=600, dz=600)
    from app.models.schemas import PlacedPiece

    left_placed = PlacedPiece(
        id="x", code="X", description="d", weight=1, stackable=True, priority=1,
        x=left_piece.x, y=left_piece.y, z=left_piece.z, dx=600, dy=600, dz=600,
        orientation_label="P1-a", source_width=600, source_height=600, source_thickness=600,
    )
    assert not is_at_back_right_floor(left_placed, CONTAINER)


def test_is_at_back_right_floor_accepts_exact_corner():
    from app.models.schemas import PlacedPiece

    corner_placed = PlacedPiece(
        id="x", code="X", description="d", weight=1, stackable=True, priority=1,
        x=CONTAINER.length - 600, y=CONTAINER.width - 600, z=0, dx=600, dy=600, dz=600,
        orientation_label="P1-a", source_width=600, source_height=600, source_thickness=600,
    )
    assert is_at_back_right_floor(corner_placed, CONTAINER)
