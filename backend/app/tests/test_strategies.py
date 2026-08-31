"""Multiples estrategias de cubicaje (secciones 3, 26-28 de V2)."""

from app.core.geometry import Box, boxes_overlap
from app.core.optimize import run_optimization
from app.core.orientation import is_valid_orientation
from app.core.packer import pack_container
from app.core.strategies import STRATEGIES
from app.models.containers import get_container
from app.models.schemas import OptimizationMode, WindowItem


def _mixed_items():
    return [
        WindowItem(
            code="A", width=1200, height=2000, thickness=100, weight=45, quantity=15,
            system="Corrediza", group="Piso1", stackable=True, priority=1,
        ),
        WindowItem(
            code="B", width=900, height=1500, thickness=80, weight=28, quantity=15,
            system="Fija", group="Piso1", stackable=True, priority=3,
        ),
        WindowItem(
            code="C", width=700, height=1000, thickness=60, weight=15, quantity=20,
            system="Proyectante", group="Piso2", stackable=True, priority=5,
        ),
    ]


def test_every_strategy_produces_a_valid_solution():
    container = get_container("40ft_standard")
    items = _mixed_items()

    for strategy in STRATEGIES:
        result = pack_container(items, container, OptimizationMode.BEST_SPACE, [], 0.0, strategy)
        assert len(result.placed) > 0, f"Estrategia {strategy} no coloco ninguna pieza"

        for piece in result.placed:
            assert is_valid_orientation(
                piece.source_width, piece.source_height, piece.source_thickness, piece.dx, piece.dy, piece.dz
            ), f"Estrategia {strategy} produjo una orientacion invalida"

        boxes = [Box(p.id, p.x, p.y, p.z, p.dx, p.dy, p.dz, p.stackable) for p in result.placed]
        for i, a in enumerate(boxes):
            for b in boxes[i + 1 :]:
                assert not boxes_overlap(a, b), f"Estrategia {strategy} produjo una colision"


def test_run_optimization_picks_the_best_scoring_candidate():
    container = get_container("40ft_standard")
    items = _mixed_items()

    best, alternatives = run_optimization(items, container, OptimizationMode.BEST_SPACE, [], 0.0)

    assert len(alternatives) >= 1
    assert alternatives[0].result == best
    scores = [a.score for a in alternatives]
    assert scores == sorted(scores, reverse=True)


def test_run_optimization_returns_up_to_three_alternatives():
    container = get_container("40ft_standard")
    items = _mixed_items()
    _, alternatives = run_optimization(items, container, OptimizationMode.BEST_SPACE, [], 0.0)
    assert 1 <= len(alternatives) <= 3


def test_run_optimization_with_keep_groups_mode_runs():
    container = get_container("40ft_standard")
    items = _mixed_items()
    best, alternatives = run_optimization(items, container, OptimizationMode.KEEP_GROUPS, [], 0.0)
    assert len(best.placed) > 0
    assert len(alternatives) >= 1
