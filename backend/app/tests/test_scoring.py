"""Scoring: la prioridad y la agrupacion deben mover el score en la direccion
esperada (secciones 5-7 de V2)."""

from app.core.packer import compute_metrics
from app.core.scoring import score_breakdown, score_solution
from app.models.schemas import ContainerSpec, OptimizationMode, PackingResult, PlacedPiece, UnloadedItem, WeightBalanceMode

CONTAINER = ContainerSpec(id="test", name="Test", length=2000, width=200, height=200, max_weight=10000)


def _piece(piece_id, priority, x, group="", system="", y=0):
    return PlacedPiece(
        id=piece_id,
        code=piece_id,
        system=system,
        group=group,
        weight=10,
        stackable=True,
        priority=priority,
        x=x,
        y=y,
        z=0,
        dx=100,
        dy=100,
        dz=100,
        orientation_label="P1-a",
        source_width=100,
        source_height=100,
        source_thickness=50,
    )


def _unloaded(piece_id, priority):
    return UnloadedItem(
        id=piece_id,
        code=piece_id,
        width=100,
        height=100,
        thickness=50,
        weight=10,
        priority=priority,
        reason="test",
        reason_code="NO_VALID_SPACE",
    )


def _result(placed, unloaded):
    metrics = compute_metrics(CONTAINER, placed, unloaded)
    return PackingResult(container=CONTAINER, placed=placed, unloaded=unloaded, metrics=metrics)


def test_priority_score_prefers_solution_that_keeps_priority_1_pieces():
    """Ejemplo de la seccion 6: cargar 95 piezas dejando fuera Priority 1
    puede puntuar peor que cargar 92 que si incluyen todas las Priority 1.
    Aqui aislamos exactamente esa variable: mismas 8 posiciones fisicas
    ocupadas en ambos casos, solo cambia QUE prioridad tiene cada una."""
    positions = [i * 100 for i in range(8)]

    # Solucion A: deja fuera las 2 piezas Priority 1 (mayor importancia).
    solution_a = _result(
        placed=[_piece(f"p{i}", priority=5, x=x) for i, x in enumerate(positions)],
        unloaded=[_unloaded("prio1-a", priority=1), _unloaded("prio1-b", priority=1)],
    )

    # Solucion B: mismas posiciones fisicas, pero incluye las 2 Priority 1
    # (deja fuera 2 de baja prioridad en su lugar).
    solution_b = _result(
        placed=[_piece(f"p{i}", priority=(1 if i < 2 else 5), x=x) for i, x in enumerate(positions)],
        unloaded=[_unloaded("prio5-a", priority=5), _unloaded("prio5-b", priority=5)],
    )

    # Mismo numero de piezas cargadas, mismo volumen/piso ocupado -> el unico
    # componente del score que puede diferir es priorityScore.
    assert solution_a.metrics.loaded_pieces == solution_b.metrics.loaded_pieces
    assert solution_a.metrics.used_volume_pct == solution_b.metrics.used_volume_pct

    score_a = score_solution(solution_a, OptimizationMode.BEST_SPACE)
    score_b = score_solution(solution_b, OptimizationMode.BEST_SPACE)
    assert score_b > score_a


def test_grouping_score_prefers_clustered_layout_in_keep_groups_mode():
    # Layout disperso: piezas del mismo grupo repartidas por todo el contenedor.
    scattered = _result(
        placed=[
            _piece("a1", priority=3, x=0, group="G1"),
            _piece("a2", priority=3, x=1900, group="G1"),
            _piece("b1", priority=3, x=900, group="G2"),
            _piece("b2", priority=3, x=1000, group="G2"),
        ],
        unloaded=[],
    )
    # Layout agrupado: piezas del mismo grupo juntas.
    clustered = _result(
        placed=[
            _piece("a1", priority=3, x=0, group="G1"),
            _piece("a2", priority=3, x=100, group="G1"),
            _piece("b1", priority=3, x=900, group="G2"),
            _piece("b2", priority=3, x=1000, group="G2"),
        ],
        unloaded=[],
    )

    score_scattered = score_solution(scattered, OptimizationMode.KEEP_GROUPS)
    score_clustered = score_solution(clustered, OptimizationMode.KEEP_GROUPS)
    assert score_clustered > score_scattered


def test_weight_balance_prefers_evenly_distributed_load():
    """Toda la carga de un lado tiende a volcar el contenedor: una solucion
    balanceada izquierda/derecha debe puntuar mejor que una lopsided."""
    lopsided = _result(
        placed=[
            _piece("a1", priority=3, x=0),
            _piece("a2", priority=3, x=100),
            _piece("a3", priority=3, x=200),
            _piece("a4", priority=3, x=300),
        ],
        unloaded=[],
    )
    # Mismo peso total, pero repartido a ambos lados del centro (y=0 vs y=100
    # dentro de un contenedor de width=200 -> centro en y=100).
    balanced = _result(
        placed=[
            _piece("a1", priority=3, x=0, y=0),
            _piece("a2", priority=3, x=100, y=0),
            _piece("a3", priority=3, x=200, y=100),
            _piece("a4", priority=3, x=300, y=100),
        ],
        unloaded=[],
    )

    assert balanced.metrics.weight_balance_pct > lopsided.metrics.weight_balance_pct

    score_lopsided = score_solution(lopsided, OptimizationMode.BEST_SPACE)
    score_balanced = score_solution(balanced, OptimizationMode.BEST_SPACE)
    assert score_balanced > score_lopsided


def test_best_space_mode_weighs_volume_more_than_grouping_mode_does():
    from app.core.scoring import _BASE_WEIGHTS, _GROUPING_WEIGHTS

    assert _BASE_WEIGHTS["volume"] + _BASE_WEIGHTS["floor"] > _GROUPING_WEIGHTS["volume"] + _GROUPING_WEIGHTS["floor"]
    assert _GROUPING_WEIGHTS["grouping"] > _BASE_WEIGHTS["grouping"]


def _lopsided_and_balanced():
    lopsided = _result(
        placed=[_piece(f"a{i}", priority=3, x=i * 100) for i in range(4)],
        unloaded=[],
    )
    balanced = _result(
        placed=[
            _piece("a1", priority=3, x=0, y=0),
            _piece("a2", priority=3, x=100, y=0),
            _piece("a3", priority=3, x=200, y=100),
            _piece("a4", priority=3, x=300, y=100),
        ],
        unloaded=[],
    )
    return lopsided, balanced


def test_weight_balance_important_widens_the_score_gap():
    """Con Weight Balance = Important, la diferencia de score entre una
    solucion balanceada y una lopsided debe ser mayor que con Normal; con
    Ignore, el balance no debe influir en absoluto en el score."""
    lopsided, balanced = _lopsided_and_balanced()

    gap_normal = score_solution(balanced, OptimizationMode.BEST_SPACE, WeightBalanceMode.NORMAL) - score_solution(
        lopsided, OptimizationMode.BEST_SPACE, WeightBalanceMode.NORMAL
    )
    gap_important = score_solution(balanced, OptimizationMode.BEST_SPACE, WeightBalanceMode.IMPORTANT) - score_solution(
        lopsided, OptimizationMode.BEST_SPACE, WeightBalanceMode.IMPORTANT
    )
    assert gap_important > gap_normal


def test_weight_balance_ignore_sets_balance_weight_to_zero():
    from app.core.scoring import _weights_for

    weights = _weights_for(OptimizationMode.BEST_SPACE, WeightBalanceMode.IGNORE)
    assert weights["balance"] == 0.0
    # el resto se renormaliza para seguir sumando 1.0 (nada de peso se pierde)
    assert abs(sum(weights.values()) - 1.0) < 1e-9


def test_score_breakdown_has_one_entry_per_component_as_percentage():
    lopsided, _ = _lopsided_and_balanced()
    breakdown = score_breakdown(lopsided, OptimizationMode.BEST_SPACE)
    assert set(breakdown.keys()) == {"loaded", "priority", "volume", "floor", "grouping", "accessibility", "balance"}
    for value in breakdown.values():
        assert 0 <= value <= 100
