"""Fase 5D: repositorio SQLite para Load Plans persistentes.

`api/routes.py:_current_state` sigue siendo el estado de sesion EN MEMORIA
-transitorio, se resetea con cada `/api/pack` y no sobrevive un reinicio del
proceso (ver su propio docstring). Este modulo es la fuente de verdad
PERSISTENTE: cada Load Plan guardado vive como una fila en la tabla `plans`
de un archivo SQLite (`backend/data/cubox.db` por defecto), sobrevive
reinicios del backend, y es lo que alimenta Recent Plans en el Home.

Deliberadamente NO se normaliza item por item / colocacion por colocacion en
columnas SQL separadas (seccion 31 del pedido de Fase 5D): el estado
canonico completo (piezas colocadas, no colocadas, Load Space, Handling
Rules, configuracion activa) se guarda como un unico blob JSON
(`state_json`) -evita duplicar en SQL el esquema que ya vive en
models/schemas.py, y mantiene una sola fuente de verdad para la FORMA de
los datos. Las columnas de resumen (nombre, contadores, fechas) existen
SOLO para que listar Recent Plans sea barato (no hay que parsear el JSON de
cada plan guardado solo para pintar una tarjeta).

Este modulo es deliberadamente ajeno a Pydantic/schemas.py -solo conoce
strings/numeros/JSON crudo. La traduccion hacia/desde los modelos de
dominio (PlacedPiece, LoadSpaceSpec, etc.) vive en core/plan_service.py.
Esto es lo que permite, segun la seccion 4 del pedido, reemplazar SQLite por
otro motor mas adelante sin tocar la logica de dominio: solo esta capa
cambiaria.

Sin ORM (seccion 4: no se justifica la complejidad todavia, una sola tabla).
Cada metodo abre y cierra su propia conexion sqlite3 -SQLite tolera esto
bien para el volumen de esta fase (un solo usuario local, sin
concurrencia real)- y usa `with conn:` para que cada escritura sea una
transaccion atomica (seccion 32 del pedido: commit automatico si no hay
excepcion, rollback automatico si la hay)."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

# Integracion NAGSA, A1: la version y los errores del FORMATO de state_json
# viven en core/plan_schema.py (el modo integrado /api/v1 los necesita sin
# SQLite). Se re-exportan aca con los mismos nombres por compatibilidad.
from app.core.plan_schema import SCHEMA_VERSION, CorruptPlanStateError, UnsupportedSchemaVersionError

__all__ = [
    "SCHEMA_VERSION",
    "CorruptPlanStateError",
    "UnsupportedSchemaVersionError",
    "DEFAULT_DB_PATH",
    "PlanNotFoundError",
    "PlanRow",
    "PlanRepository",
]

DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "cubox.db"
"""backend/data/cubox.db -ver seccion 5 del pedido. Nunca se commitea a Git
(ver .gitignore); se crea sola en el primer uso (PlanRepository.__init__)."""


class PlanNotFoundError(Exception):
    """No existe ninguna fila `plans` con ese plan_id."""

    def __init__(self, plan_id: str):
        self.plan_id = plan_id
        super().__init__(f"Load Plan no encontrado: {plan_id}")


@dataclass
class PlanRow:
    """Fila cruda de la tabla `plans` -sin ningun conocimiento de Pydantic.
    `state_json` es un string opaco para este modulo; solo plan_service.py
    sabe parsearlo/construirlo."""

    plan_id: str
    name: str
    created_at: str
    updated_at: str
    schema_version: int
    load_type: str
    load_space_name: str
    total_items: int
    loaded_items: int
    unloaded_items: int
    state_json: str

    @staticmethod
    def from_sqlite_row(row: sqlite3.Row) -> "PlanRow":
        return PlanRow(
            plan_id=row["plan_id"],
            name=row["name"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            schema_version=row["schema_version"],
            load_type=row["load_type"],
            load_space_name=row["load_space_name"],
            total_items=row["total_items"],
            loaded_items=row["loaded_items"],
            unloaded_items=row["unloaded_items"],
            state_json=row["state_json"],
        )


class PlanRepository:
    """Capa de persistencia para Load Plans -API -> Plan Service ->
    PlanRepository -> SQLite (seccion 4 del pedido). Cada instancia apunta a
    un archivo SQLite propio (`db_path`); los tests usan una ruta temporal
    en vez de `DEFAULT_DB_PATH` (seccion 50: nunca correr tests contra la
    base de datos real del usuario)."""

    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        if str(self.db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()  # seccion 32 del pedido: cada escritura es atomica -commit solo si no hubo excepcion
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS plans (
                    plan_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    schema_version INTEGER NOT NULL,
                    load_type TEXT NOT NULL,
                    load_space_name TEXT NOT NULL,
                    total_items INTEGER NOT NULL,
                    loaded_items INTEGER NOT NULL,
                    unloaded_items INTEGER NOT NULL,
                    state_json TEXT NOT NULL
                )
                """
            )

    def create(self, row: PlanRow) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO plans
                       (plan_id, name, created_at, updated_at, schema_version, load_type, load_space_name,
                        total_items, loaded_items, unloaded_items, state_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    row.plan_id, row.name, row.created_at, row.updated_at, row.schema_version,
                    row.load_type, row.load_space_name, row.total_items, row.loaded_items,
                    row.unloaded_items, row.state_json,
                ),
            )

    def update_state(
        self,
        plan_id: str,
        *,
        updated_at: str,
        schema_version: int,
        load_type: str,
        load_space_name: str,
        total_items: int,
        loaded_items: int,
        unloaded_items: int,
        state_json: str,
    ) -> None:
        """Autosave (PUT /api/plans/{id}): actualiza SOLO el estado y las
        columnas de resumen -deliberadamente no toca `name`/`created_at`,
        para que un autosave concurrente con un rename nunca pueda revertir
        el nombre (cada uno es su propia escritura atomica, seccion 32)."""
        with self._connect() as conn:
            cur = conn.execute(
                """UPDATE plans
                   SET updated_at=?, schema_version=?, load_type=?, load_space_name=?,
                       total_items=?, loaded_items=?, unloaded_items=?, state_json=?
                   WHERE plan_id=?""",
                (updated_at, schema_version, load_type, load_space_name, total_items, loaded_items, unloaded_items, state_json, plan_id),
            )
            if cur.rowcount == 0:
                raise PlanNotFoundError(plan_id)

    def rename(self, plan_id: str, name: str, updated_at: str) -> None:
        with self._connect() as conn:
            cur = conn.execute("UPDATE plans SET name=?, updated_at=? WHERE plan_id=?", (name, updated_at, plan_id))
            if cur.rowcount == 0:
                raise PlanNotFoundError(plan_id)

    def delete(self, plan_id: str) -> None:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM plans WHERE plan_id=?", (plan_id,))
            if cur.rowcount == 0:
                raise PlanNotFoundError(plan_id)

    def get(self, plan_id: str) -> PlanRow:
        with self._connect() as conn:
            cur = conn.execute("SELECT * FROM plans WHERE plan_id=?", (plan_id,))
            row = cur.fetchone()
        if row is None:
            raise PlanNotFoundError(plan_id)
        return PlanRow.from_sqlite_row(row)

    def list_recent(self, limit: int = 10) -> list[PlanRow]:
        """Ordenado por updated_at DESC (seccion 10 del pedido: mas
        recientemente modificado primero)."""
        with self._connect() as conn:
            cur = conn.execute("SELECT * FROM plans ORDER BY updated_at DESC LIMIT ?", (limit,))
            rows = cur.fetchall()
        return [PlanRow.from_sqlite_row(r) for r in rows]
