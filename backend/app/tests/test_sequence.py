"""Orden de carga/descarga (secciones 17-21 de V2, Anchored Loading Sequence
post-V4): fondo antes que frente, abajo antes que arriba; la carga ademas
arranca de una esquina fija y crece por modulos adyacentes."""

import pytest

from app.core.sequence import (
    ANCHOR_TOLERANCE_MM,
    MAX_STEP_SIZE_AUTO,
    chunk_sequence,
    compute_load_sequence,
    compute_load_steps,
    compute_unload_sequence,
    compute_unload_steps,
    detect_operational_warnings,
)
from app.models.schemas import ContainerSpec, LoadingAnchor, PlacedPiece

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
    separados entre si por mas que ANCHOR_TOLERANCE_MM, no deben mezclarse en
    el mismo Step solo porque coincida un contador."""
    module1 = [_piece(f"m1_{i}", x=CONTAINER.length - 100, y=CONTAINER.width - 100 - i * 100, z=0) for i in range(3)]
    gap = ANCHOR_TOLERANCE_MM + 500
    module2_x = CONTAINER.length - 100 - 100 - gap
    module2 = [_piece(f"m2_{i}", x=module2_x, y=CONTAINER.width - 100 - i * 100, z=0) for i in range(3)]

    steps = compute_load_steps(module1 + module2, CONTAINER)
    module1_ids = {p.id for p in module1}
    module2_ids = {p.id for p in module2}
    for step in steps:
        step_set = set(step)
        assert not (step_set & module1_ids and step_set & module2_ids), f"un step mezclo los 2 modulos separados: {step}"


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
