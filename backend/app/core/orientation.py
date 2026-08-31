"""
Motor centralizado de reglas de orientacion (CUBOX 2.0, Fase 1).

Generaliza lo que antes era una unica regla hardcoded de ventanas a un
sistema de politicas (OrientationPolicy), sin modificar la matematica de la
regla original -solo la envuelve como una politica mas (PANEL_EDGE_ONLY).

REGLA CRITICA DEL NEGOCIO (no modificar sin autorizacion explicita), vigente
sin cambios bajo OrientationPolicy.PANEL_EDGE_ONLY:

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

Esta funcion es la UNICA fuente de verdad para validar orientaciones: la
usan el packer automatico, la edicion manual y la validacion final por
igual (ver core/packer.py, core/manual_move.py, core/final_validation.py).
"""

import itertools
from dataclasses import dataclass

from app.models.schemas import OrientationPolicy

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


def _panel_edge_only_orientations(width: float, height: float, thickness: float) -> list[Orientation]:
    """Las 4 orientaciones fisicamente validas de una ventana (ver regla
    critica del negocio arriba). Matematica identica a la version original."""
    return [
        Orientation(dx=width, dy=thickness, dz=height, label="P1-a (W x T, vertical H)"),
        Orientation(dx=thickness, dy=width, dz=height, label="P1-b (T x W, vertical H)"),
        Orientation(dx=height, dy=thickness, dz=width, label="P2-a (H x T, vertical W)"),
        Orientation(dx=thickness, dy=height, dz=width, label="P2-b (T x H, vertical W)"),
    ]


def _free_orientations(width: float, height: float, thickness: float) -> list[Orientation]:
    """Las 6 orientaciones axis-aligned de una caja rectangular sin
    restricciones (p.ej. BOX con OrientationPolicy.FREE)."""
    dims = (("W", width), ("H", height), ("T", thickness))
    return [
        Orientation(dx=d1, dy=d2, dz=d3, label=f"FREE ({l1} x {l2}, vertical {l3})")
        for (l1, d1), (l2, d2), (l3, d3) in itertools.permutations(dims)
    ]


def _upright_orientations(width: float, height: float, thickness: float) -> list[Orientation]:
    """`height` siempre vertical; la base solo rota 90 grados en el piso
    entre `width` y `thickness` (p.ej. PALLET, o BOX con "Keep Upright")."""
    return [
        Orientation(dx=width, dy=thickness, dz=height, label="UPRIGHT-a (W x T, vertical H)"),
        Orientation(dx=thickness, dy=width, dz=height, label="UPRIGHT-b (T x W, vertical H)"),
    ]


def _fixed_orientation(width: float, height: float, thickness: float) -> list[Orientation]:
    """Una unica orientacion, tal como se especifico el item -sin cambios."""
    return [Orientation(dx=width, dy=height, dz=thickness, label="FIXED (W x H, vertical T)")]


def get_valid_orientations(
    width: float,
    height: float,
    thickness: float,
    policy: OrientationPolicy = OrientationPolicy.PANEL_EDGE_ONLY,
) -> list[Orientation]:
    """Devuelve las orientaciones fisicamente validas de un item bajo `policy`.

    El default (PANEL_EDGE_ONLY) preserva el comportamiento exacto de antes
    de que existiera este parametro -todo llamador legacy sigue funcionando
    igual sin cambios."""
    if policy == OrientationPolicy.PANEL_EDGE_ONLY:
        return _panel_edge_only_orientations(width, height, thickness)
    if policy == OrientationPolicy.FREE:
        return _free_orientations(width, height, thickness)
    if policy == OrientationPolicy.UPRIGHT:
        return _upright_orientations(width, height, thickness)
    if policy == OrientationPolicy.FIXED:
        return _fixed_orientation(width, height, thickness)
    raise ValueError(f"Politica de orientacion desconocida: {policy}")


def is_valid_orientation(
    width: float,
    height: float,
    thickness: float,
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
    for o in get_valid_orientations(width, height, thickness, policy):
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
    width: float,
    height: float,
    thickness: float,
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
    orientations = get_valid_orientations(width, height, thickness, policy)
    current_index = _current_index(orientations, dx, dy, dz, tol)
    return _paired_orientation(orientations, current_index, _rotate_pairs(len(orientations)))


def turn_orientation(
    width: float,
    height: float,
    thickness: float,
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
    orientations = get_valid_orientations(width, height, thickness, policy)
    current_index = _current_index(orientations, dx, dy, dz, tol)
    return _paired_orientation(orientations, current_index, _turn_pairs(len(orientations)))
