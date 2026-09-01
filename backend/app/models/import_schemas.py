"""Esquemas de la Fase 3B: preview de importacion Excel CUBOX 2.0
(profile-aware, ver core/import_items.py). Separados de schemas.py porque
son especificos del flujo de import -no del dominio de cubicaje- y no
reemplazan nada de el: ImportPreview.items es simplemente list[LoadItem].
"""

from enum import Enum

from pydantic import BaseModel, Field

from app.models.schemas import ItemType, LoadItem


class ImportIssueSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


class ImportIssue(BaseModel):
    """Un problema puntual detectado al parsear el Excel.

    row=None indica un problema a nivel de archivo/columna (p.ej. una
    columna obligatoria ausente en el encabezado) en vez de una fila
    especifica de datos."""

    row: int | None = Field(default=None, description="None = problema a nivel de archivo/columna, no de una fila")
    column: str | None = None
    code: str
    message: str
    severity: ImportIssueSeverity


class ImportSummary(BaseModel):
    total_rows: int
    valid_rows: int
    invalid_rows: int
    total_units: int
    total_weight: float
    unique_codes: int


class ImportPreview(BaseModel):
    """Resultado de POST /api/import-items-excel. Solo parsea/valida -NO
    empaqueta, NO optimiza, NO reemplaza el resultado activo ni toca
    _current_state (eso lo decide un flujo posterior, "New Load Plan").

    is_valid=False si `errors` tiene al menos un elemento; los `warnings`
    nunca afectan is_valid. `items` puede contener filas parseadas
    correctamente aunque OTRAS filas del mismo archivo hayan fallado
    (import parcial: se reportan todos los errores juntos, no se detiene en
    la primera fila invalida)."""

    profile: ItemType
    is_valid: bool
    items: list[LoadItem]
    errors: list[ImportIssue] = []
    warnings: list[ImportIssue] = []
    summary: ImportSummary
