"""Fase 6A.1: Delivery-Aware Optimization Mode (Prioritize Delivery Sequence).

Confirma que el nuevo OptimizationMode.PRIORITIZE_DELIVERY realmente intenta
colocar Delivery Sequence mas bajo (entrega mas temprana) cerca de la
apertura de carga (x chico, ver core/strategies.py:_delivery_key), que los
otros modos quedan sin cambios (delivery_key neutral), y que el "poor UX"
original -el Sequence Engine de Fase 6A reportando decenas de
DELIVERY_SEQUENCE_CONFLICT bajo un modo que nunca intento respetar Delivery
Sequence- mejora al elegir este modo. NO redisena el Sequence Engine en si
(ver test_sequence.py para esa logica)."""

from app.core.optimize import run_optimization
from app.core.packer import compute_metrics, pack_container
from app.core.scoring import score_breakdown, score_solution
from app.core.sequence import compute_operational_warnings
from app.models.containers import get_container
from app.models.schemas import (
    ContainerSpec,
    OperationalWarningType,
    OptimizationMode,
    PackingResult,
    PlacedPiece,
    WindowItem,
)

# Cubo casi del tamano completo de la seccion transversal del 40ft standard
# (2352 x 2393 interno) a proposito: garantiza UN item por "seccion
# transversal" (ver comentario de packer.pack_container sobre llenar x antes
# que y/z), asi que la posicion X final queda determinada pura y
# exclusivamente por el ORDEN DE PROCESAMIENTO -sin esto, varios cubos
# chicos podrian compartir la misma profundidad X y el test dejaria de ser
# determinista.
_CUBE = 2300


def _item(code, delivery_sequence=None, quantity=1):
    return WindowItem(
        code=code, width=_CUBE, height=_CUBE, thickness=_CUBE, weight=10, quantity=quantity,
        system="", group="", stackable=True, priority=1, delivery_sequence=delivery_sequence,
        item_type="box",
    )


CONTAINER = get_container("40ft_standard")


# ---------------------------------------------------------------------------
# core/strategies.py: _delivery_key via pack_container (end-to-end, no
# implementation detail exposed directly -mismo criterio que test_strategies.py).
# ---------------------------------------------------------------------------


def test_prioritize_delivery_places_lower_delivery_sequence_closer_to_the_door():
    items = [_item("late", delivery_sequence=3), _item("mid", delivery_sequence=2), _item("early", delivery_sequence=1)]
    result = pack_container(items, CONTAINER, OptimizationMode.PRIORITIZE_DELIVERY, [], 0.0, "highest_priority")
    by_code = {p.code: p for p in result.placed}
    assert by_code["early"].x <= by_code["mid"].x <= by_code["late"].x


def test_prioritize_delivery_items_without_delivery_sequence_go_deep():
    """Items sin Delivery Sequence no le roban la posicion cercana a la
    puerta a items que SI tienen un destino conocido."""
    items = [_item("unknown", delivery_sequence=None), _item("early", delivery_sequence=1)]
    result = pack_container(items, CONTAINER, OptimizationMode.PRIORITIZE_DELIVERY, [], 0.0, "highest_priority")
    by_code = {p.code: p for p in result.placed}
    assert by_code["early"].x <= by_code["unknown"].x


def test_prioritize_delivery_allows_many_items_sharing_the_same_delivery_sequence():
    """Seccion 2 del pedido: Delivery Sequence es un grupo/parada de entrega,
    NO un orden unico por item -varios items con el mismo valor es el caso
    NORMAL, no debe romper nada ni producir un orden invalido."""
    items = [_item(f"a{i}", delivery_sequence=1) for i in range(2)] + [
        _item(f"b{i}", delivery_sequence=2) for i in range(2)
    ]
    result = pack_container(items, CONTAINER, OptimizationMode.PRIORITIZE_DELIVERY, [], 0.0, "highest_priority")
    assert len(result.placed) == 4
    by_code = {p.code: p for p in result.placed}
    # Ambos grupos quedan (como grupo) mas cerca/mas lejos de la puerta segun
    # corresponda; NO se exige un orden estricto DENTRO de un mismo grupo.
    max_a = max(by_code[f"a{i}"].x for i in range(2))
    min_b = min(by_code[f"b{i}"].x for i in range(2))
    assert max_a <= min_b


def test_other_modes_are_unaffected_by_delivery_sequence_values():
    """delivery_key es neutral (0.0) fuera de PRIORITIZE_DELIVERY -agregar
    Delivery Sequence a los items no debe cambiar el orden de colocacion
    bajo Best Space Utilization (ni ningun otro modo existente)."""
    with_delivery = [_item("a", delivery_sequence=3), _item("b", delivery_sequence=1), _item("c", delivery_sequence=2)]
    without_delivery = [_item("a"), _item("b"), _item("c")]

    result_with = pack_container(with_delivery, CONTAINER, OptimizationMode.BEST_SPACE, [], 0.0, "highest_priority")
    result_without = pack_container(without_delivery, CONTAINER, OptimizationMode.BEST_SPACE, [], 0.0, "highest_priority")

    positions_with = {p.code: (p.x, p.y, p.z) for p in result_with.placed}
    positions_without = {p.code: (p.x, p.y, p.z) for p in result_without.placed}
    assert positions_with == positions_without


def test_run_optimization_with_prioritize_delivery_mode_runs():
    """Mismo smoke test que ya existe para KEEP_GROUPS en test_strategies.py,
    ahora para el modo nuevo -corre las 7 estrategias + scoring sin romper."""
    items = [_item(f"p{i}", delivery_sequence=(i % 3) + 1) for i in range(6)]
    best, alternatives = run_optimization(items, CONTAINER, OptimizationMode.PRIORITIZE_DELIVERY, [], 0.0)
    assert len(best.placed) > 0
    assert len(alternatives) >= 1


# ---------------------------------------------------------------------------
# Criterio de aceptacion del pedido: el modo realmente reduce los
# DELIVERY_SEQUENCE_CONFLICT que el Sequence Engine (Fase 6A) reportaria de
# otro modo -sin tocar el Sequence Engine en si.
# ---------------------------------------------------------------------------


def test_prioritize_delivery_mode_eliminates_delivery_sequence_conflicts_versus_best_space():
    # Orden de Delivery Sequence deliberadamente NO correlacionado con el
    # orden de la lista -bajo Best Space (delivery_key neutral, procesamiento
    # estable en orden de lista) esto genera varios pares mal ordenados.
    scrambled = [3, 1, 2, 1, 3, 2]
    items = [_item(f"p{i}", delivery_sequence=d) for i, d in enumerate(scrambled)]

    best_space = pack_container(items, CONTAINER, OptimizationMode.BEST_SPACE, [], 0.0, "highest_priority")
    prioritize_delivery = pack_container(items, CONTAINER, OptimizationMode.PRIORITIZE_DELIVERY, [], 0.0, "highest_priority")

    def _conflicts(result):
        warnings = compute_operational_warnings(result.placed, CONTAINER)
        return [w for w in warnings if w.type == OperationalWarningType.DELIVERY_SEQUENCE_CONFLICT]

    best_space_conflicts = _conflicts(best_space)
    prioritize_delivery_conflicts = _conflicts(prioritize_delivery)

    assert len(best_space_conflicts) > 0, (
        "el escenario deberia generar conflictos bajo Best Space (orden de colocacion no correlacionado con Delivery Sequence)"
    )
    assert len(prioritize_delivery_conflicts) == 0, (
        "Prioritize Delivery Sequence deberia eliminar los conflictos en una cadena simple de una sola fila"
    )


# ---------------------------------------------------------------------------
# core/scoring.py: _delivery_score / _DELIVERY_WEIGHTS -unit level, mismo
# patron que test_scoring.py.
# ---------------------------------------------------------------------------

SCORE_CONTAINER = ContainerSpec(id="test", name="Test", length=2000, width=200, height=200, max_weight=10000)


def _piece(piece_id, x, delivery_sequence=None, z=0):
    return PlacedPiece(
        id=piece_id, code=piece_id, weight=10, stackable=True, priority=1, delivery_sequence=delivery_sequence,
        x=x, y=0, z=z, dx=100, dy=100, dz=100,
        orientation_label="P1-a", source_width=100, source_height=100, source_thickness=50,
    )


def _score_result(placed):
    metrics = compute_metrics(SCORE_CONTAINER, placed, [])
    return PackingResult(container=SCORE_CONTAINER, placed=placed, unloaded=[], metrics=metrics)


def test_delivery_score_is_perfect_for_well_ordered_placement():
    placed = [_piece("a", x=0, delivery_sequence=1), _piece("b", x=200, delivery_sequence=2)]
    breakdown = score_breakdown(_score_result(placed), OptimizationMode.PRIORITIZE_DELIVERY)
    assert breakdown["delivery"] == 100.0


def test_delivery_score_penalizes_a_reversed_placement():
    placed = [_piece("a", x=200, delivery_sequence=1), _piece("b", x=0, delivery_sequence=2)]
    breakdown = score_breakdown(_score_result(placed), OptimizationMode.PRIORITIZE_DELIVERY)
    assert breakdown["delivery"] == 0.0


def test_delivery_score_is_neutral_when_no_items_have_delivery_sequence():
    placed = [_piece("a", x=0), _piece("b", x=200)]
    breakdown = score_breakdown(_score_result(placed), OptimizationMode.PRIORITIZE_DELIVERY)
    assert breakdown["delivery"] == 100.0


def test_delivery_score_penalizes_a_lower_delivery_sequence_trapped_under_a_higher_one():
    """Fase 6A.1 FINAL, seccion 11 del pedido: auditoria confirmo que
    _delivery_score (y el sort key de placement) eran ciegos a Z/apilado
    -esta prueba confirma la correccion: un par de SOPORTE directo (base
    apoya al de arriba) cuenta como mal ordenado si la base (que sale
    despues, seccion 13 del pedido) tiene Delivery Sequence MAS BAJO
    (queria salir antes) que lo que la atrapa encima."""
    base = _piece("base", x=0, z=0, delivery_sequence=1)
    top = _piece("top", x=0, z=100, delivery_sequence=3)  # mismo x/y -> se apoya en base
    breakdown = score_breakdown(_score_result([base, top]), OptimizationMode.PRIORITIZE_DELIVERY)
    assert breakdown["delivery"] < 100.0


def test_delivery_score_does_not_penalize_a_stacking_order_that_matches_delivery_sequence():
    base = _piece("base", x=0, z=0, delivery_sequence=3)
    top = _piece("top", x=0, z=100, delivery_sequence=1)  # base "sale despues" y tiene el numero mas alto: correcto
    breakdown = score_breakdown(_score_result([base, top]), OptimizationMode.PRIORITIZE_DELIVERY)
    assert breakdown["delivery"] == 100.0


def test_delivery_score_ignores_non_touching_pieces_at_different_heights():
    """2 piezas a distinta Z pero SIN contacto real (separadas, no se
    apoyan una en la otra) no deben generar un par de soporte -solo importa
    el contacto fisico directo, igual criterio que _direct_supporters."""
    far_below = _piece("far_below", x=0, z=0, delivery_sequence=1)
    floating_above = _piece("floating_above", x=0, z=500, delivery_sequence=3)  # lejos, no la toca
    breakdown = score_breakdown(_score_result([far_below, floating_above]), OptimizationMode.PRIORITIZE_DELIVERY)
    assert breakdown["delivery"] == 100.0


def test_delivery_score_appears_in_breakdown_for_every_mode_but_only_weighs_for_prioritize_delivery():
    placed = [_piece("a", x=200, delivery_sequence=1), _piece("b", x=0, delivery_sequence=2)]  # mal ordenado
    result = _score_result(placed)

    for mode in OptimizationMode:
        breakdown = score_breakdown(result, mode)
        assert "delivery" in breakdown

    # Bajo BEST_SPACE, el delivery score bajo NO debe afectar el score final
    # (peso 0 -no esta en _BASE_WEIGHTS).
    best_space_score = score_solution(result, OptimizationMode.BEST_SPACE)
    well_ordered = _score_result([_piece("a", x=0, delivery_sequence=1), _piece("b", x=200, delivery_sequence=2)])
    best_space_score_well_ordered = score_solution(well_ordered, OptimizationMode.BEST_SPACE)
    assert best_space_score == best_space_score_well_ordered


def test_prioritize_delivery_weights_reward_a_well_ordered_solution_over_a_reversed_one():
    reversed_result = _score_result([_piece("a", x=200, delivery_sequence=1), _piece("b", x=0, delivery_sequence=2)])
    well_ordered_result = _score_result([_piece("a", x=0, delivery_sequence=1), _piece("b", x=200, delivery_sequence=2)])

    reversed_score = score_solution(reversed_result, OptimizationMode.PRIORITIZE_DELIVERY)
    well_ordered_score = score_solution(well_ordered_result, OptimizationMode.PRIORITIZE_DELIVERY)

    assert well_ordered_score > reversed_score
