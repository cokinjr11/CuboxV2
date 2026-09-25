"""Orden de carga/descarga (secciones 17-21 de V2, Anchored Loading Sequence
post-V4): fondo antes que frente, abajo antes que arriba; la carga ademas
arranca de una esquina fija y crece por modulos adyacentes."""

import pytest

from app.core.sequence import (
    ANCHOR_TOLERANCE_MM,
    MAX_STEP_SIZE_AUTO,
    chunk_sequence,
    compute_blocking_pairs,
    compute_load_dependencies,
    compute_load_sequence,
    compute_load_steps,
    compute_operational_warnings,
    compute_unload_dependencies,
    compute_unload_sequence,
    compute_unload_steps,
    detect_operational_warnings,
    detect_sequence_cycle,
)
from app.models.schemas import ContainerSpec, LoadingAnchor, LoadingOpeningType, OperationalWarningType, PlacedPiece

# Contenedor grande a proposito: piezas en coordenadas chicas (x/y < 1500) no
# tocan por accidente la pared del fondo o la lateral, asi los tests que no
# estan probando el anclaje no se ven afectados por el a partir de ahora.
CONTAINER = ContainerSpec(id="test", name="Test", length=5000, width=5000, height=3000, max_weight=1_000_000)


def _piece(piece_id, x, z=0, y=0, delivery_sequence=None, dx=100, dy=100, dz=100):
    return PlacedPiece(
        id=piece_id,
        code=piece_id,
        weight=10,
        stackable=True,
        priority=1,
        delivery_sequence=delivery_sequence,
        x=x,
        y=y,
        z=z,
        dx=dx,
        dy=dy,
        dz=dz,
        orientation_label="P1-a",
        source_width=dx,
        source_height=dy,
        source_thickness=dz,
    )


def test_load_sequence_is_back_before_front():
    # "front" (cerca de la puerta, x=0) vs "back" (fondo, x alto)
    front = _piece("front", x=0)
    back = _piece("back", x=1000)
    sequence = compute_load_sequence([front, back], CONTAINER)
    assert sequence.index("back") < sequence.index("front")


def test_load_sequence_is_bottom_before_top_when_stacked():
    bottom = _piece("bottom", x=0, z=0)
    top = _piece("top", x=0, z=100)
    sequence = compute_load_sequence([bottom, top], CONTAINER)
    assert sequence.index("bottom") < sequence.index("top")


def test_unload_sequence_is_reverse_of_load_sequence():
    pieces = [_piece("a", x=0), _piece("b", x=500), _piece("c", x=1000)]
    load = compute_load_sequence(pieces, CONTAINER)
    unload = compute_unload_sequence(pieces)
    assert unload == list(reversed(load))


def test_load_sequence_includes_every_piece_exactly_once():
    pieces = [_piece(f"p{i}", x=i * 100) for i in range(6)]
    sequence = compute_load_sequence(pieces, CONTAINER)
    assert sorted(sequence) == sorted(p.id for p in pieces)
    assert len(sequence) == len(set(sequence))


def test_load_sequence_never_loads_a_piece_before_its_support():
    """La dependencia de soporte manda sobre el orden espacial: nada se carga
    antes que lo que lo sostiene, incluso si (tras ediciones manuales) el
    soporte quedara mas cerca de la puerta que la pieza apoyada."""
    support = _piece("support", x=0, z=0)  # cerca de la puerta
    top = _piece("top", x=0, z=100)  # mismo x/y que support -> se apoya en el

    sequence = compute_load_sequence([support, top], CONTAINER)
    assert sequence.index("support") < sequence.index("top")


def test_unload_sequence_never_unloads_a_piece_before_what_rests_on_it():
    bottom = _piece("bottom", x=0, z=0)
    top = _piece("top", x=0, z=100)
    sequence = compute_unload_sequence([bottom, top])
    assert sequence.index("top") < sequence.index("bottom")


def test_unload_sequence_prefers_lower_delivery_sequence_first():
    stop1 = _piece("stop1", x=0, y=0, delivery_sequence=1)
    stop2 = _piece("stop2", x=0, y=200, delivery_sequence=2)
    sequence = compute_unload_sequence([stop1, stop2])
    assert sequence.index("stop1") < sequence.index("stop2")


def test_load_sequence_starts_from_back_right_anchor_not_an_isolated_piece_near_door():
    """Seccion 31: la secuencia debe empezar por la pieza mas anclada a
    fondo+lateral (BACK_RIGHT por defecto), nunca por una pieza aislada cerca
    de la puerta aunque geometricamente sea "igual de valida"."""
    anchor_piece = _piece("anchor", x=CONTAINER.length - 100, y=CONTAINER.width - 100, z=0)
    isolated_near_door = _piece("isolated", x=50, y=1400, z=0)  # cerca de la puerta, sin tocar ninguna pared
    others = [_piece(f"mid{i}", x=2000 + i * 300, y=1000, z=0) for i in range(3)]

    pieces = [anchor_piece, isolated_near_door, *others]
    sequence = compute_load_sequence(pieces, CONTAINER)
    assert sequence[0] == "anchor"
    assert sequence.index("isolated") > 0


def test_load_sequence_back_left_anchor_prefers_the_other_side_wall():
    right_wall_piece = _piece("right", x=CONTAINER.length - 100, y=CONTAINER.width - 100, z=0)
    left_wall_piece = _piece("left", x=CONTAINER.length - 100, y=0, z=0)

    sequence = compute_load_sequence([right_wall_piece, left_wall_piece], CONTAINER, LoadingAnchor.BACK_LEFT)
    assert sequence[0] == "left"


def test_compute_load_steps_respects_support_dependency():
    """support y top pueden terminar en el MISMO step (son un modulo fisico
    coherente: apenas se apoya una pieza sobre otra, es natural cargarlas
    juntas) -lo que nunca puede pasar es que top aparezca antes que support
    en el orden global (steps concatenados)."""
    support = _piece("support", x=0, y=0, z=0)
    top = _piece("top", x=0, y=0, z=100)  # mismo x/y -> se apoya en support
    steps = compute_load_steps([support, top], CONTAINER)
    flat = [pid for step in steps for pid in step]
    assert flat.index("support") < flat.index("top")


def test_compute_load_steps_splits_large_waves():
    """10 piezas al piso, en fila continua (dx=100 cada 100mm, se tocan entre
    si) sin dependencia de soporte: forman un solo modulo fisico, pero no debe
    quedar como un solo step gigante de 10 -se sub-divide en lotes de a lo
    sumo MAX_STEP_SIZE_AUTO."""
    pieces = [_piece(f"p{i}", x=i * 100, z=0) for i in range(10)]
    steps = compute_load_steps(pieces, CONTAINER)
    assert len(steps[0]) == MAX_STEP_SIZE_AUTO
    assert sum(len(s) for s in steps) == 10
    assert all(len(s) <= MAX_STEP_SIZE_AUTO for s in steps)


def test_compute_load_steps_includes_every_piece_exactly_once():
    pieces = [_piece(f"p{i}", x=i * 100) for i in range(8)]
    steps = compute_load_steps(pieces, CONTAINER)
    flat = [pid for step in steps for pid in step]
    assert sorted(flat) == sorted(p.id for p in pieces)
    assert len(flat) == len(set(flat))


def test_compute_load_steps_keeps_two_physical_modules_separate():
    """Seccion 33: 2 modulos de 3 ventanas paralelas contra la pared elegida,
    separados entre si por mas que ANCHOR_TOLERANCE_MM Y en bandas de Y
    distintas (para no solaparse en Y-Z y no generar una dependencia de
    bloqueo lateral -Fase 6A- entre ellos), no deben mezclarse en el mismo
    Step solo porque coincida un contador."""
    module1 = [_piece(f"m1_{i}", x=CONTAINER.length - 100, y=CONTAINER.width - 100 - i * 100, z=0) for i in range(3)]
    gap = ANCHOR_TOLERANCE_MM + 500
    module2_x = CONTAINER.length - 100 - 100 - gap
    module2 = [_piece(f"m2_{i}", x=module2_x, y=1500 - i * 100, z=0) for i in range(3)]

    steps = compute_load_steps(module1 + module2, CONTAINER)
    module1_ids = {p.id for p in module1}
    module2_ids = {p.id for p in module2}
    for step in steps:
        step_set = set(step)
        assert not (step_set & module1_ids and step_set & module2_ids), f"un step mezclo los 2 modulos separados: {step}"


def test_compute_load_steps_merges_modules_linked_by_a_blocking_dependency():
    """Fase 6A: a diferencia del test anterior, si 2 "modulos" separados en X
    SI se solapan en Y-Z (el mas cercano a la puerta le tapa el acceso al mas
    lejano), ahora existe una dependencia dura entre ellos -es correcto que
    terminen conectados por `compute_load_dependencies`, aunque la distancia
    en X supere ANCHOR_TOLERANCE_MM."""
    far_from_door = [_piece(f"m1_{i}", x=CONTAINER.length - 100, y=CONTAINER.width - 100 - i * 100, z=0) for i in range(3)]
    gap = ANCHOR_TOLERANCE_MM + 500
    near_door_x = CONTAINER.length - 100 - 100 - gap
    # Mismas bandas de Y que far_from_door -> se solapan en Y-Z, near_door
    # bloquea el acceso a far_from_door.
    near_door = [_piece(f"m2_{i}", x=near_door_x, y=CONTAINER.width - 100 - i * 100, z=0) for i in range(3)]

    deps = compute_load_dependencies(far_from_door + near_door)
    for i in range(3):
        assert far_from_door[i].id in deps[near_door[i].id], (
            "near_door deberia depender de far_from_door (bloqueo lateral, Fase 6A)"
        )

    sequence = compute_load_sequence(far_from_door + near_door, CONTAINER)
    for i in range(3):
        assert sequence.index(far_from_door[i].id) < sequence.index(near_door[i].id)


def test_compute_unload_steps_never_unloads_a_piece_before_what_rests_on_it():
    bottom = _piece("bottom", x=0, y=0, z=0)
    top = _piece("top", x=0, y=0, z=100)
    steps = compute_unload_steps([bottom, top])
    flat = [pid for step in steps for pid in step]
    assert flat.index("top") < flat.index("bottom")


def test_detect_operational_warnings_clean_case_has_no_warnings():
    anchor_piece = _piece("anchor", x=CONTAINER.length - 100, y=CONTAINER.width - 100, z=0)
    adjacent_piece = _piece("adjacent", x=CONTAINER.length - 100, y=CONTAINER.width - 200, z=0)
    warnings = detect_operational_warnings([anchor_piece, adjacent_piece], CONTAINER)
    assert warnings == []


def test_detect_operational_warnings_flags_a_truly_isolated_piece():
    """Seccion 19: una pieza correctamente apoyada en el piso pero sin
    ninguna referencia de pared/lateral/pieza-ya-cargada debe generar un
    OPERATIONAL_LOADABILITY_WARNING (el piso solo no cuenta como referencia
    de posicionamiento, ver seccion 10)."""
    anchor_piece = _piece("anchor", x=CONTAINER.length - 100, y=CONTAINER.width - 100, z=0)
    isolated = _piece("isolated", x=1000, y=1000, z=0)  # lejos de todo, ninguna referencia
    warnings = detect_operational_warnings([anchor_piece, isolated], CONTAINER)
    assert any("isolated" in w for w in warnings)


def test_chunk_sequence_splits_into_fixed_size_batches():
    sequence = [f"p{i}" for i in range(7)]
    chunks = chunk_sequence(sequence, 3)
    assert chunks == [["p0", "p1", "p2"], ["p3", "p4", "p5"], ["p6"]]


def test_chunk_sequence_rejects_non_positive_size():
    with pytest.raises(ValueError):
        chunk_sequence(["a", "b"], 0)


# ==========================================================================
# Fase 5.1 -Operational Load/Unload Sequence Refinement (seccion 19 del
# pedido). Contenedor grande (5000x5000x3000) reutilizado de arriba: BACK_RIGHT
# ancla en x=length-100 (fondo), y=width-100 (RIGHT, ver docstring del modulo
# para la convencion Y confirmada contra packer.py).
# ==========================================================================

BACK_X = CONTAINER.length - 100
RIGHT_Y = CONTAINER.width - 100
MID_Y = CONTAINER.width - 200
LEFT_Y = CONTAINER.width - 300


def _column(prefix, x, y, levels=3):
    """3 piezas apiladas (mismo x/y, z=0/100/200): cada una se apoya en la
    anterior -una columna operativa de una sola pieza de ancho."""
    return [_piece(f"{prefix}{i}", x=x, y=y, z=i * 100) for i in range(levels)]


def test_sequence_a_simple_3x3_wall_starts_bottom_right_and_builds_up_before_sweeping_left():
    right_col = _column("r", BACK_X, RIGHT_Y)
    mid_col = _column("m", BACK_X, MID_Y)
    left_col = _column("l", BACK_X, LEFT_Y)
    pieces = right_col + mid_col + left_col

    sequence = compute_load_sequence(pieces, CONTAINER)

    assert sequence[0] == "r0"
    for col in ("r", "m", "l"):
        ids = [f"{col}{i}" for i in range(3)]
        assert [sequence.index(i) for i in ids] == sorted(sequence.index(i) for i in ids), (
            f"la columna {col} no se cargo de abajo hacia arriba: {sequence}"
        )
    # cada columna se completa (sube) antes de saltar del todo a la siguiente:
    # el ultimo elemento de una columna debe ir antes que el primero de la
    # siguiente (derecha -> centro -> izquierda).
    assert sequence.index("r2") < sequence.index("m0")
    assert sequence.index("m2") < sequence.index("l0")


def test_sequence_b_support_dependency_bottom_always_before_top():
    bottom = _piece("bottom", x=0, z=0)
    top = _piece("top", x=0, z=100)  # mismo x/y -> se apoya en bottom
    sequence = compute_load_sequence([bottom, top], CONTAINER)
    assert sequence.index("bottom") < sequence.index("top")


def test_sequence_c_multiple_supports_both_load_before_top():
    support_a = _piece("support_a", x=0, y=0, z=0, dx=100, dy=100)
    support_b = _piece("support_b", x=0, y=100, z=0, dx=100, dy=100)
    # dy=200 -> se apoya sobre AMBOS soportes (solapa 100x100 con cada uno)
    top = _piece("top", x=0, y=0, z=100, dx=100, dy=200)

    sequence = compute_load_sequence([support_a, support_b, top], CONTAINER)

    assert sequence.index("support_a") < sequence.index("top")
    assert sequence.index("support_b") < sequence.index("top")


def test_sequence_d_prefers_completing_local_column_before_distant_floor_piece():
    right_col = _column("r", BACK_X, RIGHT_Y)
    distant_floor = _piece("distant", x=500, y=500, z=0)  # lejos, sin relacion

    sequence = compute_load_sequence(right_col + [distant_floor], CONTAINER)

    assert sequence[0] == "r0"
    assert sequence.index("r2") < sequence.index("distant"), (
        "la columna activa debe completarse antes de saltar a una pieza de piso lejana"
    )


def test_sequence_e_back_right_sweeps_physically_right_to_left():
    right = _piece("right", x=BACK_X, y=RIGHT_Y, z=0)
    mid = _piece("mid", x=BACK_X, y=MID_Y, z=0)
    left = _piece("left", x=BACK_X, y=LEFT_Y, z=0)

    sequence = compute_load_sequence([left, mid, right], CONTAINER)

    assert sequence == ["right", "mid", "left"]


def test_sequence_f_back_section_before_equivalent_front_section():
    back = _piece("back", x=CONTAINER.length - 100, y=0)
    front = _piece("front", x=100, y=0)
    sequence = compute_load_sequence([back, front], CONTAINER)
    assert sequence.index("back") < sequence.index("front")


def test_sequence_g_back_left_mirrors_back_right():
    right_col = _column("r", BACK_X, RIGHT_Y)
    left_col = _column("l", BACK_X, LEFT_Y)
    pieces = right_col + left_col

    sequence = compute_load_sequence(pieces, CONTAINER, LoadingAnchor.BACK_LEFT)

    assert sequence[0] == "l0"
    for col in ("r", "l"):
        ids = [f"{col}{i}" for i in range(3)]
        assert [sequence.index(i) for i in ids] == sorted(sequence.index(i) for i in ids)
    assert sequence.index("l2") < sequence.index("r0")


def test_sequence_h_automatic_step_grouping_does_not_produce_one_step_per_piece():
    right_col = _column("r", BACK_X, RIGHT_Y)
    mid_col = _column("m", BACK_X, MID_Y)
    left_col = _column("l", BACK_X, LEFT_Y)
    pieces = right_col + mid_col + left_col  # 9 piezas, un modulo fisico contiguo

    steps = compute_load_steps(pieces, CONTAINER)
    flat = [pid for step in steps for pid in step]

    assert sorted(flat) == sorted(p.id for p in pieces)
    assert len(steps) < len(pieces), f"se esperaban pocos steps agrupados, se obtuvieron {len(steps)}: {steps}"
    assert all(len(s) <= MAX_STEP_SIZE_AUTO for s in steps)


def test_sequence_i_unload_stack_top_before_middle_before_bottom():
    bottom = _piece("bottom", x=0, z=0)
    middle = _piece("middle", x=0, z=100)
    top = _piece("top", x=0, z=200)

    load = compute_load_sequence([bottom, middle, top], CONTAINER)
    assert load.index("bottom") < load.index("middle") < load.index("top")

    unload = compute_unload_sequence([bottom, middle, top])
    assert unload.index("top") < unload.index("middle") < unload.index("bottom")


def test_sequence_j_unload_prefers_door_accessible_module_before_deep_rear_module():
    front_module = [_piece(f"front{i}", x=100, y=i * 100, z=0) for i in range(2)]
    rear_module = [_piece(f"rear{i}", x=CONTAINER.length - 100, y=i * 100, z=0) for i in range(2)]

    unload = compute_unload_sequence(front_module + rear_module)

    assert max(unload.index(p.id) for p in front_module) < min(unload.index(p.id) for p in rear_module)


def test_sequence_k_deterministic_across_repeated_runs():
    right_col = _column("r", BACK_X, RIGHT_Y)
    mid_col = _column("m", BACK_X, MID_Y)
    pieces = right_col + mid_col

    first = compute_load_sequence(pieces, CONTAINER)
    second = compute_load_sequence(list(reversed(pieces)), CONTAINER)
    assert first == second

    first_steps = compute_load_steps(pieces, CONTAINER)
    second_steps = compute_load_steps(list(reversed(pieces)), CONTAINER)
    assert first_steps == second_steps


# ==========================================================================
# Fase 6A -Loading & Unloading Sequence Engine: bloqueo lateral (accesibilidad
# hacia la apertura de carga en x=0), grafo de dependencias unificado
# (soporte + bloqueo), deteccion de ciclos, Delivery/Stacking Sequence
# conflicts y limitacion de Loading Opening Type. Ver docstring de
# compute_blocking_pairs en core/sequence.py para la aproximacion geometrica
# exacta (seccion 45 del pedido: tests deterministicos de accesibilidad).
# ==========================================================================


def test_blocking_direct_blocker_closer_to_door_with_full_overlap():
    blocker = _piece("blocker", x=0, y=0)  # cerca de la puerta
    blocked = _piece("blocked", x=200, y=0)  # mismo Y/Z -> se solapa
    pairs = compute_blocking_pairs([blocker, blocked])
    assert ("blocker", "blocked") in pairs
    assert ("blocked", "blocker") not in pairs


def test_blocking_pairs_only_report_the_direct_immediate_blocker_not_the_whole_chain():
    """Regresion (reporte de uso real con ~300 cajas -Fase 6A): una columna
    de N piezas en fila hacia la puerta, todas con el mismo Y/Z, NO debe
    generar pares transitivos (p0 bloqueando a p9, p1 bloqueando a p9, etc)
    -solo la relacion INMEDIATA entre vecinos consecutivos. La version
    anterior devolvia C(N,2) pares por columna, causando una explosion de
    DELIVERY_SEQUENCE_CONFLICT (miles de warnings) para un plan chico."""
    n = 10
    pieces = [_piece(f"p{i}", x=i * 100, y=0) for i in range(n)]  # p0 mas cerca de la puerta

    pairs = compute_blocking_pairs(pieces)

    expected = {(f"p{i}", f"p{i + 1}") for i in range(n - 1)}
    assert set(pairs) == expected, f"se esperaban solo {len(expected)} pares directos, se obtuvieron {len(pairs)}"


def test_blocking_pairs_direct_blocker_with_two_side_by_side_candidates():
    """Si 2 piezas mas cercanas a la puerta bloquean, en paralelo, a la misma
    pieza lejana (sin bloquearse entre si), AMBAS son directas -esto NO debe
    reducirse a una sola (ver test_blocking_multiple_blockers_for_a_single_deep_piece,
    que ya cubre esto para compute_unload_dependencies; este test lo cubre a
    nivel de compute_blocking_pairs directamente)."""
    blocked = _piece("blocked", x=400, y=0, dy=300)  # y en [0,300]
    side_a = _piece("side_a", x=0, y=0, dy=150)  # y en [0,150]
    side_b = _piece("side_b", x=0, y=150, dy=150)  # y en [150,300], mismo x que side_a

    pairs = compute_blocking_pairs([blocked, side_a, side_b])
    assert set(pairs) == {("side_a", "blocked"), ("side_b", "blocked")}


def test_delivery_sequence_conflict_count_stays_linear_not_quadratic_for_a_deep_column():
    """Regresion directa del bug reportado: una columna de N piezas hacia la
    puerta con Delivery Sequence ALTERNADA (para que cada vecino inmediato
    efectivamente entre en conflicto) debe generar a lo sumo N-1
    DELIVERY_SEQUENCE_CONFLICT -nunca del orden de N^2/2."""
    n = 20
    pieces = [_piece(f"p{i}", x=i * 100, y=0, delivery_sequence=(1 if i % 2 == 0 else 99)) for i in range(n)]

    warnings = compute_operational_warnings(pieces, CONTAINER)
    conflicts = [w for w in warnings if w.type == OperationalWarningType.DELIVERY_SEQUENCE_CONFLICT]

    assert len(conflicts) <= n - 1, f"se esperaban <= {n - 1} conflictos, se obtuvieron {len(conflicts)}"


def test_blocking_side_by_side_pieces_do_not_block_each_other():
    """Seccion 38 (Scenario B): 2 piezas al mismo X, lado a lado en Y, no se
    bloquean entre si -ninguna esta "delante" de la otra en el eje de
    extraccion."""
    left = _piece("left", x=0, y=0)
    right = _piece("right", x=0, y=200)
    pairs = compute_blocking_pairs([left, right])
    assert pairs == []


def test_blocking_requires_perpendicular_overlap_not_just_x_ordering():
    """Piezas en X distinto pero SIN solapamiento en Y (corredor lateral
    libre): mas cerca de la puerta no implica bloqueo si no comparten
    columna Y-Z."""
    near_door = _piece("near_door", x=0, y=0)
    deep_offset = _piece("deep_offset", x=1000, y=1000)  # Y totalmente distinto
    pairs = compute_blocking_pairs([near_door, deep_offset])
    assert pairs == []


def test_blocking_partial_perpendicular_overlap_still_blocks():
    """Un solapamiento PARCIAL en Y (no exacto) ya alcanza para bloquear -no
    hace falta que las piezas coincidan exactamente en huella."""
    blocker = _piece("blocker", x=0, y=0, dy=150)  # y en [0,150]
    blocked = _piece("blocked", x=200, y=100, dy=150)  # y en [100,250] -> solapa [100,150]
    pairs = compute_blocking_pairs([blocker, blocked])
    assert ("blocker", "blocked") in pairs


def test_blocking_multiple_blockers_for_a_single_deep_piece():
    deep = _piece("deep", x=400, y=0, dy=300)  # y en [0,300]
    blocker_a = _piece("blocker_a", x=0, y=0, dy=150)  # y en [0,150]
    blocker_b = _piece("blocker_b", x=100, y=150, dy=150)  # y en [150,300]
    deps = compute_unload_dependencies([deep, blocker_a, blocker_b])
    assert set(deps["deep"]) == {"blocker_a", "blocker_b"}


def test_blocking_opening_adjacent_piece_has_no_blockers():
    at_door = _piece("at_door", x=0, y=0)
    behind_it = _piece("behind_it", x=200, y=0)
    deps = compute_unload_dependencies([at_door, behind_it])
    assert deps["at_door"] == []


def test_blocking_deep_piece_with_clear_corridor_has_no_blockers():
    """Pieza lejos de la puerta pero con el corredor Y-Z realmente libre (nada
    comparte su columna): no deberia depender de nada por bloqueo."""
    deep = _piece("deep", x=CONTAINER.length - 100, y=2500)
    unrelated = [_piece(f"far{i}", x=i * 200, y=0) for i in range(5)]
    deps = compute_unload_dependencies([deep, *unrelated])
    assert deps["deep"] == []


def test_load_dependencies_blocker_loads_after_the_piece_it_blocks():
    blocker = _piece("blocker", x=0, y=0)
    blocked = _piece("blocked", x=200, y=0)
    deps = compute_load_dependencies([blocker, blocked])
    assert "blocked" in deps["blocker"]
    sequence = compute_load_sequence([blocker, blocked], CONTAINER)
    assert sequence.index("blocked") < sequence.index("blocker")


def test_unload_dependencies_blocker_unloads_before_the_piece_it_blocks():
    blocker = _piece("blocker", x=0, y=0)
    blocked = _piece("blocked", x=200, y=0)
    deps = compute_unload_dependencies([blocker, blocked])
    assert "blocker" in deps["blocked"]
    sequence = compute_unload_sequence([blocker, blocked])
    assert sequence.index("blocker") < sequence.index("blocked")


def test_delivery_sequence_conflict_when_a_later_delivery_piece_blocks_an_earlier_one():
    """Seccion 17/40 (Scenario D): P002 (Delivery 3) mas cerca de la puerta
    bloquea a P001 (Delivery 1) -el orden fisico debe respetar el bloqueo
    (P002 antes) y generar un DELIVERY_SEQUENCE_CONFLICT."""
    p002 = _piece("P002", x=0, y=0, delivery_sequence=3)
    p001 = _piece("P001", x=200, y=0, delivery_sequence=1)

    sequence = compute_unload_sequence([p002, p001])
    assert sequence.index("P002") < sequence.index("P001")

    warnings = compute_operational_warnings([p002, p001], CONTAINER)
    conflicts = [w for w in warnings if w.type == OperationalWarningType.DELIVERY_SEQUENCE_CONFLICT]
    assert len(conflicts) == 1
    conflict = conflicts[0]
    assert conflict.item_id == "P001"
    assert conflict.blocking_item_id == "P002"
    assert conflict.requested_delivery_sequence == 1
    assert conflict.blocking_delivery_sequence == 3


def test_no_delivery_sequence_conflict_when_the_earlier_stop_is_already_closer_to_the_door():
    """Seccion 41 (Scenario E variant): P001 (Delivery 1) YA esta mas cerca de
    la puerta que P002 (Delivery 3) -no hay conflicto, el orden fisico
    coincide con lo pedido."""
    p001 = _piece("P001", x=0, y=0, delivery_sequence=1)
    p002 = _piece("P002", x=200, y=0, delivery_sequence=3)

    warnings = compute_operational_warnings([p001, p002], CONTAINER)
    assert all(w.type != OperationalWarningType.DELIVERY_SEQUENCE_CONFLICT for w in warnings)


def test_no_delivery_sequence_conflict_when_delivery_sequence_is_equal():
    a = _piece("a", x=0, y=0, delivery_sequence=1)
    b = _piece("b", x=200, y=0, delivery_sequence=1)
    warnings = compute_operational_warnings([a, b], CONTAINER)
    assert all(w.type != OperationalWarningType.DELIVERY_SEQUENCE_CONFLICT for w in warnings)


def test_no_delivery_sequence_warnings_when_delivery_sequence_is_absent():
    """Seccion 8/41 (Scenario E): sin Delivery Sequence, Cubox sigue
    produciendo un orden fisico valido, sin warnings de delivery."""
    blocker = _piece("blocker", x=0, y=0)
    blocked = _piece("blocked", x=200, y=0)
    warnings = compute_operational_warnings([blocker, blocked], CONTAINER)
    assert all(w.type != OperationalWarningType.DELIVERY_SEQUENCE_CONFLICT for w in warnings)


def test_stacking_sequence_conflict_when_the_trapped_base_wants_an_earlier_delivery():
    """Seccion 13/28: B encima de A -A (base) sale despues de B pase lo que
    pase. Si A tiene Delivery Sequence mas bajo que B (queria salir antes),
    es un STACKING_SEQUENCE_CONFLICT, no un DELIVERY_SEQUENCE_CONFLICT
    (causa distinta: soporte vertical, no bloqueo lateral)."""
    base = _piece("base", x=0, y=0, z=0, delivery_sequence=1)
    top = _piece("top", x=0, y=0, z=100, delivery_sequence=3)  # mismo x/y -> se apoya en base

    warnings = compute_operational_warnings([base, top], CONTAINER)
    conflicts = [w for w in warnings if w.type == OperationalWarningType.STACKING_SEQUENCE_CONFLICT]
    assert len(conflicts) == 1
    assert conflicts[0].item_id == "base"
    assert conflicts[0].blocking_item_id == "top"


def test_no_stacking_sequence_conflict_when_delivery_sequence_matches_support_order():
    base = _piece("base", x=0, y=0, z=0, delivery_sequence=3)
    top = _piece("top", x=0, y=0, z=100, delivery_sequence=1)
    warnings = compute_operational_warnings([base, top], CONTAINER)
    assert all(w.type != OperationalWarningType.STACKING_SEQUENCE_CONFLICT for w in warnings)


def test_detect_sequence_cycle_returns_none_for_a_simple_chain():
    deps = {"a": [], "b": ["a"], "c": ["b"]}
    assert detect_sequence_cycle(deps) is None


def test_detect_sequence_cycle_returns_none_for_independent_nodes():
    deps = {"a": [], "b": [], "c": []}
    assert detect_sequence_cycle(deps) is None


def test_detect_sequence_cycle_returns_none_for_multiple_independent_dependencies():
    deps = {"a": [], "b": [], "c": ["a", "b"]}
    assert detect_sequence_cycle(deps) is None


def test_detect_sequence_cycle_finds_a_direct_two_node_cycle():
    deps = {"a": ["b"], "b": ["a"]}
    cycle = detect_sequence_cycle(deps)
    assert cycle is not None
    assert set(cycle) == {"a", "b"}


def test_detect_sequence_cycle_finds_a_longer_cycle():
    deps = {"a": ["b"], "b": ["c"], "c": ["a"], "d": []}
    cycle = detect_sequence_cycle(deps)
    assert cycle is not None
    assert set(cycle) >= {"a", "b", "c"}


def test_operational_warnings_flag_a_sequence_cycle_without_crashing():
    """No hay forma de producir un ciclo real con geometria valida (Fase 6A,
    seccion 16) -se fuerza uno con un monkeypatch minimo para confirmar que
    compute_operational_warnings lo detecta y sigue devolviendo una lista
    utilizable en vez de trabarse."""
    import app.core.sequence as sequence_module

    a = _piece("a", x=0, y=0)
    b = _piece("b", x=200, y=0)

    original = sequence_module.compute_unload_dependencies
    try:
        sequence_module.compute_unload_dependencies = lambda *args, **kwargs: {"a": ["b"], "b": ["a"]}
        warnings = compute_operational_warnings([a, b], CONTAINER)
    finally:
        sequence_module.compute_unload_dependencies = original

    cycles = [w for w in warnings if w.type == OperationalWarningType.SEQUENCE_CYCLE]
    assert len(cycles) == 1
    assert "a" in cycles[0].message and "b" in cycles[0].message


def test_opening_configuration_limitation_absent_for_rear_opening():
    container = ContainerSpec(
        id="test-rear", name="Rear", length=5000, width=5000, height=3000, max_weight=1_000_000,
        loading_opening_type=LoadingOpeningType.REAR,
    )
    warnings = compute_operational_warnings([_piece("a", x=0, y=0)], container)
    assert all(w.type != OperationalWarningType.OPENING_CONFIGURATION_LIMITATION for w in warnings)


def test_opening_configuration_limitation_absent_for_the_default_container_fixture():
    """Fase 6A Final Product Decision: LoadSpaceSpec.loading_opening_type
    default paso de None a REAR -el CONTAINER de este archivo (que nunca
    pasa el campo explicitamente) ahora es REAR por default, no "unknown"."""
    assert CONTAINER.loading_opening_type == LoadingOpeningType.REAR
    warnings = compute_operational_warnings([_piece("a", x=0, y=0)], CONTAINER)
    assert all(w.type != OperationalWarningType.OPENING_CONFIGURATION_LIMITATION for w in warnings)


def test_opening_configuration_limitation_absent_when_opening_type_is_legacy_none():
    """None sigue siendo un valor valido (compatibilidad con planes
    persistidos de antes de la Fase 6A Final Product Decision) y se sigue
    tratando igual que REAR -sin warning- aunque ningun flujo actual lo
    construya asi."""
    container = ContainerSpec(
        id="test-legacy-none", name="Legacy", length=5000, width=5000, height=3000, max_weight=1_000_000,
        loading_opening_type=None,
    )
    warnings = compute_operational_warnings([_piece("a", x=0, y=0)], container)
    assert all(w.type != OperationalWarningType.OPENING_CONFIGURATION_LIMITATION for w in warnings)


def test_opening_configuration_limitation_present_for_side_opening():
    container = ContainerSpec(
        id="test-side", name="Side", length=5000, width=5000, height=3000, max_weight=1_000_000,
        loading_opening_type=LoadingOpeningType.SIDE,
    )
    warnings = compute_operational_warnings([_piece("a", x=0, y=0)], container)
    limitations = [w for w in warnings if w.type == OperationalWarningType.OPENING_CONFIGURATION_LIMITATION]
    assert len(limitations) == 1
    assert "side" in limitations[0].message.lower() or "Side" in limitations[0].message


def test_opening_configuration_limitation_present_for_multiple_openings():
    container = ContainerSpec(
        id="test-multi", name="Multi", length=5000, width=5000, height=3000, max_weight=1_000_000,
        loading_opening_type=LoadingOpeningType.MULTIPLE,
    )
    warnings = compute_operational_warnings([_piece("a", x=0, y=0)], container)
    assert any(w.type == OperationalWarningType.OPENING_CONFIGURATION_LIMITATION for w in warnings)


def test_compute_operational_warnings_still_includes_loadability_warnings():
    """compute_operational_warnings reutiliza detect_operational_warnings
    (no lo duplica) -confirma que la loadability warning sigue apareciendo,
    ahora envuelta en OperationalWarning con el tipo correcto."""
    anchor_piece = _piece("anchor", x=CONTAINER.length - 100, y=CONTAINER.width - 100, z=0)
    isolated = _piece("isolated", x=1000, y=1000, z=0)
    warnings = compute_operational_warnings([anchor_piece, isolated], CONTAINER)
    loadability = [w for w in warnings if w.type == OperationalWarningType.OPERATIONAL_LOADABILITY_WARNING]
    assert len(loadability) == 1
    assert loadability[0].item_id == "isolated"


def test_compute_operational_warnings_empty_for_empty_plan():
    assert compute_operational_warnings([], CONTAINER) == []


def test_scenario_f_manual_correction_removes_the_delivery_conflict_warning():
    """Seccion 42 (Scenario F): mover manualmente la pieza bloqueante hace que
    el warning desaparezca al recalcular -no hace falta logica especial, es
    una consecuencia directa de que todo se recalcula desde la geometria."""
    p002 = _piece("P002", x=0, y=0, delivery_sequence=3)
    p001 = _piece("P001", x=200, y=0, delivery_sequence=1)
    warnings_before = compute_operational_warnings([p002, p001], CONTAINER)
    assert any(w.type == OperationalWarningType.DELIVERY_SEQUENCE_CONFLICT for w in warnings_before)

    # "Mueve" P002 a un costado -ya no comparte columna Y-Z con P001.
    p002_moved = _piece("P002", x=0, y=2000, delivery_sequence=3)
    warnings_after = compute_operational_warnings([p002_moved, p001], CONTAINER)
    assert all(w.type != OperationalWarningType.DELIVERY_SEQUENCE_CONFLICT for w in warnings_after)


def test_performance_operational_warnings_on_a_200_item_floor_grid():
    """Seccion 48: no busca un numero magico -solo confirma que un plan
    representativo (200 piezas en grilla, con dependencias de bloqueo reales
    entre filas) no explota en tiempo/memoria con el enfoque O(n^2)."""
    import time

    pieces = [_piece(f"p{i}", x=(i % 20) * 200, y=(i // 20) * 200) for i in range(200)]
    start = time.perf_counter()
    compute_operational_warnings(pieces, CONTAINER)
    elapsed = time.perf_counter() - start
    assert elapsed < 5.0, f"compute_operational_warnings tardo {elapsed:.2f}s para 200 piezas"
