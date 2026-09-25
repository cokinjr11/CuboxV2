"""Integracion NAGSA, A1: version y errores del FORMATO del estado de un Load
Plan (el JSON que hoy se guarda en `state_json` y que, desde el modo
integrado `/api/v1`, tambien viaja en cada request).

Antes vivian en core/plan_store.py, pero no son un concepto de SQLite: el
modo integrado necesita validar la version del estado sin que exista ninguna
base de datos. plan_store.py los re-exporta con los mismos nombres
(compatibilidad con `from app.core.plan_store import SCHEMA_VERSION`).

Modulo deliberadamente sin dependencias -ni Pydantic ni sqlite3- para que
tanto la capa de persistencia como services/ puedan importarlo sin crear
dependencias cruzadas."""

SCHEMA_VERSION = 1
"""Fase 5D, seccion 30/42 del pedido: version del ESQUEMA de `state_json`
(no de la tabla SQL). GET /api/plans/{id} rechaza limpiamente (409, sin
crashear) cualquier fila cuyo schema_version no sea este -ver
plan_service.py:parse_plan_state. Sin migraciones automaticas todavia (solo
existe la version 1); agregar una version 2 en el futuro implica escribir
un migrador explicito, nunca reinterpretar en silencio datos de un esquema
distinto."""


class UnsupportedSchemaVersionError(Exception):
    """El plan persistido usa un schema_version que esta version de Cubox no
    sabe interpretar (seccion 42 del pedido)."""

    def __init__(self, found: int):
        self.found = found
        super().__init__(f"schema_version {found} no soportado (esperado {SCHEMA_VERSION})")


class CorruptPlanStateError(Exception):
    """El JSON persistido no es valido o no matchea los schemas de Pydantic
    actuales (seccion 41 del pedido: nunca confiar ciegamente en el JSON
    guardado)."""
