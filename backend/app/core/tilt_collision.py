"""Colision precisa (narrow phase) para piezas inclinadas -Fase 5C-FINAL.

Arquitectura (seccion 20-24 del pedido):

    AABB broad phase (geometry.py:boxes_overlap, sin cambios)
        -> sin solape de envolventes: NO hay colision, listo (barato)
        -> con solape de envolventes Y alguna de las 2 piezas inclinada:
           narrow phase OBB/SAT aca abajo -> penetracion fisica real?
               SI -> colision
               NO -> valido (contacto o falso positivo de la envolvente)

Cubox solo permite Tilt de UN eje por pieza (nunca 3 ejes libres -ver
core/orientation.py): cada pieza inclinada rota alrededor de UN eje mundial
conocido (X si tilt_axis="y", Y si tilt_axis="x") por un angulo SIGNED
conocido. Esto simplifica el caso general de SAT para 2 OBB arbitrarios (que
sigue aplicandose completo, 15 ejes candidatos: 3+3 caras + 9 productos
cruzados de aristas) porque la matriz de rotacion de cada caja se construye
de un unico angulo conocido -no hace falta quaternions ni una libreria de
fisica general.

Contacto vs colision (seccion 19 del pedido): SAT con un margen de
tolerancia (`tol`) tratado como penetracion NEGATIVA -si 2 cajas se tocan o
casi se tocan (separacion/penetracion dentro de `tol`), NO se reporta
colision. Solo penetracion real (mayor a `tol` en TODOS los ejes) cuenta.

Fase 5C FINAL SIMPLIFICATION: un panel inclinado NO necesita tocar otro
panel ni una pared del Load Space para ser valido -esa exigencia de soporte
lateral obligatorio (check_lateral_support/check_wall_support/
check_lateral_or_wall_support) fue removida por ser mas restrictiva que el
comportamiento de producto pretendido (ver el pedido de simplificacion
final). Este modulo ahora solo resuelve la pregunta que SI sigue siendo
obligatoria: contacto real vs penetracion fisica real.
"""

import math
from dataclasses import dataclass

from app.core.geometry import Box, boxes_overlap

TOL = 1e-6

Vec3 = tuple[float, float, float]


def _sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _norm(a: Vec3) -> float:
    return math.sqrt(_dot(a, a))


def axes_for_tilt(tilt_axis: str | None, tilt_angle_deg: float) -> tuple[Vec3, Vec3, Vec3]:
    """3 ejes locales ortonormales (coordenadas del mundo: X=length,
    Y=width/depth, Z=height) de una pieza inclinada `tilt_angle_deg` grados
    (SIGNED) alrededor del eje mundial perpendicular a `tilt_axis` -rotacion
    sobre X si tilt_axis="y", sobre Y si tilt_axis="x" (los mismos 2 ejes
    que mezcla core/orientation.py:apply_tilt). Sin inclinacion (tilt_axis
    None o angulo ~0), devuelve los ejes mundiales tal cual (caja
    axis-aligned): esto hace que toda pieza -inclinada o no- se pueda tratar
    con la MISMA representacion OBB."""
    if tilt_axis is None or abs(tilt_angle_deg) <= TOL:
        return (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)
    theta = math.radians(tilt_angle_deg)
    sin_t, cos_t = math.sin(theta), math.cos(theta)
    if tilt_axis == "y":
        return (1.0, 0.0, 0.0), (0.0, cos_t, sin_t), (0.0, -sin_t, cos_t)
    return (cos_t, 0.0, -sin_t), (0.0, 1.0, 0.0), (sin_t, 0.0, cos_t)


@dataclass
class OrientedBox:
    """Caja orientada: centro mundial, 3 ejes locales ortonormales y sus 3
    semi-extensiones -de las dimensiones FISICAS reales (base_dx/dy/dz),
    NUNCA de la envolvente AABB inflada (ver core/orientation.py:apply_tilt)."""

    center: Vec3
    axes: tuple[Vec3, Vec3, Vec3]
    half_extents: Vec3


def obb_from_box(box: Box) -> OrientedBox:
    """Construye la OBB fisica real de una `Box` colocada -SIEMPRE, tenga o
    no Tilt (angulo 0 -> caja axis-aligned normal, mismos ejes mundiales,
    misma envolvente). El centro de la OBB coincide EXACTAMENTE con el
    centro de la envolvente AABB (box.x/y/z + box.dx/dy/dz / 2) para
    cualquier angulo: un rectangulo tiene simetria central respecto de su
    propio centro, asi que rotarlo alrededor de ese centro nunca desplaza
    su bounding box -por eso no hace falta ningun offset de pivote extra."""
    center = (box.x + box.dx / 2.0, box.y + box.dy / 2.0, box.z + box.dz / 2.0)
    axes = axes_for_tilt(box.tilt_axis, box.tilt_angle)
    base_dx = box.base_dx if box.base_dx is not None else box.dx
    base_dy = box.base_dy if box.base_dy is not None else box.dy
    base_dz = box.base_dz if box.base_dz is not None else box.dz
    half_extents = (base_dx / 2.0, base_dy / 2.0, base_dz / 2.0)
    return OrientedBox(center, axes, half_extents)


def _project_radius(box: OrientedBox, axis: Vec3) -> float:
    return sum(box.half_extents[i] * abs(_dot(box.axes[i], axis)) for i in range(3))


def _candidate_axes(a: OrientedBox, b: OrientedBox) -> list[Vec3]:
    axes = list(a.axes) + list(b.axes)
    for i in range(3):
        for j in range(3):
            axis = _cross(a.axes[i], b.axes[j])
            if _norm(axis) > 1e-9:
                axes.append(axis)
    return axes


def obb_overlap(a: OrientedBox, b: OrientedBox, tol: float = TOL) -> bool:
    """Separating Axis Theorem completo para 2 cajas orientadas 3D. Si
    CUALQUIER eje candidato separa las proyecciones (con margen `tol`, ver
    docstring del modulo), las cajas NO se solapan de verdad."""
    d = _sub(b.center, a.center)
    for axis in _candidate_axes(a, b):
        n = _norm(axis)
        if n < 1e-9:
            continue
        unit = (axis[0] / n, axis[1] / n, axis[2] / n)
        center_dist = abs(_dot(d, unit))
        combined_radius = _project_radius(a, unit) + _project_radius(b, unit)
        if center_dist > combined_radius - tol:
            return False  # este eje separa (o apenas se tocan): no hay colision real
    return True  # ningun eje separo: penetracion real en todos


def precise_collision(a: Box, b: Box, tol: float = TOL) -> bool:
    """Broad phase (AABB) primero -sin solape de envolventes, no hay
    colision, punto. Con solape: si NINGUNA de las 2 piezas esta inclinada,
    el broad phase YA es exacto (una AABB es su propia OBB) -se confia en
    el resultado sin narrow phase (performance, seccion 21/24 del pedido).
    Con al menos una inclinada, el solape de envolventes puede ser un falso
    positivo (seccion 20): se resuelve con SAT sobre la geometria fisica
    real."""
    if not boxes_overlap(a, b, tol):
        return False
    if a.tilt_angle == 0 and b.tilt_angle == 0:
        return True
    return obb_overlap(obb_from_box(a), obb_from_box(b), tol)


def has_precise_collision(box: Box, others: list[Box], tol: float = TOL) -> Box | None:
    """Equivalente Tilt-aware de geometry.has_collision: devuelve la primera
    caja con la que `box` colisiona de verdad (penetracion fisica, no solo
    solape de envolvente), o None."""
    for other in others:
        if other.id == box.id:
            continue
        if precise_collision(box, other, tol):
            return other
    return None
