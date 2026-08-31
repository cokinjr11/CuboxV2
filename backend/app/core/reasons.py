"""Codigos de motivo para piezas no cargadas (Unloaded Items).

`reason` (en UnloadedItem) sigue siendo el texto amigable ya existente desde
V1. `reason_code` es nuevo: un identificador estable para uso programatico
(filtrar, agrupar, o que un futuro cliente distinto lo traduzca).
"""

from enum import Enum


class UnloadedReason(str, Enum):
    NO_VALID_SPACE = "NO_VALID_SPACE"
    MAX_WEIGHT_EXCEEDED = "MAX_WEIGHT_EXCEEDED"
    ORIENTATION_CONFLICT = "ORIENTATION_CONFLICT"
    STACKING_CONFLICT = "STACKING_CONFLICT"
    RESERVED_ZONE_CONFLICT = "RESERVED_ZONE_CONFLICT"
    INSUFFICIENT_SUPPORT = "INSUFFICIENT_SUPPORT"
    CLEARANCE_CONFLICT = "CLEARANCE_CONFLICT"
    PRIORITY_DISPLACED = "PRIORITY_DISPLACED"
    MANUAL_REMOVE = "MANUAL_REMOVE"


REASON_TEXT: dict[str, str] = {
    UnloadedReason.NO_VALID_SPACE: "No se encontro espacio valido restante en el contenedor",
    UnloadedReason.MAX_WEIGHT_EXCEEDED: "Excede el peso maximo del contenedor",
    UnloadedReason.ORIENTATION_CONFLICT: "Ninguna orientacion valida cabe en el contenedor",
    UnloadedReason.STACKING_CONFLICT: "No se puede apilar sobre esa pieza",
    UnloadedReason.RESERVED_ZONE_CONFLICT: "Invade una zona reservada (por ejemplo, el pasillo central)",
    UnloadedReason.INSUFFICIENT_SUPPORT: "Soporte insuficiente para apilar en esa posicion",
    UnloadedReason.CLEARANCE_CONFLICT: "No respeta la separacion minima con otra pieza",
    UnloadedReason.PRIORITY_DISPLACED: "Desplazada por piezas de mayor prioridad",
    UnloadedReason.MANUAL_REMOVE: "Removido manualmente",
}
