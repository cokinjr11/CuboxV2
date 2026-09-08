"""Validacion de edicion manual (mover, insertar, rotar) sobre el cubicaje.

Reutiliza exactamente las mismas reglas del algoritmo automatico
(orientation.py, geometry.py y, si el LoadSpace tiene RoadWeightConfig
habilitado, road_weight.py) para que el usuario nunca pueda, editando a
mano, violar ninguna restriccion que el algoritmo respeta. `validate_placement`
es la unica fuente de verdad: se usa tanto para mover una pieza ya colocada
como para insertar una pieza nueva desde Unloaded Items o para rotarla.
"""

from app.core.geometry import (
    Box,
    check_stack_weight,
    check_support,
    has_clearance_conflict,
    within_container,
)
from app.core.orientation import apply_tilt, is_valid_orientation, orientation_rejection_reason
from app.core.reserved_zones import ReservedZone, zone_conflict_with_clearance
from app.core.road_weight import WeightPoint, evaluate_road_weight, weight_point_from_placed
from app.core.tilt_collision import has_precise_collision
from app.models.schemas import ContainerSpec, ItemType, OrientationPolicy, PlacedPiece, dimensions_from_legacy

TOL = 1e-6


def validate_placement(
    piece_id: str,
    width: float,
    height: float,
    thickness: float,
    stackable: bool,
    weight: float,
    max_stack_weight: float | None,
    x: float,
    y: float,
    z: float,
    dx: float,
    dy: float,
    dz: float,
    other_pieces: list[PlacedPiece],
    container: ContainerSpec,
    reserved_zones: list[ReservedZone] | None = None,
    clearance: float = 0.0,
    orientation_policy: OrientationPolicy = OrientationPolicy.PANEL_EDGE_ONLY,
    allow_tilt: bool = False,
    max_tilt_angle: float = 0.0,
    tilt_angle: float = 0.0,
    item_type: ItemType | None = None,
    tilt_axis: str | None = None,
    base_dx: float | None = None,
    base_dy: float | None = None,
    base_dz: float | None = None,
) -> tuple[bool, str]:
    reserved_zones = reserved_zones or []

    dims = dimensions_from_legacy(width, height, thickness)
    if not is_valid_orientation(dims, dx, dy, dz, orientation_policy, allow_tilt=allow_tilt, max_tilt_angle=max_tilt_angle):
        reason = orientation_rejection_reason(orientation_policy)
        return False, reason[0].upper() + reason[1:]

    candidate_box = Box(
        id=piece_id,
        x=x,
        y=y,
        z=z,
        dx=dx,
        dy=dy,
        dz=dz,
        stackable=stackable,
        max_stack_weight=max_stack_weight,
        tilt_angle=tilt_angle,
        tilt_axis=tilt_axis,
        base_dx=base_dx,
        base_dy=base_dy,
        base_dz=base_dz,
        item_type=item_type,
    )

    if not within_container(candidate_box, container.length, container.width, container.height):
        return False, "La pieza quedaria fuera de los limites del contenedor"

    zone = zone_conflict_with_clearance(candidate_box, reserved_zones, clearance)
    if zone is not None:
        return False, f"La pieza invade una zona reservada ({zone.label})"

    other_boxes = [
        Box(
            id=p.id,
            x=p.x,
            y=p.y,
            z=p.z,
            dx=p.dx,
            dy=p.dy,
            dz=p.dz,
            stackable=p.stackable,
            max_stack_weight=p.max_stack_weight,
            tilt_angle=p.tilt_angle,
            tilt_axis=p.tilt_axis,
            base_dx=p.base_dx,
            base_dy=p.base_dy,
            base_dz=p.base_dz,
            item_type=p.item_type,
        )
        for p in other_pieces
        if p.id != piece_id
    ]

    collision = has_precise_collision(candidate_box, other_boxes)
    if collision is not None:
        return False, f"Colisiona con la pieza {collision.id}"

    clearance_conflict = has_clearance_conflict(candidate_box, other_boxes, clearance)
    if clearance_conflict is not None:
        return False, f"No respeta la separacion minima con la pieza {clearance_conflict.id}"

    ok, reason = check_support(candidate_box, other_boxes)
    if not ok:
        return False, reason

    weights_by_id = {p.id: p.weight for p in other_pieces if p.id != piece_id}
    ok, reason = check_stack_weight(candidate_box, weight, other_boxes, weights_by_id)
    if not ok:
        return False, reason

    road_weight_points = [weight_point_from_placed(p) for p in other_pieces if p.id != piece_id]
    road_weight_points.append(WeightPoint(weight=weight, x=x, dx=dx))
    road_weight = evaluate_road_weight(container.road_weight_config, road_weight_points)
    if road_weight is not None and not road_weight.valid:
        return False, "; ".join(road_weight.errors)

    return True, ""


def validate_move(
    piece: PlacedPiece,
    new_x: float,
    new_y: float,
    new_z: float,
    new_dx: float,
    new_dy: float,
    new_dz: float,
    other_pieces: list[PlacedPiece],
    container: ContainerSpec,
    reserved_zones: list[ReservedZone] | None = None,
    clearance: float = 0.0,
) -> tuple[bool, str]:
    """Un movimiento manual normal preserva el Tilt actual de la pieza tal
    cual (seccion 34 del pedido: no se resetea a vertical solo por moverla).
    Para cambiar el angulo en si, ver `validate_tilt_change`."""
    return validate_placement(
        piece.id,
        piece.source_width,
        piece.source_height,
        piece.source_thickness,
        piece.stackable,
        piece.weight,
        piece.max_stack_weight,
        new_x,
        new_y,
        new_z,
        new_dx,
        new_dy,
        new_dz,
        other_pieces,
        container,
        reserved_zones,
        clearance,
        piece.resolved_orientation_policy,
        allow_tilt=piece.allow_tilt,
        max_tilt_angle=piece.max_tilt_angle or 0.0,
        tilt_angle=piece.tilt_angle,
        item_type=piece.item_type,
        tilt_axis=piece.tilt_axis,
        base_dx=piece.base_dx,
        base_dy=piece.base_dy,
        base_dz=piece.base_dz,
    )


def validate_tilt_change(
    piece: PlacedPiece,
    new_tilt_angle: float,
    other_pieces: list[PlacedPiece],
    container: ContainerSpec,
    reserved_zones: list[ReservedZone] | None = None,
    clearance: float = 0.0,
) -> tuple[bool, str, tuple[float, float, float, float, float, float] | None]:
    """Valida (y devuelve, si es valido) la nueva geometria (x, y, z, dx, dy,
    dz) de `piece` al cambiar su Tilt a `new_tilt_angle` grados -SIGNED
    (Fase 5C-FINAL, seccion 6/7/13 del pedido: rango real [-max, +max],
    nunca solo [0, max]). Reutiliza `apply_tilt` (la misma formula que usa
    el packer automatico, ver core/orientation.py) desde el estado base (0
    grados) guardado en la pieza -nunca invierte trigonometricamente el
    angulo actual- y luego `validate_placement`, la misma fuente de verdad
    de siempre, para confirmar que la nueva envolvente sigue siendo una
    orientacion valida, cabe en el contenedor, no colisiona (precisa, no
    solo AABB) y sigue soportada en su base.

    Recalcula ademas la posicion (x, y, z): un cambio de angulo rota la
    pieza alrededor de su propio CENTRO (ver core/orientation.py:apply_tilt
    y core/tilt_collision.py:obb_from_box -la envolvente siempre esta
    centrada en el mismo punto que la geometria fisica real). Mantener
    `piece.x/y/z` (la esquina de la envolvente VIEJA) sin actualizar
    mientras dx/dy/dz cambian haria crecer la envolvente de forma asimetrica
    desde esa esquina -no desde el centro real- lo cual no corresponde a
    ninguna rotacion fisica valida (bug encontrado en verificacion runtime:
    una pieza tocando a su vecino a 0 grados reportaba colision espuria al
    inclinarse, porque la esquina vieja se comia el lado del vecino en vez
    de que el centro se mantuviera fijo).

    Ademas rechaza el cambio si alguna OTRA pieza descansa actualmente
    encima de esta -inclinarla la dejaria sin una superficie de apoyo real
    para lo que tiene arriba (seccion 24/29 del pedido); mover primero esa
    pieza es responsabilidad del usuario."""
    if not piece.allow_tilt:
        return False, "Esta pieza no admite Tilt", None

    effective_max = piece.max_tilt_angle or 0.0
    if abs(new_tilt_angle) > effective_max + TOL:
        return False, f"El angulo de Tilt excede el maximo permitido (±{effective_max:g}°)", None

    if piece.tilt_axis is None or piece.base_dx is None or piece.base_dy is None or piece.base_dz is None:
        return False, "Esta pieza no tiene un eje de Tilt definido", None

    resting_on_top = [
        p
        for p in other_pieces
        if p.id != piece.id
        and abs(p.z - (piece.z + piece.dz)) < TOL
        and max(0.0, min(p.x + p.dx, piece.x + piece.dx) - max(p.x, piece.x)) > TOL
        and max(0.0, min(p.y + p.dy, piece.y + piece.dy) - max(p.y, piece.y)) > TOL
    ]
    if resting_on_top:
        return False, f"No se puede inclinar: la pieza {resting_on_top[0].id} esta apoyada encima", None

    new_dx, new_dy, new_dz = apply_tilt(piece.base_dx, piece.base_dy, piece.base_dz, piece.tilt_axis, new_tilt_angle)

    # El eje Z se mantiene ANCLADO a la base (piece.z sin cambios) -la base
    # de la pieza no debe "flotar" ni "hundirse" solo porque dz cambio con
    # el angulo (mismo criterio que usa el packer automatico al colocar una
    # pieza inclinada por primera vez -siempre en z=cand.z, nunca recentrado
    # -ver core/packer.py). Solo el eje horizontal de Tilt (Y si
    # tilt_axis="y", X si tilt_axis="x") se recentra: ESE eje crece
    # simetrico desde el propio centro de la pieza (ver apply_tilt); dejar
    # la esquina VIEJA fija ahi haria crecer la envolvente solo hacia un
    # lado, lo que puede reportar colision espuria contra un vecino que en
    # realidad da soporte lateral del otro lado (bug encontrado en
    # verificacion runtime, no en revision de codigo).
    new_z = piece.z
    if piece.tilt_axis == "y":
        new_x = piece.x
        new_y = (piece.y + piece.dy / 2) - new_dy / 2
    else:
        new_x = (piece.x + piece.dx / 2) - new_dx / 2
        new_y = piece.y

    ok, reason = validate_placement(
        piece.id,
        piece.source_width,
        piece.source_height,
        piece.source_thickness,
        piece.stackable,
        piece.weight,
        piece.max_stack_weight,
        new_x,
        new_y,
        new_z,
        new_dx,
        new_dy,
        new_dz,
        other_pieces,
        container,
        reserved_zones,
        clearance,
        piece.resolved_orientation_policy,
        allow_tilt=piece.allow_tilt,
        max_tilt_angle=effective_max,
        tilt_angle=new_tilt_angle,
        item_type=piece.item_type,
        tilt_axis=piece.tilt_axis,
        base_dx=piece.base_dx,
        base_dy=piece.base_dy,
        base_dz=piece.base_dz,
    )
    if not ok:
        return False, reason, None
    return True, "", (new_x, new_y, new_z, new_dx, new_dy, new_dz)
