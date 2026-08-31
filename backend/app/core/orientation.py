"""
Reglas de orientacion valida para ventanas de vidrio dentro del contenedor.

REGLA CRITICA DEL NEGOCIO (no modificar sin autorizacion explicita):

Cada ventana tiene Width (W), Height (H) y Thickness (T).
La cara W x H es la cara de vidrio y JAMAS puede quedar horizontal
(es decir, la ventana nunca puede quedar "acostada" sobre el vidrio).

Solo existen dos posiciones validas:

    Posicion 1: Base = W x T   | Vertical = H
    Posicion 2: Base = H x T   | Vertical = W

Cada una de esas bases puede rotar 90 grados dentro del plano del piso
(intercambiando a que eje del contenedor -X o Y- apunta cada lado de la
base), lo cual da 4 combinaciones validas en total. Ninguna combinacion
puede tener como dimension vertical (dz) el valor de Thickness.
"""

from dataclasses import dataclass

TOL = 1e-6


@dataclass(frozen=True)
class Orientation:
    """Una orientacion valida de una pieza dentro del contenedor.

    dx: dimension de la pieza a lo largo del eje X (length) del contenedor
    dy: dimension de la pieza a lo largo del eje Y (width/depth) del contenedor
    dz: dimension de la pieza a lo largo del eje Z (height, vertical)
    label: identificador legible de la orientacion
    """

    dx: float
    dy: float
    dz: float
    label: str


def get_valid_orientations(width: float, height: float, thickness: float) -> list[Orientation]:
    """Devuelve las 4 orientaciones fisicamente validas de una ventana.

    Las 4 combinaciones surgen de las 2 posiciones permitidas (base W x T con
    vertical H, o base H x T con vertical W) cada una rotada 90 grados en el
    plano horizontal del contenedor.
    """
    return [
        Orientation(dx=width, dy=thickness, dz=height, label="P1-a (W x T, vertical H)"),
        Orientation(dx=thickness, dy=width, dz=height, label="P1-b (T x W, vertical H)"),
        Orientation(dx=height, dy=thickness, dz=width, label="P2-a (H x T, vertical W)"),
        Orientation(dx=thickness, dy=height, dz=width, label="P2-b (T x H, vertical W)"),
    ]


def is_valid_orientation(
    width: float,
    height: float,
    thickness: float,
    dx: float,
    dy: float,
    dz: float,
    tol: float = TOL,
) -> bool:
    """Verifica si una terna (dx, dy, dz) corresponde a una orientacion valida.

    Esta funcion es la unica fuente de verdad para validar orientaciones y debe
    usarse tanto en el algoritmo automatico como en el movimiento manual.
    """
    for o in get_valid_orientations(width, height, thickness):
        if abs(o.dx - dx) < tol and abs(o.dy - dy) < tol and abs(o.dz - dz) < tol:
            return True
    return False


# Las orientaciones son [P1-a, P1-b, P2-a, P2-b] (ver get_valid_orientations).
# ROTATE conserva el eje donde vive Thickness y alterna cual de Width/Height
# queda vertical: (P1-a, P2-a) y (P1-b, P2-b).
_ROTATE_PAIRS = ((0, 2), (1, 3))

# TURN conserva cual dimension queda vertical y gira la base 90 grados en el
# piso (intercambia que eje del contenedor -X o Y- ocupa cada lado de la
# base): (P1-a, P1-b) y (P2-a, P2-b).
_TURN_PAIRS = ((0, 1), (2, 3))


def _current_index(orientations: list[Orientation], dx: float, dy: float, dz: float, tol: float) -> int | None:
    for i, o in enumerate(orientations):
        if abs(o.dx - dx) < tol and abs(o.dy - dy) < tol and abs(o.dz - dz) < tol:
            return i
    return None


def _paired_orientation(
    orientations: list[Orientation], current_index: int | None, pairs: tuple[tuple[int, int], ...]
) -> Orientation | None:
    if current_index is None:
        return None
    for a, b in pairs:
        if current_index == a:
            return orientations[b]
        if current_index == b:
            return orientations[a]
    return None


def toggle_orientation(
    width: float,
    height: float,
    thickness: float,
    dx: float,
    dy: float,
    dz: float,
    tol: float = TOL,
) -> Orientation | None:
    """"Rotate": la otra orientacion valida que mantiene el eje de Thickness y
    alterna cual de Width/Height queda vertical. Devuelve None si (dx, dy, dz)
    no coincide con ninguna orientacion valida conocida.
    """
    orientations = get_valid_orientations(width, height, thickness)
    current_index = _current_index(orientations, dx, dy, dz, tol)
    return _paired_orientation(orientations, current_index, _ROTATE_PAIRS)


def turn_orientation(
    width: float,
    height: float,
    thickness: float,
    dx: float,
    dy: float,
    dz: float,
    tol: float = TOL,
) -> Orientation | None:
    """"Turn"/"Girar": gira la base 90 grados en el piso manteniendo la misma
    dimension vertical (nunca cambia que la vertical sea Width o Height, solo
    que eje del contenedor ocupa cada lado de la base). Combinado con
    toggle_orientation da acceso a las 4 orientaciones validas. Devuelve None
    si (dx, dy, dz) no coincide con ninguna orientacion valida conocida.
    """
    orientations = get_valid_orientations(width, height, thickness)
    current_index = _current_index(orientations, dx, dy, dz, tol)
    return _paired_orientation(orientations, current_index, _TURN_PAIRS)
