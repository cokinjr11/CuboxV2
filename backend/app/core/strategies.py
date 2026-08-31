"""Estrategias de orden para el motor de cubicaje (seccion 3 de V2).

Cada estrategia es solo una forma distinta de ORDENAR las piezas antes de
correr el mismo motor de colocacion de siempre (`packer.pack_container`). No
se reescribe el motor: se generaliza para que reciba el orden ya armado.

Escala de Priority: 1 = Highest ... 5 = Lowest (menor numero = mas prioritario).
Por eso el desempate por prioridad siempre ordena ASCENDENTE.

El Optimization Mode elegido por el usuario se mezcla como clave secundaria
(agrupar por Group o System) en TODAS las estrategias, para que el usuario no
tenga que elegir estrategia y modo por separado.
"""

from typing import Callable

from app.models.schemas import OptimizationMode, WindowItem

STRATEGIES: list[str] = [
    "largest_volume",
    "largest_footprint",
    "tallest_first",
    "highest_priority",
    "group_and_size",
    "system_and_size",
    "longest_dimension",
]

STRATEGY_LABELS: dict[str, str] = {
    "largest_volume": "Largest Volume First",
    "largest_footprint": "Largest Footprint First",
    "tallest_first": "Tallest First",
    "highest_priority": "Highest Priority First",
    "group_and_size": "Group + Size",
    "system_and_size": "System + Size",
    "longest_dimension": "Longest Dimension First",
}


def _volume(w: WindowItem) -> float:
    return w.width * w.height * w.thickness


def _footprint(w: WindowItem) -> float:
    """Aproximacion de la base: Width x Thickness (Orientacion A)."""
    return w.width * w.thickness


def _tallest(w: WindowItem) -> float:
    return max(w.width, w.height)


def _longest(w: WindowItem) -> float:
    return max(w.width, w.height, w.thickness)


def _grouping_key(w: WindowItem, optimization_mode: OptimizationMode) -> str:
    if optimization_mode == OptimizationMode.KEEP_GROUPS:
        return w.group or ""
    if optimization_mode == OptimizationMode.KEEP_SYSTEMS:
        return w.system or ""
    return ""


def build_sort_key(strategy: str, optimization_mode: OptimizationMode) -> Callable:
    """Devuelve una funcion de orden para instancias con atributo `.source: WindowItem`."""

    def key(inst):
        w: WindowItem = inst.source
        grouping = _grouping_key(w, optimization_mode)

        if strategy == "largest_volume":
            return (grouping, -_volume(w), w.priority)
        if strategy == "largest_footprint":
            return (grouping, -_footprint(w), w.priority)
        if strategy == "tallest_first":
            return (grouping, -_tallest(w), w.priority)
        if strategy == "highest_priority":
            return (w.priority, grouping, -_volume(w))
        if strategy == "group_and_size":
            return (w.group or "", -_volume(w), w.priority)
        if strategy == "system_and_size":
            return (w.system or "", -_volume(w), w.priority)
        if strategy == "longest_dimension":
            return (grouping, -_longest(w), w.priority)
        raise ValueError(f"Estrategia de cubicaje desconocida: {strategy}")

    return key
