"""
Motor centralizado de reglas de orientacion (CUBOX 2.0).

Fase 3A: opera sobre Dimensions3D (length/width/height), la representacion
generica canonica -no sobre width/height/thickness sueltos. Los llamadores
legacy (WindowItem/PlacedPiece/UnloadedItem) siguen almacenando y
serializando width/height/thickness tal cual; convierten a Dimensions3D via
`item.dimensions`/`piece.source_dimensions` (ver models/schemas.py) antes de
llamar a este modulo. `dimensions_from_legacy()` es el UNICO punto de
conversion, y es el mismo para cualquier ItemType.

REGLA CRITICA DEL NEGOCIO (no modificar sin autorizacion explicita), vigente
sin cambios bajo OrientationPolicy.PANEL_EDGE_ONLY:

Cada ventana tiene Width (W), Height (H) y Thickness (T). La cara W x H es
la cara de vidrio y JAMAS puede quedar horizontal (la ventana nunca puede
quedar "acostada" sobre el vidrio). Solo existen dos posiciones validas:

    Posicion 1: Base = W x T   | Vertical = H
    Posicion 2: Base = H x T   | Vertical = W

Cada base puede rotar 90 grados en el plano del piso, dando 4 combinaciones
validas en total. Ninguna combinacion puede tener como dimension vertical
(dz) el valor de Thickness. Sobre Dimensions3D esto se expresa via
PANEL_DIMENSION_MAPPING (face_axes=("length","height"), thickness_axis=
"width"): junto con dimensions_from_legacy(width,height,thickness) =
Dimensions3D(length=width, width=thickness, height=height), reproduce
exactamente esta regla (face_axes selecciona W y H, thickness_axis
selecciona T) -ver _panel_edge_only_orientations.

Esta funcion es la UNICA fuente de verdad para validar orientaciones: la
usan el packer automatico, la edicion manual y la validacion final por
igual (ver core/packer.py, core/manual_move.py, core/final_validation.py).
"""

import itertools
from dataclasses import dataclass

from app.models.schemas import PANEL_DIMENSION_MAPPING, Dimensions3D, OrientationPolicy, PanelDimensionMapping

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


def _panel_edge_only_orientations(
    dims: Dimensions3D, mapping: PanelDimensionMapping = PANEL_DIMENSION_MAPPING
) -> list[Orientation]:
    """Las 4 orientaciones fisicamente validas de un panel/ventana (ver
    regla critica del negocio arriba). face_axes/thickness_axis vienen de
    `mapping` -nunca se infieren por heuristica geometrica."""
    face_a = getattr(dims, mapping.face_axes[0])
    face_b = getattr(dims, mapping.face_axes[1])
    t = getattr(dims, mapping.thickness_axis)
    return [
        Orientation(dx=face_a, dy=t, dz=face_b, label="P1-a (cara-a x T, vertical cara-b)"),
        Orientation(dx=t, dy=face_a, dz=face_b, label="P1-b (T x cara-a, vertical cara-b)"),
        Orientation(dx=face_b, dy=t, dz=face_a, label="P2-a (cara-b x T, vertical cara-a)"),
        Orientation(dx=t, dy=face_b, dz=face_a, label="P2-b (T x cara-b, vertical cara-a)"),
    ]


def _free_orientations(dims: Dimensions3D) -> list[Orientation]:
    """Las 6 orientaciones axis-aligned de una caja rectangular sin
    restricciones (p.ej. BOX con OrientationPolicy.FREE)."""
    axes = (("L", dims.length), ("W", dims.width), ("H", dims.height))
    return [
        Orientation(dx=d1, dy=d2, dz=d3, label=f"FREE ({l1} x {l2}, vertical {l3})")
        for (l1, d1), (l2, d2), (l3, d3) in itertools.permutations(axes)
    ]


def _upright_orientations(dims: Dimensions3D) -> list[Orientation]:
    """`height` siempre vertical; la base solo rota 90 grados en el piso
    entre `length` y `width` (p.ej. PALLET, o BOX con "Keep Upright")."""
    return [
        Orientation(dx=dims.length, dy=dims.width, dz=dims.height, label="UPRIGHT-a (L x W, vertical H)"),
        Orientation(dx=dims.width, dy=dims.length, dz=dims.height, label="UPRIGHT-b (W x L, vertical H)"),
    ]


def _fixed_orientation(dims: Dimensions3D) -> list[Orientation]:
    """Una unica orientacion, tal como se especifico el item -sin cambios."""
    return [Orientation(dx=dims.length, dy=dims.width, dz=dims.height, label="FIXED (L x W, vertical H)")]


def get_valid_orientations(
    dims: Dimensions3D,
    policy: OrientationPolicy = OrientationPolicy.PANEL_EDGE_ONLY,
) -> list[Orientation]:
    """Devuelve las orientaciones fisicamente validas de un item bajo `policy`."""
    if policy == OrientationPolicy.PANEL_EDGE_ONLY:
        return _panel_edge_only_orientations(dims)
    if policy == OrientationPolicy.FREE:
        return _free_orientations(dims)
    if policy == OrientationPolicy.UPRIGHT:
        return _upright_orientations(dims)
    if policy == OrientationPolicy.FIXED:
        return _fixed_orientation(dims)
    raise ValueError(f"Politica de orientacion desconocida: {policy}")


def is_valid_orientation(
    dims: Dimensions3D,
    dx: float,
    dy: float,
    dz: float,
    policy: OrientationPolicy = OrientationPolicy.PANEL_EDGE_ONLY,
    tol: float = TOL,
) -> bool:
    """Verifica si una terna (dx, dy, dz) corresponde a una orientacion valida
    bajo `policy`. Esta funcion es la unica fuente de verdad para validar
    orientaciones y debe usarse tanto en el algoritmo automatico como en el
    movimiento manual y la validacion final."""
    for o in get_valid_orientations(dims, policy):
        if abs(o.dx - dx) < tol and abs(o.dy - dy) < tol and abs(o.dz - dz) < tol:
            return True
    return False


def orientation_rejection_reason(policy: OrientationPolicy = OrientationPolicy.PANEL_EDGE_ONLY) -> str:
    """Mensaje legible (sin mayuscula inicial) de por que una orientacion fue
    rechazada por `is_valid_orientation`, sin hardcodear vocabulario de
    ventanas en las funciones de validacion generica (manual_move.py,
    final_validation.py)."""
    if policy == OrientationPolicy.PANEL_EDGE_ONLY:
        return "orientacion invalida: la ventana no puede quedar acostada sobre la cara de vidrio"
    if policy == OrientationPolicy.UPRIGHT:
        return "orientacion invalida: la pieza debe permanecer vertical (upright)"
    if policy == OrientationPolicy.FIXED:
        return "orientacion invalida: esta pieza no admite cambios de orientacion"
    return "orientacion invalida para esta pieza"


def _current_index(orientations: list[Orientation], dx: float, dy: float, dz: float, tol: float) -> int | None:
    for i, o in enumerate(orientations):
        if abs(o.dx - dx) < tol and abs(o.dy - dy) < tol and abs(o.dz - dz) < tol:
            return i
    return None


def _rotate_pairs(n: int) -> tuple[tuple[int, int], ...]:
    """"Rotate": empareja el indice i con i + n/2. Para n=4 (PANEL_EDGE_ONLY)
    da exactamente (0,2),(1,3) -identico al esquema original hardcoded."""
    half = n // 2
    return tuple((i, i + half) for i in range(half))


def _turn_pairs(n: int) -> tuple[tuple[int, int], ...]:
    """"Turn": empareja indices consecutivos (0,1),(2,3),... Para n=4
    (PANEL_EDGE_ONLY) da exactamente (0,1),(2,3) -identico al original."""
    return tuple((i, i + 1) for i in range(0, n - n % 2, 2))


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
    dims: Dimensions3D,
    dx: float,
    dy: float,
    dz: float,
    policy: OrientationPolicy = OrientationPolicy.PANEL_EDGE_ONLY,
    tol: float = TOL,
) -> Orientation | None:
    """"Rotate": para PANEL_EDGE_ONLY, la otra orientacion valida que
    mantiene el eje de Thickness y alterna cual de Width/Height queda
    vertical (comportamiento identico al original). Devuelve None si
    (dx, dy, dz) no coincide con ninguna orientacion valida conocida, o si
    la politica no tiene una orientacion "pareja" (p.ej. FIXED)."""
    orientations = get_valid_orientations(dims, policy)
    current_index = _current_index(orientations, dx, dy, dz, tol)
    return _paired_orientation(orientations, current_index, _rotate_pairs(len(orientations)))


def turn_orientation(
    dims: Dimensions3D,
    dx: float,
    dy: float,
    dz: float,
    policy: OrientationPolicy = OrientationPolicy.PANEL_EDGE_ONLY,
    tol: float = TOL,
) -> Orientation | None:
    """"Turn"/"Girar": para PANEL_EDGE_ONLY, gira la base 90 grados en el
    piso manteniendo la misma dimension vertical (comportamiento identico al
    original). Combinado con toggle_orientation da acceso a las 4
    orientaciones validas de esa politica. Devuelve None si (dx, dy, dz) no
    coincide con ninguna orientacion valida conocida, o si la politica no
    tiene una orientacion "pareja"."""
    orientations = get_valid_orientations(dims, policy)
    current_index = _current_index(orientations, dx, dy, dz, tol)
    return _paired_orientation(orientations, current_index, _turn_pairs(len(orientations)))
