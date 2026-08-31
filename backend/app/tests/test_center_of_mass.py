"""Center of Mass (secciones 17-19, 63 de V3): centro geometrico ponderado
por peso de todas las piezas cargadas."""

from app.core.packer import compute_metrics
from app.models.schemas import ContainerSpec, PlacedPiece

CONTAINER = ContainerSpec(id="test", name="Test", length=2000, width=200, height=200, max_weight=10000)


def _piece(piece_id, x, y, weight=10):
    return PlacedPiece(
        id=piece_id,
        code=piece_id,
        weight=weight,
        stackable=True,
        priority=1,
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


def test_center_of_mass_symmetric_distribution_is_centered():
    # dx=100: para que los centros geometricos (x + dx/2) queden simetricos
    # alrededor de length/2=1000, las piezas van en x=0 (centro=50) y
    # x=1900 (centro=1950); el promedio de ambos es exactamente 1000.
    placed = [
        _piece("a", x=0, y=0),
        _piece("b", x=1900, y=0),
        _piece("c", x=0, y=100),
        _piece("d", x=1900, y=100),
    ]
    metrics = compute_metrics(CONTAINER, placed, [])
    # centro esperado: mitad del contenedor en X, y en Y el promedio de los
    # centros geometricos de las piezas (0+50 y 100+50 -> 100)
    assert abs(metrics.center_of_mass_x - CONTAINER.length / 2) < 1.0
    assert abs(metrics.center_of_mass_y - 100.0) < 1.0


def test_center_of_mass_shifts_toward_heavier_side():
    # Todo el peso pesado de un lado (y bajo) debe desplazar el centro de masa
    # hacia ese lado, aunque haya piezas livianas del otro lado.
    placed = [
        _piece("heavy", x=0, y=0, weight=1000),
        _piece("light", x=0, y=100, weight=1),
    ]
    metrics = compute_metrics(CONTAINER, placed, [])
    assert metrics.center_of_mass_y < CONTAINER.width / 2


def test_center_of_mass_defaults_to_container_center_when_empty():
    metrics = compute_metrics(CONTAINER, [], [])
    assert metrics.center_of_mass_x == CONTAINER.length / 2
    assert metrics.center_of_mass_y == CONTAINER.width / 2
    assert metrics.center_of_mass_z == CONTAINER.height / 2
