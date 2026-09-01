"""
Distribucion de peso longitudinal para vehiculos de carretera (CUBOX 2.0,
Fase 2B).

Esto NO es un motor de normativa legal: RoadSupport.max_load_kg es siempre
un dato de CONFIGURACION del usuario (o de su empresa/fabricante), nunca un
limite legal/pais/fabricante asumido o hardcodeado aca. Ver
models/schemas.py:RoadSupport/RoadWeightConfig.

CONVENCION DE COORDENADAS (verificada en core/packer.py y en
frontend/src/components/Scene3D.tsx -DoorMarker fijo en x=0):

    x = 0                     -> puerta/abertura de carga del LoadSpace.
    x = LoadSpaceSpec.length   -> pared del fondo.

PlacedPiece.x/dx ya estan en ese sistema (coordenadas reales, no espejadas).
RoadSupport.position_x_mm usa exactamente el mismo eje: en un camion/trailer
tipico, cargado por la puerta trasera, una "Rear Axle Group" queda cerca de
x=0 y una "Front Axle Group"/"Kingpin" cerca de x=length (contra la cabina).
Este modulo no asume esa disposicion -solo usa position_x_mm tal cual se
configuro.

Modelo fisico: equilibrio estatico de 2 apoyos (Fase 2B soporta EXACTAMENTE
2 supports; ver RoadWeightConfig). El mismo calculo sirve para Truck
(Front/Rear Axle Group) y Trailer (Kingpin/Trailer Axle Group) -no hay dos
motores de fisica distintos.
"""

from dataclasses import dataclass
from typing import Iterable

from app.models.schemas import PlacedPiece, RoadSupport, RoadWeightConfig, RoadWeightMetrics, SupportLoadOut

WEIGHT_EPSILON_KG = 0.01
"""Tolerancia numerica para comparar cargas calculadas contra limites
configurados y para detectar reacciones negativas. Pequena a proposito: un
sobrepeso real en la practica es siempre ordenes de magnitud mayor a 0.01kg
-esta tolerancia solo absorbe error de punto flotante, nunca oculta un
sobrepeso genuino."""


@dataclass(frozen=True)
class WeightPoint:
    """Un peso puntual con su posicion longitudinal. Abstrae PlacedPiece, un
    candidato de manual move (x/dx/weight sueltos) o un candidato
    prospectivo del packer, para que el calculo de momento/centro de carga
    sea exactamente el mismo en los 3 casos (packer, manual_move,
    final_validation)."""

    weight: float
    x: float
    dx: float

    @property
    def center_x(self) -> float:
        return self.x + self.dx / 2


def weight_point_from_placed(p: PlacedPiece) -> WeightPoint:
    return WeightPoint(weight=p.weight, x=p.x, dx=p.dx)


def piece_center_x(x: float, dx: float) -> float:
    return x + dx / 2


def compute_total_weight(points: Iterable[WeightPoint]) -> float:
    return sum(p.weight for p in points)


def compute_longitudinal_moment(points: Iterable[WeightPoint]) -> float:
    """M = Sum(weight_i * center_x_i)."""
    return sum(p.weight * p.center_x for p in points)


def compute_load_center_x(points: Iterable[WeightPoint]) -> float | None:
    """Centro de carga longitudinal de los items actualmente cargados
    (metrica adicional -no reemplaza el Center of Mass general de
    core/packer.py:compute_metrics, que tambien considera Y/Z)."""
    points = list(points)
    total = compute_total_weight(points)
    if total <= WEIGHT_EPSILON_KG:
        return None
    return compute_longitudinal_moment(points) / total


def compute_support_reactions(
    total_weight: float, total_moment: float, support_a: RoadSupport, support_b: RoadSupport
) -> tuple[float, float]:
    """Equilibrio estatico de 2 apoyos. xA/xB se toman de
    support_a/support_b.position_x_mm tal cual -sin asumir que xA < xB; el
    resultado es fisicamente correcto en cualquier orden.

    reactionB = (M - P*xA) / (xB - xA)
    reactionA = P - reactionB

    Devuelve (reaction_a, reaction_b): la porcion del peso/momento de los
    ITEMS (sin baseline_load_kg) que recae en cada support."""
    xa = support_a.position_x_mm
    xb = support_b.position_x_mm
    if abs(xa - xb) < WEIGHT_EPSILON_KG:
        # Defensivo: RoadWeightConfig ya rechaza esto al construirse.
        raise ValueError("Los 2 supports no pueden tener la misma position_x_mm")

    reaction_b = (total_moment - total_weight * xa) / (xb - xa)
    reaction_a = total_weight - reaction_b
    return reaction_a, reaction_b


def _support_violations(
    total_a: float, reaction_a: float, support_a: RoadSupport, total_b: float, reaction_b: float, support_b: RoadSupport
) -> list[str]:
    errors: list[str] = []
    if reaction_a < -WEIGHT_EPSILON_KG:
        errors.append(f"{support_a.name}: reaccion negativa ({round(reaction_a, 2)} kg) -layout fisicamente inestable")
    if reaction_b < -WEIGHT_EPSILON_KG:
        errors.append(f"{support_b.name}: reaccion negativa ({round(reaction_b, 2)} kg) -layout fisicamente inestable")
    if total_a > support_a.max_load_kg + WEIGHT_EPSILON_KG:
        errors.append(f"{support_a.name} excede su limite de carga configurado ({round(total_a, 2)}/{support_a.max_load_kg} kg)")
    if total_b > support_b.max_load_kg + WEIGHT_EPSILON_KG:
        errors.append(f"{support_b.name} excede su limite de carga configurado ({round(total_b, 2)}/{support_b.max_load_kg} kg)")
    return errors


def would_exceed_support_limits(total_weight: float, total_moment: float, config: RoadWeightConfig | None) -> bool:
    """Chequeo liviano para el packer (se puede llamar por cada candidato
    evaluado): True si estos totales PROSPECTIVOS (incluyendo el candidato
    en evaluacion) dejarian algun support sobrecargado o con reaccion
    negativa. No construye SupportLoadOut -solo aritmetica.

    config is None o enabled=False -> siempre False (sin efecto en el
    empaque; comportamiento identico a Fase 2A)."""
    if config is None or not config.enabled:
        return False
    a, b = config.supports
    reaction_a, reaction_b = compute_support_reactions(total_weight, total_moment, a, b)
    total_a = a.baseline_load_kg + reaction_a
    total_b = b.baseline_load_kg + reaction_b
    return bool(_support_violations(total_a, reaction_a, a, total_b, reaction_b, b))


def evaluate_road_weight(config: RoadWeightConfig | None, points: Iterable[WeightPoint]) -> RoadWeightMetrics | None:
    """Evaluacion completa (metricas + validez) contra un conjunto de items
    cargados. None si config es None o enabled=False -asi los llamadores
    (final_validation, manual_move, la API) no necesitan ramificar por
    LoadSpaceType: containers/trucks/trailers sin configurar simplemente no
    reciben ningun chequeo ni metrica de peso por eje (Fase 2A intacta)."""
    if config is None or not config.enabled:
        return None

    points = list(points)
    total_weight = compute_total_weight(points)
    total_moment = compute_longitudinal_moment(points)
    a, b = config.supports
    reaction_a, reaction_b = compute_support_reactions(total_weight, total_moment, a, b)
    total_a = a.baseline_load_kg + reaction_a
    total_b = b.baseline_load_kg + reaction_b
    errors = _support_violations(total_a, reaction_a, a, total_b, reaction_b, b)

    def _support_out(support: RoadSupport, reaction: float, total: float) -> SupportLoadOut:
        utilization = round((total / support.max_load_kg) * 100, 2) if support.max_load_kg > 0 else 0.0
        return SupportLoadOut(
            id=support.id,
            name=support.name,
            position_x_mm=support.position_x_mm,
            cargo_reaction_kg=round(reaction, 2),
            baseline_load_kg=support.baseline_load_kg,
            total_load_kg=round(total, 2),
            max_load_kg=support.max_load_kg,
            utilization_pct=utilization,
            overloaded=total > support.max_load_kg + WEIGHT_EPSILON_KG,
            unstable=reaction < -WEIGHT_EPSILON_KG,
        )

    return RoadWeightMetrics(
        load_center_x_mm=compute_load_center_x(points),
        total_item_weight_kg=round(total_weight, 2),
        supports=[_support_out(a, reaction_a, total_a), _support_out(b, reaction_b, total_b)],
        valid=len(errors) == 0,
        errors=errors,
    )
