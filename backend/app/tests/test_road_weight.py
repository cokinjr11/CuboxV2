"""
CUBOX 2.0 - Fase 2B: distribucion de peso longitudinal para vehiculos de
carretera (Truck/Trailer). Cubre los Tests A-L especificados para esta fase.

Convencion de coordenadas (ver core/road_weight.py): x=0 es la puerta del
LoadSpace, x=length es la pared del fondo -la misma que usa PlacedPiece.x en
todo el resto de CUBOX. En los tests, los items usan ItemType.BOX +
OrientationPolicy.FREE (cubos de dimensiones iguales) para que la
orientacion nunca sea una variable -el foco de este archivo es la fisica de
distribucion de peso, no las reglas de orientacion (ya cubiertas en
test_item_generalization.py)."""

import pytest
from pydantic import ValidationError

from app.core.final_validation import validate_for_export
from app.core.manual_move import validate_move
from app.core.packer import compute_metrics, pack_container
from app.core.road_weight import (
    WeightPoint,
    compute_longitudinal_moment,
    compute_support_reactions,
    compute_total_weight,
    evaluate_road_weight,
    weight_point_from_placed,
)
from app.models.schemas import (
    ItemType,
    LoadSpaceSpec,
    LoadSpaceType,
    OrientationPolicy,
    PackingResult,
    PlacedPiece,
    RoadSupport,
    RoadWeightConfig,
    WindowItem,
)


def _support(support_id, x, max_load=100000, baseline=0.0):
    return RoadSupport(id=support_id, name=f"Support {support_id}", position_x_mm=x, max_load_kg=max_load, baseline_load_kg=baseline)


def _box_item(width=1000, height=1000, thickness=1000, weight=300, quantity=1, code="ITEM"):
    """Cubo de dimensiones iguales con OrientationPolicy.FREE: la orientacion
    nunca varia (todas las permutaciones dan el mismo dx/dy/dz), asi que la
    geometria del test depende solo de x/weight."""
    return WindowItem(
        code=code, width=width, height=height, thickness=thickness, weight=weight, quantity=quantity,
        item_type=ItemType.BOX, orientation_policy=OrientationPolicy.FREE,
    )


def _placed_box(piece_id, x, weight, dx=1000, dy=1000, dz=1000, y=0, z=0, locked=False, stackable=True):
    return PlacedPiece(
        id=piece_id, code=piece_id, weight=weight, stackable=stackable, priority=1,
        x=x, y=y, z=z, dx=dx, dy=dy, dz=dz,
        orientation_label="FREE", source_width=dx, source_height=dy, source_thickness=dz,
        item_type=ItemType.BOX, orientation_policy=OrientationPolicy.FREE, locked=locked,
    )


def _load_space(supports, length=6000, width=2400, height=2500, max_weight=100000, load_space_type=LoadSpaceType.TRUCK):
    return LoadSpaceSpec(
        id="road-test", name="Road Test Vehicle", load_space_type=load_space_type,
        length=length, width=width, height=height, max_weight=max_weight,
        road_weight_config=RoadWeightConfig(enabled=True, supports=supports),
    )


def _result(placed: list[PlacedPiece], container: LoadSpaceSpec) -> PackingResult:
    metrics = compute_metrics(container, placed, [])
    return PackingResult(container=container, placed=placed, unloaded=[], metrics=metrics)


# ---------------------------------------------------------------------------
# TEST A - carga centrada exactamente entre 2 supports -> 50/50.
# ---------------------------------------------------------------------------


def test_centered_load_splits_evenly_between_supports():
    support_a, support_b = _support("A", 1000), _support("B", 5000)
    points = [WeightPoint(weight=1000, x=2500, dx=1000)]  # center_x=3000, punto medio de A/B

    reaction_a, reaction_b = compute_support_reactions(
        compute_total_weight(points), compute_longitudinal_moment(points), support_a, support_b
    )

    assert reaction_a == pytest.approx(500)
    assert reaction_b == pytest.approx(500)


# ---------------------------------------------------------------------------
# TEST B - carga mas cerca de Support A -> reaction_a sube, reaction_b baja.
# ---------------------------------------------------------------------------


def test_load_near_support_a_increases_its_reaction():
    support_a, support_b = _support("A", 1000), _support("B", 5000)
    points = [WeightPoint(weight=1000, x=1000, dx=200)]  # center_x=1100, cerca de A

    reaction_a, reaction_b = compute_support_reactions(
        compute_total_weight(points), compute_longitudinal_moment(points), support_a, support_b
    )

    assert reaction_a > 500
    assert reaction_b < 500
    assert reaction_a > reaction_b


# ---------------------------------------------------------------------------
# TEST C - inverso: carga mas cerca de Support B.
# ---------------------------------------------------------------------------


def test_load_near_support_b_increases_its_reaction():
    support_a, support_b = _support("A", 1000), _support("B", 5000)
    points = [WeightPoint(weight=1000, x=4800, dx=200)]  # center_x=4900, cerca de B

    reaction_a, reaction_b = compute_support_reactions(
        compute_total_weight(points), compute_longitudinal_moment(points), support_a, support_b
    )

    assert reaction_b > 500
    assert reaction_a < 500
    assert reaction_b > reaction_a


# ---------------------------------------------------------------------------
# TEST D - multiples items: Sum(weight) y Sum(moment) producen la reaccion
# esperada.
# ---------------------------------------------------------------------------


def test_multiple_items_use_sum_of_weights_and_moments():
    support_a, support_b = _support("A", 0), _support("B", 4000)
    points = [
        WeightPoint(weight=300, x=0, dx=1000),  # center_x=500
        WeightPoint(weight=700, x=3000, dx=1000),  # center_x=3500
    ]

    total_weight = compute_total_weight(points)
    total_moment = compute_longitudinal_moment(points)
    assert total_weight == pytest.approx(1000)
    assert total_moment == pytest.approx(300 * 500 + 700 * 3500)

    reaction_a, reaction_b = compute_support_reactions(total_weight, total_moment, support_a, support_b)
    expected_reaction_b = total_moment / 4000  # xA=0 simplifica la formula
    assert reaction_b == pytest.approx(expected_reaction_b)
    assert reaction_a == pytest.approx(total_weight - expected_reaction_b)


# ---------------------------------------------------------------------------
# TEST E - baseline_load_kg se suma a la reaccion de carga.
# ---------------------------------------------------------------------------


def test_baseline_load_adds_to_cargo_reaction():
    config = RoadWeightConfig(enabled=True, supports=[_support("A", 0, baseline=500), _support("B", 4000, baseline=800)])
    points = [WeightPoint(weight=1000, x=1500, dx=1000)]  # center_x=2000

    metrics = evaluate_road_weight(config, points)

    support_a, support_b = metrics.supports
    assert support_a.baseline_load_kg == 500
    assert support_b.baseline_load_kg == 800
    assert support_a.total_load_kg == pytest.approx(support_a.baseline_load_kg + support_a.cargo_reaction_kg)
    assert support_b.total_load_kg == pytest.approx(support_b.baseline_load_kg + support_b.cargo_reaction_kg)


# ---------------------------------------------------------------------------
# TEST F - un support excede su limite aunque el payload total sea valido.
# ---------------------------------------------------------------------------


def test_support_overload_fails_even_when_total_payload_is_valid():
    container = _load_space([_support("A", 200), _support("B", 5800, max_load=250)], max_weight=50000)
    piece = _placed_box("p1", x=5000, weight=300)  # center_x=5500, muy cerca de Support B

    result = _result([piece], container)
    errors = validate_for_export(result, container)

    assert any("Support B" in e for e in errors)
    assert sum(p.weight for p in result.placed) <= container.max_weight  # payload total: PASS


# ---------------------------------------------------------------------------
# TEST G - el payload total excede el maximo aunque los supports esten bien.
# ---------------------------------------------------------------------------


def test_total_payload_overload_fails_even_when_supports_are_valid():
    container = _load_space([_support("A", 0), _support("B", 6000)], max_weight=1000)
    piece = _placed_box("p1", x=2500, weight=1500)  # > max_weight=1000, pero lejos de sobrecargar cualquier support

    result = _result([piece], container)
    errors = validate_for_export(result, container)

    assert any("Peso total" in e for e in errors)
    road_weight = evaluate_road_weight(container.road_weight_config, [weight_point_from_placed(piece)])
    assert road_weight.valid is True  # supports: PASS


# ---------------------------------------------------------------------------
# TEST H - el packer rechaza un candidato que sobrecargaria un support y
# prueba otro candidato geometricamente valido.
# ---------------------------------------------------------------------------


def test_packer_rejects_candidate_that_would_overload_a_support():
    container = _load_space([_support("A", 200), _support("B", 5800, max_load=250)])

    # Pieza Locked (Optimize Remaining) que ocupa una franja cerca de la
    # mitad del contenedor -su presencia genera un candidato geometrico
    # adicional, mas alla del que el packer probaria primero (cerca del
    # fondo, junto a Support B).
    anchor = _placed_box("anchor", x=3000, weight=1, dx=500, dy=2400, dz=100, locked=True)

    item = _box_item(weight=300)
    result = pack_container([item], container, strategy="highest_priority", preplaced=[anchor])

    assert len(result.placed) == 2
    new_piece = next(p for p in result.placed if p.id != "anchor")

    # El candidato "natural" (cerca del fondo, x=5000) sobrecargaria Support
    # B -el packer debe haberlo descartado y colocado la pieza en otro lado.
    assert new_piece.x == pytest.approx(2000)
    assert new_piece.x != pytest.approx(5000)

    road_weight = evaluate_road_weight(container.road_weight_config, (weight_point_from_placed(p) for p in result.placed))
    assert road_weight.valid is True


# ---------------------------------------------------------------------------
# TEST I - un manual move que sobrecargaria un support es rechazado.
# ---------------------------------------------------------------------------


def test_manual_move_rejected_when_it_would_overload_a_support():
    container = _load_space([_support("A", 200), _support("B", 5800, max_load=250)])
    piece = _placed_box("p1", x=2000, weight=300)  # posicion valida (ver Test H)

    valid, reason = validate_move(
        piece, new_x=5000, new_y=0, new_z=0, new_dx=1000, new_dy=1000, new_dz=1000, other_pieces=[piece], container=container
    )

    assert valid is False
    assert "Support B" in reason


# ---------------------------------------------------------------------------
# TEST J - las piezas Locked contribuyen su peso/momento durante Optimize
# Remaining (simulado aca via pack_container(..., preplaced=[locked])).
# ---------------------------------------------------------------------------


def test_locked_piece_weight_and_moment_counted_during_optimize_remaining():
    container = _load_space([_support("A", 200), _support("B", 5800, max_load=250)])
    locked = _placed_box("locked", x=5000, weight=200, locked=True)
    item = _box_item(weight=100)

    # Solo (sin el locked): el item entra cerca del fondo sin problema.
    alone = pack_container([item], container, strategy="highest_priority")
    assert len(alone.placed) == 1
    assert len(alone.unloaded) == 0

    # Con el locked ya presente, su aporte a Support B debe contarse: ya no
    # queda ninguna posicion cerca del fondo que no sobrecargue Support B.
    with_locked = pack_container([item], container, strategy="highest_priority", preplaced=[locked])
    assert len(with_locked.unloaded) == 1
    assert with_locked.unloaded[0].reason_code == "NO_VALID_SPACE"


# ---------------------------------------------------------------------------
# TEST K - sin RoadWeightConfig, el comportamiento es identico a Fase 2A
# para Container/Truck/Trailer.
# ---------------------------------------------------------------------------


def test_no_road_config_behaves_exactly_like_phase_2a():
    for load_space_type in (LoadSpaceType.CONTAINER, LoadSpaceType.TRUCK, LoadSpaceType.TRAILER):
        space = LoadSpaceSpec(
            id=f"space-{load_space_type.value}", name="space", load_space_type=load_space_type,
            length=6000, width=2400, height=2500, max_weight=50000,
        )
        assert space.road_weight_config is None

        result = pack_container([_box_item(quantity=3)], space, strategy="highest_priority")
        assert len(result.placed) == 3
        assert evaluate_road_weight(space.road_weight_config, (weight_point_from_placed(p) for p in result.placed)) is None
        assert validate_for_export(result, space) == []


# ---------------------------------------------------------------------------
# TEST L - configuraciones de support invalidas devuelven un error claro.
# ---------------------------------------------------------------------------


def test_invalid_support_config_same_position_rejected():
    with pytest.raises(ValidationError):
        RoadWeightConfig(enabled=True, supports=[_support("A", 1000), _support("B", 1000)])


def test_invalid_support_config_wrong_count_rejected():
    with pytest.raises(ValidationError):
        RoadWeightConfig(enabled=True, supports=[_support("A", 0)])
    with pytest.raises(ValidationError):
        RoadWeightConfig(enabled=True, supports=[_support("A", 0), _support("B", 1000), _support("C", 2000)])


def test_invalid_support_config_negative_max_load_rejected():
    with pytest.raises(ValidationError):
        RoadSupport(id="A", name="A", position_x_mm=0, max_load_kg=-100)


def test_disabled_config_does_not_enforce_support_count():
    # enabled=False -> nunca se evalua nada, asi que un config a medio
    # configurar (o vacio) no debe fallar.
    RoadWeightConfig(enabled=False, supports=[])
    RoadWeightConfig(enabled=False, supports=[_support("A", 0)])
