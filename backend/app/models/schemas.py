"""Modelos Pydantic: piezas (ventanas), contenedores y resultados de cubicaje."""

from enum import Enum

from pydantic import BaseModel, Field


class WindowItem(BaseModel):
    """Una fila del Excel importado (una linea de producto, con cantidad)."""

    code: str
    description: str = ""
    width: float = Field(gt=0, description="mm")
    height: float = Field(gt=0, description="mm")
    thickness: float = Field(gt=0, description="mm")
    weight: float = Field(gt=0, description="kg, peso unitario")
    quantity: int = Field(gt=0)
    system: str = ""
    group: str = ""
    stackable: bool = True
    priority: int = 0
    max_stack_weight: float | None = Field(default=None, description="kg, None = sin limite")
    delivery_sequence: int | None = Field(default=None, description="orden de entrega/parada; None = sin definir")


class ContainerSpec(BaseModel):
    id: str
    name: str
    length: float = Field(description="mm, eje X interno")
    width: float = Field(description="mm, eje Y interno")
    height: float = Field(description="mm, eje Z interno")
    max_weight: float = Field(description="kg, peso maximo de carga")


class OptimizationMode(str, Enum):
    BEST_SPACE = "best_space"
    KEEP_GROUPS = "keep_groups"
    KEEP_SYSTEMS = "keep_systems"


class LoadingAnchor(str, Enum):
    """Esquina de inicio de la secuencia de carga (Anchored Loading
    Sequence): que pared lateral (ademas de la pared del fondo, siempre fija)
    se prioriza como punto de partida. BACK_RIGHT es el default."""

    BACK_RIGHT = "back_right"
    BACK_LEFT = "back_left"


class WeightBalanceMode(str, Enum):
    IGNORE = "ignore"
    NORMAL = "normal"
    IMPORTANT = "important"


class PlacedPiece(BaseModel):
    id: str
    code: str
    description: str = ""
    system: str = ""
    group: str = ""
    weight: float
    stackable: bool
    priority: int
    max_stack_weight: float | None = None
    delivery_sequence: int | None = None
    locked: bool = False
    x: float
    y: float
    z: float
    dx: float
    dy: float
    dz: float
    orientation_label: str
    source_width: float
    source_height: float
    source_thickness: float


class UnloadedItem(BaseModel):
    id: str
    code: str
    description: str = ""
    width: float
    height: float
    thickness: float
    weight: float
    system: str = ""
    group: str = ""
    stackable: bool = True
    priority: int = 0
    max_stack_weight: float | None = None
    delivery_sequence: int | None = None
    reason: str
    reason_code: str


class PackingMetrics(BaseModel):
    total_pieces: int
    loaded_pieces: int
    unloaded_pieces: int
    used_volume_pct: float
    total_weight: float
    weight_utilization_pct: float
    floor_utilization_pct: float
    container_floor_area: float
    used_floor_area: float
    max_payload: float
    number_of_groups: int
    number_of_systems: int
    weight_balance_pct: float
    left_weight_kg: float
    right_weight_kg: float
    left_weight_pct: float
    right_weight_pct: float
    front_weight_kg: float
    back_weight_kg: float
    front_weight_pct: float
    back_weight_pct: float
    center_of_mass_x: float
    center_of_mass_y: float
    center_of_mass_z: float


class ReservedZoneOut(BaseModel):
    """Version Pydantic (serializable) de core.reserved_zones.ReservedZone,
    solo para exponer la geometria de zonas reservadas (p.ej. el pasillo
    central) al frontend -single source of verdad, en vez de que el
    frontend recalcule el centrado por su cuenta."""

    x: float
    y: float
    z: float
    length: float
    width: float
    height: float
    label: str = "reserved"


class PackingResult(BaseModel):
    container: ContainerSpec
    placed: list[PlacedPiece]
    unloaded: list[UnloadedItem]
    metrics: PackingMetrics
    load_sequence: list[str] = []
    unload_sequence: list[str] = []
    load_sequence_warnings: list[str] = Field(
        default=[],
        description="Operational loadability warnings: piezas sin ninguna referencia fisica (piso/fondo/lateral/pieza ya cargada) en el punto en que la secuencia las carga.",
    )
    reserved_zones: list[ReservedZoneOut] = []


class AlternativeSolution(BaseModel):
    strategy: str
    score: float
    breakdown: dict[str, float] = {}
    result: PackingResult


class OptimizeResponse(BaseModel):
    best: PackingResult
    alternatives: list[AlternativeSolution]


class PackRequest(BaseModel):
    items: list[WindowItem]
    container_id: str
    optimization_mode: OptimizationMode = OptimizationMode.BEST_SPACE
    weight_balance_mode: WeightBalanceMode = WeightBalanceMode.NORMAL
    loading_anchor: LoadingAnchor = LoadingAnchor.BACK_RIGHT
    enable_central_aisle: bool = False
    aisle_width_mm: float = 500
    clearance_mm: float = 0


class MoveRequest(BaseModel):
    """Solicitud de mover manualmente una pieza ya colocada."""

    piece_id: str
    x: float
    y: float
    z: float
    dx: float
    dy: float
    dz: float


class MoveValidationResult(BaseModel):
    valid: bool
    reason: str = ""


class RemovePieceRequest(BaseModel):
    """Quitar manualmente una pieza colocada; pasa a Unloaded Items."""

    piece_id: str


class InsertPieceRequest(BaseModel):
    """Intentar colocar manualmente una pieza que esta en Unloaded Items."""

    unloaded_id: str
    x: float
    y: float
    z: float
    dx: float
    dy: float
    dz: float


class RotatePieceRequest(BaseModel):
    """Alternar una pieza colocada entre sus 2 orientaciones validas."""

    piece_id: str


class LockPieceRequest(BaseModel):
    """Bloquear una pieza para que Optimize Remaining no la mueva."""

    piece_id: str


class UnlockPieceRequest(BaseModel):
    piece_id: str


class OptimizeRemainingRequest(BaseModel):
    """Ambos campos opcionales: si se omiten, se reusa el ultimo optimization
    mode / weight balance mode guardado en el estado activo (comportamiento
    identico al de antes de que este request body existiera). Si se pasan,
    reflejan la seleccion actual de la UI para que Optimize Remaining (y
    Re-optimize cuando hay piezas Locked) realmente respete Keep Groups/Keep
    Systems/Weight Balance en vez de quedarse con lo que habia en el ultimo
    /api/pack."""

    optimization_mode: OptimizationMode | None = None
    weight_balance_mode: WeightBalanceMode | None = None
    loading_anchor: LoadingAnchor | None = None


class ReportMetadata(BaseModel):
    """Datos que no existen en ningun otro lado del modelo -solo tienen
    sentido al momento de exportar un reporte, asi que viven aca y no en
    PackingResult/PackingMetrics."""

    project_name: str = ""
    customer: str = ""


class SortReportBy(str, Enum):
    GROUP = "group"
    SYSTEM = "system"


class StepMode(str, Enum):
    AUTOMATIC = "automatic"
    MANUAL = "manual"


class ContainerReportRequest(BaseModel):
    meta: ReportMetadata = ReportMetadata()
    sort_by: SortReportBy = SortReportBy.GROUP
    include_overview_image: bool = True
    overview_image_png_base64: str | None = Field(
        default=None, description="PNG del snapshot 3D en base64; requerido si include_overview_image=True"
    )


class GuideReportRequest(BaseModel):
    meta: ReportMetadata = ReportMetadata()
    step_mode: StepMode = StepMode.AUTOMATIC
    pieces_per_step: int | None = Field(default=None, description="requerido si step_mode == MANUAL")
    step_images_png_base64: list[str] = Field(default_factory=list, description="un PNG por paso, en el mismo orden")


class ReportDirection(str, Enum):
    LOAD = "load"
    UNLOAD = "unload"


class ReportStepsRequest(BaseModel):
    direction: ReportDirection
    step_mode: StepMode = StepMode.AUTOMATIC
    pieces_per_step: int | None = Field(default=None, description="requerido si step_mode == MANUAL")


class ReportStepsResponse(BaseModel):
    steps: list[list[str]]


class ReportValidationResponse(BaseModel):
    valid: bool
    errors: list[str] = []
