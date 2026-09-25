"""Integracion NAGSA, A2: errores de negocio de la capa services/.

Unica responsabilidad: describir POR QUE una operacion sobre un plan no se
puede hacer, sin saber nada de HTTP. La traduccion a codigo HTTP vive en un
solo lugar (api/error_mapping.py, A3) y reproduce exactamente los codigos que
api/routes.py devolvia antes (400/404/409/422)."""

from __future__ import annotations

from enum import Enum


class PlanOpErrorKind(str, Enum):
    INVALID_INPUT = "invalid_input"
    """La peticion es incompleta o incoherente (hoy 400)."""
    NOT_FOUND = "not_found"
    """La pieza / Load Space / Group pedido no existe (hoy 404)."""
    CONFLICT = "conflict"
    """La operacion violaria una regla fisica u operativa, o la pieza esta
    Locked (hoy 409)."""
    NOT_EXPORTABLE = "not_exportable"
    """Plan NOT_READY y el export no fue explicitamente permitido (hoy 422)."""


class PlanOpError(Exception):
    """`detail` es texto, salvo NOT_EXPORTABLE que conserva el dict
    {"errors", "status"} que ya consume el frontend."""

    def __init__(self, kind: PlanOpErrorKind, detail: str | dict):
        self.kind = kind
        self.detail = detail
        super().__init__(detail if isinstance(detail, str) else str(detail))


def invalid_input(detail: str) -> PlanOpError:
    return PlanOpError(PlanOpErrorKind.INVALID_INPUT, detail)


def not_found(detail: str) -> PlanOpError:
    return PlanOpError(PlanOpErrorKind.NOT_FOUND, detail)


def conflict(detail: str) -> PlanOpError:
    return PlanOpError(PlanOpErrorKind.CONFLICT, detail)
