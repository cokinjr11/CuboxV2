"""Puntuacion de una solucion de cubicaje (seccion 5 de V2).

score = loadedPiecesScore + priorityScore + volumeScore + floorScore
        + groupingScore + accessibilityScore (ponderados)

Los pesos son internos (no se exponen al usuario). Cuando el Optimization
Mode es Keep Groups/Systems Together, se le da mas peso a groupingScore y
menos a volumen/piso, para que el modo elegido realmente cambie el resultado.
"""

from collections import defaultdict

from app.core.geometry import TOL
from app.core.road_weight import evaluate_road_weight, weight_point_from_placed
from app.models.schemas import OptimizationMode, PackingResult, PlacedPiece, WeightBalanceMode

MIN_WALK_WIDTH_MM = 500.0

ROAD_BALANCE_BONUS_WEIGHT = 0.05
"""Bonus modesto (Fase 2B): nunca reemplaza el balance general existente ni
los pesos base -solo desempata entre alternativas que YA son validas segun
RoadWeightConfig (los limites duros se aplican en el packer/validacion
final, no aca). 0 para cualquier LoadSpace sin RoadWeightConfig habilitado
-no cambia el score de ningun escenario existente."""

_BASE_WEIGHTS = {
    "loaded": 0.32,
    "priority": 0.23,
    "volume": 0.12,
    "floor": 0.08,
    "grouping": 0.10,
    "accessibility": 0.05,
    "balance": 0.10,
}

_GROUPING_WEIGHTS = {
    "loaded": 0.28,
    "priority": 0.18,
    "volume": 0.08,
    "floor": 0.04,
    "grouping": 0.27,
    "accessibility": 0.05,
    "balance": 0.10,
}


def _priority_weight(priority: int) -> int:
    """1 = Highest ... 5 = Lowest. Valores fuera de rango se tratan como medios (3)."""
    effective = priority if 1 <= priority <= 5 else 3
    return 6 - effective


def _loaded_score(result: PackingResult) -> float:
    total = result.metrics.total_pieces
    return result.metrics.loaded_pieces / total if total > 0 else 1.0


def _priority_score(result: PackingResult) -> float:
    placed_weight = sum(_priority_weight(p.priority) for p in result.placed)
    unloaded_weight = sum(_priority_weight(u.priority) for u in result.unloaded)
    total_weight = placed_weight + unloaded_weight
    return placed_weight / total_weight if total_weight > 0 else 1.0


def _clustering_score(placed: list[PlacedPiece], key_fn, container_volume: float) -> float:
    """1.0 = piezas de un mismo grupo perfectamente juntas, 0.0 = muy dispersas.
    Proxy simple: volumen de la caja envolvente de cada grupo vs volumen del
    contenedor (sin pares de distancias, para que sea O(n))."""
    groups: dict[str, list[PlacedPiece]] = defaultdict(list)
    for p in placed:
        key = key_fn(p)
        if key:
            groups[key].append(p)

    if not groups or container_volume <= TOL:
        return 1.0

    spread_volume = 0.0
    for pieces in groups.values():
        if len(pieces) < 2:
            continue
        min_x = min(p.x for p in pieces)
        max_x = max(p.x + p.dx for p in pieces)
        min_y = min(p.y for p in pieces)
        max_y = max(p.y + p.dy for p in pieces)
        min_z = min(p.z for p in pieces)
        max_z = max(p.z + p.dz for p in pieces)
        spread_volume += (max_x - min_x) * (max_y - min_y) * (max_z - min_z)

    return max(0.0, 1.0 - spread_volume / container_volume)


def _accessibility_score(result: PackingResult) -> float:
    """Proxy simple de 'facilidad de carga': el hueco continuo mas ancho (eje
    Y) libre de piezas a nivel de piso, comparado con un ancho minimo de
    paso. No es un simulador de carga, solo una senal de que existe (o no)
    un pasillo natural."""
    container = result.container
    floor_pieces = [p for p in result.placed if p.z <= TOL]
    if not floor_pieces:
        return 1.0

    intervals = sorted((p.y, p.y + p.dy) for p in floor_pieces)
    merged: list[list[float]] = []
    for start, end in intervals:
        if merged and start <= merged[-1][1] + TOL:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])

    gaps = []
    prev_end = 0.0
    for start, end in merged:
        if start > prev_end:
            gaps.append(start - prev_end)
        prev_end = max(prev_end, end)
    if container.width > prev_end:
        gaps.append(container.width - prev_end)

    largest_gap = max(gaps) if gaps else container.width
    return min(1.0, largest_gap / MIN_WALK_WIDTH_MM)


def _weights_for(optimization_mode: OptimizationMode, weight_balance_mode: WeightBalanceMode) -> dict[str, float]:
    """Pesos base segun el Optimization Mode, con el peso de `balance`
    ajustado por Weight Balance (Ignore=0, Normal=el valor de siempre,
    Important=el doble) y el resto renormalizado proporcionalmente para
    seguir sumando 1.0."""
    weights = dict(_BASE_WEIGHTS if optimization_mode == OptimizationMode.BEST_SPACE else _GROUPING_WEIGHTS)

    normal_balance = weights["balance"]
    target_balance = {
        WeightBalanceMode.IGNORE: 0.0,
        WeightBalanceMode.NORMAL: normal_balance,
        WeightBalanceMode.IMPORTANT: normal_balance * 2,
    }[weight_balance_mode]

    delta = target_balance - weights["balance"]
    other_keys = [k for k in weights if k != "balance"]
    other_total = sum(weights[k] for k in other_keys)
    if other_total > TOL:
        for k in other_keys:
            weights[k] -= delta * (weights[k] / other_total)
    weights["balance"] = target_balance

    return weights


def _compute_components(result: PackingResult, optimization_mode: OptimizationMode) -> dict[str, float]:
    if optimization_mode == OptimizationMode.KEEP_SYSTEMS:
        key_fn = lambda p: p.system  # noqa: E731
    else:
        key_fn = lambda p: p.group  # noqa: E731

    container_volume = result.container.length * result.container.width * result.container.height

    return {
        "loaded": _loaded_score(result),
        "priority": _priority_score(result),
        "volume": result.metrics.used_volume_pct / 100,
        "floor": result.metrics.floor_utilization_pct / 100,
        "grouping": _clustering_score(result.placed, key_fn, container_volume),
        "accessibility": _accessibility_score(result),
        "balance": result.metrics.weight_balance_pct / 100,
    }


def _road_balance_bonus(result: PackingResult, weight_balance_mode: WeightBalanceMode) -> float:
    """0.0 salvo que el LoadSpace tenga RoadWeightConfig habilitado, Weight
    Balance no sea Ignore, y la solucion ya sea valida por support -en ese
    caso, premia levemente una utilizacion mas pareja entre los 2 supports."""
    config = result.container.road_weight_config
    if config is None or not config.enabled or weight_balance_mode == WeightBalanceMode.IGNORE:
        return 0.0

    road_weight = evaluate_road_weight(config, (weight_point_from_placed(p) for p in result.placed))
    if road_weight is None or not road_weight.valid:
        return 0.0

    utilization_gap = abs(road_weight.supports[0].utilization_pct - road_weight.supports[1].utilization_pct) / 100
    balance = 1 - min(1.0, utilization_gap)
    multiplier = 2.0 if weight_balance_mode == WeightBalanceMode.IMPORTANT else 1.0
    return ROAD_BALANCE_BONUS_WEIGHT * multiplier * balance


def score_solution(
    result: PackingResult,
    optimization_mode: OptimizationMode,
    weight_balance_mode: WeightBalanceMode = WeightBalanceMode.NORMAL,
) -> float:
    weights = _weights_for(optimization_mode, weight_balance_mode)
    components = _compute_components(result, optimization_mode)
    base_score = sum(weights[k] * components[k] for k in weights)
    return base_score + _road_balance_bonus(result, weight_balance_mode)


def score_breakdown(
    result: PackingResult,
    optimization_mode: OptimizationMode,
    weight_balance_mode: WeightBalanceMode = WeightBalanceMode.NORMAL,
) -> dict[str, float]:
    """Desglose simple del score (0-100 por componente) para mostrarle al
    usuario que pesa cada cosa en la solucion, sin exponer la formula ni los
    pesos internos (seccion 47 de V3)."""
    components = _compute_components(result, optimization_mode)
    return {k: round(v * 100, 1) for k, v in components.items()}
