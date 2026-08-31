"""Modelos Pydantic: piezas de carga, contenedores y resultados de cubicaje."""

from enum import Enum

from pydantic import BaseModel, Field


class ItemType(str, Enum):
    """Clasificacion fisica del item (CUBOX 2.0). No confundir con los
    "Planning Modes" (Loose Boxes, Palletized Load, Build Pallets, Panels &
    Fragile, Custom Load) que son flujos de preparacion, no tipos de item."""

    BOX = "box"
    PALLET = "pallet"
    PANEL = "panel"
    CUSTOM = "custom"


class OrientationPolicy(str, Enum):
    """Que orientaciones son fisicamente validas para un item.

    Separada de ItemType a proposito: un BOX puede ser FREE o UPRIGHT segun
    reglas de manejo (fragilidad, "this side up"), no segun su tipo fisico.

    FREE            = las 6 orientaciones axis-aligned posibles.
    UPRIGHT         = la dimension `height` siempre vertical; solo rota 90
                       grados en el piso entre `width`/`thickness`.
    PANEL_EDGE_ONLY = regla critica actual de ventanas: la cara
                       Width x Height jamas puede ser la base (4 orientaciones).
    FIXED           = una sola orientacion, tal como se especifico el item.
    """

    FREE = "free"
    UPRIGHT = "upright"
    PANEL_EDGE_ONLY = "panel_edge_only"
    FIXED = "fixed"


DEFAULT_ORIENTATION_POLICY_BY_ITEM_TYPE: dict[ItemType, OrientationPolicy] = {
    ItemType.PANEL: OrientationPolicy.PANEL_EDGE_ONLY,
    ItemType.BOX: OrientationPolicy.FREE,
    ItemType.PALLET: OrientationPolicy.UPRIGHT,
    ItemType.CUSTOM: OrientationPolicy.FREE,
}


def resolve_orientation_policy(item_type: ItemType, orientation_policy: OrientationPolicy | None) -> OrientationPolicy:
    """orientation_policy explicito siempre gana; si no se especifico, se usa
    el default de item_type. Un WindowItem/legacy item (item_type=PANEL, sin
    orientation_policy) resuelve siempre a PANEL_EDGE_ONLY -comportamiento
    identico al de antes de que este campo existiera."""
    if orientation_policy is not None:
        return orientation_policy
    return DEFAULT_ORIENTATION_POLICY_BY_ITEM_TYPE[item_type]


class LoadItem(BaseModel):
    """Una fila de carga a planificar (una linea de producto, con cantidad).

    Generalizacion de lo que antes era WindowItem: mismos campos y mismo
    comportamiento por defecto (item_type=PANEL sin orientation_policy se
    comporta exactamente como una ventana), mas item_type/orientation_policy
    para representar otros tipos de carga. `WindowItem` es un alias de este
    modelo por compatibilidad -no se elimina ni se duplica logica."""

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
    item_type: ItemType = ItemType.PANEL
    orientation_policy: OrientationPolicy | None = Field(
        default=None, description="None = usar la politica por defecto de item_type"
    )

    @property
    def resolved_orientation_policy(self) -> OrientationPolicy:
        return resolve_orientation_policy(self.item_type, self.orientation_policy)


WindowItem = LoadItem


class LoadSpaceType(str, Enum):
    """Clasificacion del espacio de carga (CUBOX 2.0)."""

    CONTAINER = "container"
    TRUCK = "truck"
    TRAILER = "trailer"
    CUSTOM = "custom"


class LoadingOpeningType(str, Enum):
    """Por donde se accede al espacio de carga. Opcional: si no se conoce el
    dato real, se deja en None -nunca se inventa (ver LoadSpaceSpec)."""

    REAR = "rear"
    SIDE = "side"
    TOP = "top"
    MULTIPLE = "multiple"


class LoadSpaceSpec(BaseModel):
    """Espacio de carga generico: contenedor, camion, trailer o
    personalizado. Generalizacion de lo que antes era ContainerSpec -mismos
    campos y mismo comportamiento (ContainerSpec es un alias de este modelo,
    igual patron que WindowItem/LoadItem). Todo ContainerSpec existente
    sigue siendo valido: load_space_type default = CONTAINER.

    Punto de extension para Fase 2B (ejes/distribucion de peso de vehiculos
    de carretera): esos campos no existen todavia -se agregaran a este
    mismo modelo cuando corresponda, sin otro refactor destructivo."""

    id: str
    name: str
    load_space_type: LoadSpaceType = LoadSpaceType.CONTAINER
    length: float = Field(description="mm, eje X interno")
    width: float = Field(description="mm, eje Y interno")
    height: float = Field(description="mm, eje Z interno")
    max_weight: float = Field(description="kg, peso maximo de carga")
    loading_opening_type: LoadingOpeningType | None = Field(
        default=None, description="None = sin dato conocido; no se inventa"
    )
    rear_opening_width: float | None = Field(default=None, description="mm; None = sin dato conocido")
    rear_opening_height: float | None = Field(default=None, description="mm; None = sin dato conocido")


ContainerSpec = LoadSpaceSpec


class CustomLoadSpaceRequest(BaseModel):
    """Definicion de un espacio de carga hecha por el usuario (Truck,
    Trailer, Container o Custom con dimensiones propias). No se persiste en
    el catalogo -se resuelve a un LoadSpaceSpec ad-hoc por request (ver
    models/containers.py:build_custom_load_space); la persistencia real de
    espacios de carga se manejara en una fase separada."""

    name: str
    load_space_type: LoadSpaceType
    length: float = Field(gt=0, description="mm")
    width: float = Field(gt=0, description="mm")
    height: float = Field(gt=0, description="mm")
    max_weight: float = Field(gt=0, description="kg")


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
    item_type: ItemType = ItemType.PANEL
    orientation_policy: OrientationPolicy | None = None

    @property
    def resolved_orientation_policy(self) -> OrientationPolicy:
        return resolve_orientation_policy(self.item_type, self.orientation_policy)


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
    item_type: ItemType = ItemType.PANEL
    orientation_policy: OrientationPolicy | None = None

    @property
    def resolved_orientation_policy(self) -> OrientationPolicy:
        return resolve_orientation_policy(self.item_type, self.orientation_policy)


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
    """container_id (catalogo existente) y custom_load_space (Truck/Trailer/
    Container/Custom con dimensiones propias, sin catalogo) son mutuamente
    excluyentes -custom_load_space tiene prioridad si ambos llegan. Un
    request legacy que solo manda container_id sigue funcionando igual."""

    items: list[WindowItem]
    container_id: str | None = None
    custom_load_space: CustomLoadSpaceRequest | None = None
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
