"""
Test obligatorio (especificado por el usuario):

Ventana 1200 x 2000 x 100 (Width x Height x Thickness):
  - Debe aceptar 1200 x 100 x 2000  (dx=1200, dy=100, dz=2000)
  - Debe aceptar 2000 x 100 x 1200  (dx=2000, dy=100, dz=1200)
  - Debe RECHAZAR SIEMPRE 1200 x 2000 x 100 (dx=1200, dy=2000, dz=100)
    -> esta seria la ventana acostada sobre la cara de vidrio.

Si una modificacion futura permite la tercera orientacion, este test debe
fallar y se considera un bug critico.

Fase 3A: get_valid_orientations/is_valid_orientation ahora reciben
Dimensions3D (representacion generica canonica) en vez de width/height/
thickness sueltos -se convierte via dimensions_from_legacy(), el mismo
mapeo fijo que usa WindowItem.dimensions/PlacedPiece.source_dimensions. Las
aserciones (que ternas dx/dy/dz son validas/invalidas) no cambiaron en
absoluto.
"""

import pytest

from app.core.orientation import get_valid_orientations, is_valid_orientation
from app.models.schemas import dimensions_from_legacy

WIDTH, HEIGHT, THICKNESS = 1200, 2000, 100
DIMS = dimensions_from_legacy(WIDTH, HEIGHT, THICKNESS)


def test_accepts_position_1_base_width_thickness():
    assert is_valid_orientation(DIMS, dx=1200, dy=100, dz=2000) is True


def test_accepts_position_1_rotated_in_plane():
    assert is_valid_orientation(DIMS, dx=100, dy=1200, dz=2000) is True


def test_accepts_position_2_base_height_thickness():
    assert is_valid_orientation(DIMS, dx=2000, dy=100, dz=1200) is True


def test_accepts_position_2_rotated_in_plane():
    assert is_valid_orientation(DIMS, dx=100, dy=2000, dz=1200) is True


def test_rejects_lying_flat_on_glass_face():
    """PROHIBIDO: base = Width x Height, vertical = Thickness."""
    assert is_valid_orientation(DIMS, dx=1200, dy=2000, dz=100) is False


def test_rejects_lying_flat_on_glass_face_rotated():
    assert is_valid_orientation(DIMS, dx=2000, dy=1200, dz=100) is False


def test_exactly_four_valid_orientations():
    orientations = get_valid_orientations(DIMS)
    assert len(orientations) == 4
    for o in orientations:
        assert o.dz != THICKNESS


@pytest.mark.parametrize(
    "dx,dy,dz",
    [
        (1200, 2000, 100),
        (2000, 1200, 100),
    ],
)
def test_forbidden_orientation_never_valid_regardless_of_order(dx, dy, dz):
    assert is_valid_orientation(DIMS, dx, dy, dz) is False


def test_square_like_window_never_lies_on_glass():
    """Caso limite: aunque width == height, la cara de vidrio sigue prohibida."""
    w = h = 1000
    t = 50
    dims = dimensions_from_legacy(w, h, t)
    assert is_valid_orientation(dims, dx=1000, dy=1000, dz=50) is False
    assert is_valid_orientation(dims, dx=1000, dy=50, dz=1000) is True
