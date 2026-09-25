"""Estrategias de orden para el motor de cubicaje (seccion 3 de V2).

Cada estrategia es solo una forma distinta de ORDENAR las piezas antes de
correr el mismo motor de colocacion de siempre (`packer.pack_container`). No
se reescribe el motor: se generaliza para que reciba el orden ya armado.

Escala de Priority: 1 = Highest ... 5 = Lowest (menor numero = mas prioritario).
Por eso el desempate por prioridad siempre ordena ASCENDENTE.

El Optimization Mode elegido por el usuario se mezcla como clave DOMINANTE
(agrupar por Group/System, o -Fase 6A.1- por Delivery Sequence) en TODAS las
estrategias, para que el usuario no tenga que elegir estrategia y modo por
separado. Delivery Sequence (_delivery_key) se evalua ANTES que Group/System
(_grouping_key) en la tupla de orden porque ambos modos son mutuamente
excluyentes (OptimizationMode es un unico valor a la vez) -el orden entre
ellos en la tupla nunca importa en la practica, solo se elige uno fijo.
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


def _delivery_key(w: WindowItem, optimization_mode: OptimizationMode) -> float:
    """Fase 6A.1: cuando el modo es PRIORITIZE_DELIVERY, ordena por Delivery
    Sequence DESCENDENTE -los numeros mas altos (entregas mas tardias) se
    procesan PRIMERO. El motor de colocacion llena el contenedor desde el
    fondo hacia la puerta (ver docstring de packer.pack_container: espejo de
    X -lo procesado primero termina en el fondo, lo procesado al final
    termina cerca de la puerta), asi que procesar primero los Delivery
    Sequence mas altos deja los mas bajos (entrega mas temprana) para el
    final, que es justo donde tienen que terminar: cerca de la puerta
    (seccion 3 del pedido de Fase 6A.1).

    Items sin Delivery Sequence se tratan como "lo mas tardio posible"
    (+inf -> se procesan primero, terminan en el fondo) para no robarle la
    posicion cercana a la puerta a items que SI tienen un destino conocido
    -mismo criterio que _NO_DELIVERY_SEQUENCE en core/sequence.py.

    Neutral (0.0, no cambia el orden entre items) para cualquier otro modo."""
    if optimization_mode != OptimizationMode.PRIORITIZE_DELIVERY:
        return 0.0
    effective = w.delivery_sequence if w.delivery_sequence is not None else float("inf")
    return -effective


def build_sort_key(strategy: str, optimization_mode: OptimizationMode) -> Callable:
    """Devuelve una funcion de orden para instancias con atributo `.source: WindowItem`."""

    def key(inst):
        w: WindowItem = inst.source
        delivery = _delivery_key(w, optimization_mode)
        grouping = _grouping_key(w, optimization_mode)

        if strategy == "largest_volume":
            return (delivery, grouping, -_volume(w), w.priority)
        if strategy == "largest_footprint":
            return (delivery, grouping, -_footprint(w), w.priority)
        if strategy == "tallest_first":
            return (delivery, grouping, -_tallest(w), w.priority)
        if strategy == "highest_priority":
            return (delivery, w.priority, grouping, -_volume(w))
        if strategy == "group_and_size":
            return (delivery, w.group or "", -_volume(w), w.priority)
        if strategy == "system_and_size":
            return (delivery, w.system or "", -_volume(w), w.priority)
        if strategy == "longest_dimension":
            return (delivery, grouping, -_longest(w), w.priority)
        raise ValueError(f"Estrategia de cubicaje desconocida: {strategy}")

    return key
