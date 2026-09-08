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
import math
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

    Fase 5C (Tilt/Inclination): dx/dy/dz de una orientacion inclinada ya son
    la ENVOLVENTE AABB de la pieza inclinada (ver apply_tilt) -no la pieza
    "acostada visualmente pero con caja de colision vertical". Por eso
    collision/boundaries/support/stacking (geometry.py) no necesitan saber
    que existe Tilt: siguen comparando dx/dy/dz como siempre.

    tilt_angle/tilt_axis/base_* solo tienen valor para trazabilidad
    (PlacedPiece) y para poder recalcular la geometria si el angulo cambia
    manualmente despues -no participan en ninguna comparacion geometrica.
    """

    dx: float
    dy: float
    dz: float
    label: str
    tilt_angle: float = 0.0
    tilt_axis: str | None = None
    base_dx: float | None = None
    base_dy: float | None = None
    base_dz: float | None = None


def apply_tilt(
    base_dx: float, base_dy: float, base_dz: float, tilt_axis: str | None, tilt_angle_deg: float
) -> tuple[float, float, float]:
    """Envolvente AABB (dx, dy, dz) de un prisma rectangular que se inclina
    `tilt_angle_deg` grados alrededor de su arista de apoyo (la base se
    queda quieta; el resto se inclina hacia `tilt_axis`). Formula estandar
    de bounding box de un rectangulo rotado, aplicada solo al eje vertical
    (base_dz) y al eje horizontal de Thickness (`tilt_axis`) -el otro eje
    horizontal no cambia.

        nuevo_dz              = base_dz*cos(theta) + T*sin(theta)
        nuevo_eje_thickness    = base_dz*sin(theta) + T*cos(theta)

    donde T es base_dx/base_dy segun `tilt_axis` y theta = abs(tilt_angle_deg)
    en radianes. Fase 5C-FINAL: `tilt_angle_deg` es SIGNED (direccion de la
    inclinacion), pero la ENVOLVENTE es simetrica por construccion -inclinar
    +15 o -15 grados produce exactamente la misma caja AABB, solo cambia
    hacia que lado se inclina la geometria fisica real (ver DragBox.tsx /
    tilt_collision.py, que si usan el signo). Por eso esta funcion trabaja
    siempre con la MAGNITUD (abs) para el calculo de dx/dy/dz -el signo se
    preserva por separado en PlacedPiece.tilt_angle, nunca se intenta
    recuperar desde la envolvente (geometricamente imposible: +/-15 son
    indistinguibles en dx/dy/dz). `tilt_axis=None` o angulo ~0 -> misma caja
    sin cambios. Unica fuente de verdad para esta transformacion: la usan
    tanto la generacion de candidatos de tilt automatico (_tilt_variant, mas
    abajo) como el endpoint manual /set-tilt (ver core/manual_move.py)."""
    if tilt_axis is None or abs(tilt_angle_deg) <= TOL:
        return base_dx, base_dy, base_dz
    theta = math.radians(abs(tilt_angle_deg))
    sin_t, cos_t = math.sin(theta), math.cos(theta)
    if tilt_axis == "x":
        t, h = base_dx, base_dz
        return h * sin_t + t * cos_t, base_dy, h * cos_t + t * sin_t
    t, h = base_dy, base_dz
    return base_dx, h * sin_t + t * cos_t, h * cos_t + t * sin_t


def _tilt_variant(base: "Orientation", tilt_angle_deg: float) -> "Orientation":
    dx, dy, dz = apply_tilt(base.dx, base.dy, base.dz, base.tilt_axis, tilt_angle_deg)
    return Orientation(
        dx=dx,
        dy=dy,
        dz=dz,
        label=f"{base.label} + Tilt {tilt_angle_deg:g}°",
        tilt_angle=tilt_angle_deg,
        tilt_axis=base.tilt_axis,
        base_dx=base.dx,
        base_dy=base.dy,
        base_dz=base.dz,
    )


_TILT_CANDIDATE_FRACTIONS: tuple[float, ...] = (0.25, 0.5, 0.75, 1.0)
"""Fase 5C-FINAL, seccion 10 del pedido: barrido ACOTADO y DETERMINISTICO de
magnitudes de Tilt (25/50/75/100% del maximo efectivo) -nunca un barrido
continuo (no 0.1 en 0.1). 0 grados ya esta cubierto por las orientaciones
`base` (y el packer SIEMPRE las prueba primero, ver packer.py -seccion 8:
"0 preferred"). Con 4 fracciones incluye deliberadamente un angulo
INTERMEDIO (no solo 0 y el maximo, ver seccion 11 del pedido: un caso real
puede necesitar exactamente, por ejemplo, el 50% del maximo para caber)."""


def _panel_edge_only_orientations(
    dims: Dimensions3D,
    mapping: PanelDimensionMapping = PANEL_DIMENSION_MAPPING,
    allow_tilt: bool = False,
    max_tilt_angle: float = 0.0,
) -> list[Orientation]:
    """Las 4 orientaciones fisicamente validas de un panel/ventana (ver
    regla critica del negocio arriba). face_axes/thickness_axis vienen de
    `mapping` -nunca se infieren por heuristica geometrica.

    Fase 5C: cada orientacion se etiqueta con `tilt_axis` ('x' si Thickness
    quedo en dx, 'y' si quedo en dy) -necesario incluso a 0 grados, para que
    PlacedPiece sepa sobre que eje se podria inclinar despues. Si
    `allow_tilt` y `max_tilt_angle` > 0 (Fase 5C-FINAL, seccion 9/10 del
    pedido), se agregan ademas variantes inclinadas de cada orientacion base
    en AMBOS signos (+/-) a un set acotado de magnitudes -ver
    _TILT_CANDIDATE_FRACTIONS. `apply_tilt` ya trabaja con abs(angulo) para
    el tamano de la envolvente (simetrica), asi que +M y -M dan la MISMA
    caja pero se generan como candidatos SEPARADOS -solo el signo
    almacenado en Orientation.tilt_angle distingue cual es cual, para que
    packer.py pueda registrar el signo correcto en PlacedPiece."""
    face_a = getattr(dims, mapping.face_axes[0])
    face_b = getattr(dims, mapping.face_axes[1])
    t = getattr(dims, mapping.thickness_axis)
    base = [
        Orientation(dx=face_a, dy=t, dz=face_b, label="P1-a (cara-a x T, vertical cara-b)", tilt_axis="y"),
        Orientation(dx=t, dy=face_a, dz=face_b, label="P1-b (T x cara-a, vertical cara-b)", tilt_axis="x"),
        Orientation(dx=face_b, dy=t, dz=face_a, label="P2-a (cara-b x T, vertical cara-a)", tilt_axis="y"),
        Orientation(dx=t, dy=face_b, dz=face_a, label="P2-b (T x cara-b, vertical cara-a)", tilt_axis="x"),
    ]
    if not allow_tilt or max_tilt_angle <= TOL:
        return base
    tilted: list[Orientation] = []
    for o in base:
        for fraction in _TILT_CANDIDATE_FRACTIONS:
            magnitude = max_tilt_angle * fraction
            if magnitude <= TOL:
                continue
            tilted.append(_tilt_variant(o, magnitude))
            tilted.append(_tilt_variant(o, -magnitude))
    return base + tilted


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
    allow_tilt: bool = False,
    max_tilt_angle: float = 0.0,
) -> list[Orientation]:
    """Devuelve las orientaciones fisicamente validas de un item bajo `policy`.

    `allow_tilt`/`max_tilt_angle` (Fase 5C) solo tienen efecto bajo
    PANEL_EDGE_ONLY -por defecto (False/0) el resultado es identico al de
    antes de Fase 5C para cualquier caller que no los pase (compatibilidad
    hacia atras, seccion 7 del pedido)."""
    if policy == OrientationPolicy.PANEL_EDGE_ONLY:
        return _panel_edge_only_orientations(dims, allow_tilt=allow_tilt, max_tilt_angle=max_tilt_angle)
    if policy == OrientationPolicy.FREE:
        return _free_orientations(dims)
    if policy == OrientationPolicy.UPRIGHT:
        return _upright_orientations(dims)
    if policy == OrientationPolicy.FIXED:
        return _fixed_orientation(dims)
    raise ValueError(f"Politica de orientacion desconocida: {policy}")


def _solve_tilt_angle(base: "Orientation", dx: float, dy: float, dz: float, tol: float) -> float | None:
    """Si (dx, dy, dz) es la envolvente de `base` inclinada ALGUN angulo en
    [0, 90] grados, devuelve ese angulo (grados); None si no hay ninguna
    inclinacion fisicamente coherente que explique esta terna.

    Resuelve el sistema lineal inverso de apply_tilt:

        dz              = h*cos(theta) + t*sin(theta)
        eje_thickness   = h*sin(theta) + t*cos(theta)

    (h, t conocidos: base.dz y base.dx/dy segun tilt_axis) para (cos, sin),
    valida que cos^2+sin^2 ~= 1 (una terna arbitraria no corresponde a
    NINGUN angulo real) y que el otro eje horizontal no cambio. Se usa desde
    is_valid_orientation para soportar Tilt MANUAL continuo (cualquier
    angulo entre 0 y el maximo efectivo, no solo los 2 candidatos discretos
    que prueba el packer automatico -ver get_valid_orientations)."""
    if base.tilt_axis is None:
        return None
    if base.tilt_axis == "x":
        other_dim, other_base = dy, base.dy
        thickness_dim, t = dx, base.dx
    else:
        other_dim, other_base = dx, base.dx
        thickness_dim, t = dy, base.dy
    if abs(other_dim - other_base) > tol:
        return None
    h = base.dz
    det = h * h - t * t
    if abs(det) < 1e-9:
        return None
    cos_t = (h * dz - t * thickness_dim) / det
    sin_t = (h * thickness_dim - t * dz) / det
    if abs(cos_t * cos_t + sin_t * sin_t - 1.0) > 1e-4:
        return None
    angle = math.degrees(math.atan2(sin_t, cos_t))
    if angle < -1e-4 or angle > 90 + 1e-4:
        return None
    return max(0.0, angle)


def is_valid_orientation(
    dims: Dimensions3D,
    dx: float,
    dy: float,
    dz: float,
    policy: OrientationPolicy = OrientationPolicy.PANEL_EDGE_ONLY,
    tol: float = TOL,
    allow_tilt: bool = False,
    max_tilt_angle: float = 0.0,
) -> bool:
    """Verifica si una terna (dx, dy, dz) corresponde a una orientacion valida
    bajo `policy`. Esta funcion es la unica fuente de verdad para validar
    orientaciones y debe usarse tanto en el algoritmo automatico como en el
    movimiento manual y la validacion final.

    Fase 5C: con `allow_tilt`/`max_tilt_angle` (los valores YA resueltos de
    la pieza), ademas de las orientaciones base tambien se acepta CUALQUIER
    inclinacion continua entre 0 y el maximo efectivo (ver _solve_tilt_angle)
    -no solo los 2 candidatos discretos que prueba el packer automatico
    (get_valid_orientations con allow_tilt=True): el Tilt MANUAL (seccion 28
    del pedido) permite cualquier angulo intermedio, y tanto la validacion
    manual como la final deben poder confirmarlo. Sin `allow_tilt` (default),
    una pieza inclinada nunca coincide -por eso Rotate/Turn (que llaman esta
    funcion indirectamente via toggle_orientation/turn_orientation sin pasar
    tilt) se rechazan limpiamente sobre una pieza inclinada en vez de
    reinterpretar mal su eje de Tilt (ver seccion 36 del pedido)."""
    for o in get_valid_orientations(dims, policy):
        if abs(o.dx - dx) < tol and abs(o.dy - dy) < tol and abs(o.dz - dz) < tol:
            return True
        if allow_tilt and max_tilt_angle > TOL and policy == OrientationPolicy.PANEL_EDGE_ONLY:
            angle = _solve_tilt_angle(o, dx, dy, dz, tol)
            if angle is not None and angle <= max_tilt_angle + 1e-4:
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
