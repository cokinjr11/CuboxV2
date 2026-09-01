"""Modelos Pydantic: piezas de carga, contenedores y resultados de cubicaje."""

from enum import Enum

from pydantic import BaseModel, Field, model_validator


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


class Dimensions3D(BaseModel):
    """Representacion generica CANONICA de las 3 dimensiones fisicas de un
    item (CUBOX 2.0, Fase 3A). `length`/`width`/`height` son etiquetas
    neutras: su significado fisico (cual es "vertical", cual es "la cara")
    lo determina ItemType + OrientationPolicy (y, para PANEL,
    PanelDimensionMapping) en core/orientation.py -nunca una heuristica
    geometrica como min(length, width, height).

    Nunca se guarda como campo independiente en LoadItem/PlacedPiece/
    UnloadedItem: siempre se deriva en el momento (ver dimensions_from_legacy
    y las properties `dimensions`/`source_dimensions`) a partir de los
    campos legacy width/height/thickness, que siguen siendo la UNICA fuente
    de verdad almacenada y serializada. Asi no puede haber 2 representaciones
    independientes que se desincronicen."""

    length: float = Field(gt=0, description="mm")
    width: float = Field(gt=0, description="mm")
    height: float = Field(gt=0, description="mm")


def dimensions_from_legacy(width: float, height: float, thickness: float) -> Dimensions3D:
    """Mapeo LEGACY -> GENERICO (Fase 3A): fijo, posicional, y el MISMO para
    cualquier ItemType (no hay una version distinta para PANEL vs BOX).

        legacy.width     -> generic.length
        legacy.thickness -> generic.width
        legacy.height    -> generic.height

    No es una reinterpretacion fisica -es una correspondencia arbitraria
    pero deterministica entre 2 representaciones planas de 3 numeros. Los
    items genericos (BOX/PALLET/CUSTOM) usan estos 3 valores con su
    significado natural (UPRIGHT: `height` permanece vertical, igual
    criterio que ya se usaba desde la Fase 2B). Para PANEL,
    PANEL_DIMENSION_MAPPING reinterpreta estos mismos 3 valores para
    reproducir, sin ninguna heuristica, la regla original de vidrio (cara =
    legacy.width x legacy.height, thickness = legacy.thickness) -ver
    core/orientation.py."""
    return Dimensions3D(length=width, width=thickness, height=height)


class PanelDimensionMapping(BaseModel):
    """Que 2 ejes de Dimensions3D forman la cara grande (panel/vidrio) y
    cual es el eje de thickness -unico mecanismo para interpretar
    OrientationPolicy.PANEL_EDGE_ONLY sobre dimensiones genericas. Explicito
    a proposito (Fase 3A): jamas se infiere por heuristica (p.ej. "la
    dimension mas chica es el thickness")."""

    face_axes: tuple[str, str]
    thickness_axis: str


PANEL_DIMENSION_MAPPING = PanelDimensionMapping(face_axes=("length", "height"), thickness_axis="width")
"""Unico perfil de panel que existe hoy (ventanas legacy). Combinado con
dimensions_from_legacy(), reproduce EXACTAMENTE la regla original: cara =
legacy.width x legacy.height, thickness = legacy.thickness -ver
core/orientation.py:_panel_edge_only_orientations."""


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

    @property
    def dimensions(self) -> Dimensions3D:
        """Representacion generica canonica (Fase 3A), derivada en el
        momento -nunca almacenada- a partir de width/height/thickness."""
        return dimensions_from_legacy(self.width, self.height, self.thickness)


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


class RoadSupport(BaseModel):
    """Un punto/grupo de apoyo longitudinal de un vehiculo de carretera
    (p.ej. Front Axle Group, Rear Axle Group, o Kingpin/Trailer Axle Group en
    un semirremolque). Generico a proposito: el mismo modelo sirve para
    Truck y Trailer, sin dos motores de fisica distintos (ver
    core/road_weight.py)."""

    id: str
    name: str
    position_x_mm: float = Field(
        description="Posicion longitudinal del support en el mismo eje X que PlacedPiece.x "
        "(x=0 es la puerta/abertura de carga, x=LoadSpaceSpec.length es la pared del fondo)"
    )
    max_load_kg: float = Field(gt=0, description="Limite CONFIGURADO -nunca un valor legal/de fabricante asumido")
    baseline_load_kg: float = Field(default=0.0, ge=0, description="Carga del vehiculo ya presente en este support antes de la carga de items")


class RoadWeightConfig(BaseModel):
    """Configuracion opcional de distribucion de peso longitudinal (Fase 2B).
    Si enabled=False (o el campo es None en LoadSpaceSpec), no afecta en nada
    el empaque -comportamiento identico a Fase 2A. Cuando enabled=True, se
    exige exactamente 2 supports (modelo estatico de 2 apoyos, ver
    core/road_weight.py); mas de 2 requeriria un modelo de distribucion
    multi-eje que este modulo NO intenta adivinar."""

    enabled: bool = False
    supports: list[RoadSupport] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_supports(self) -> "RoadWeightConfig":
        if not self.enabled:
            return self
        if len(self.supports) != 2:
            raise ValueError(
                f"RoadWeightConfig.enabled requiere exactamente 2 supports (modelo estatico de 2 apoyos); "
                f"se recibieron {len(self.supports)}"
            )
        a, b = self.supports
        if abs(a.position_x_mm - b.position_x_mm) < 1e-6:
            raise ValueError("RoadWeightConfig: los 2 supports no pueden tener la misma position_x_mm")
        return self


class LoadSpaceSpec(BaseModel):
    """Espacio de carga generico: contenedor, camion, trailer o
    personalizado. Generalizacion de lo que antes era ContainerSpec -mismos
    campos y mismo comportamiento (ContainerSpec es un alias de este modelo,
    igual patron que WindowItem/LoadItem). Todo ContainerSpec existente
    sigue siendo valido: load_space_type default = CONTAINER,
    road_weight_config default = None (sin efecto en el empaque)."""

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
    road_weight_config: RoadWeightConfig | None = Field(
        default=None, description="None = sin distribucion de peso longitudinal (comportamiento Fase 2A)"
    )


ContainerSpec = LoadSpaceSpec


class SupportLoadOut(BaseModel):
    """Metricas de un RoadSupport para un cubicaje dado (ver core/road_weight.py:evaluate_road_weight)."""

    id: str
    name: str
    position_x_mm: float
    cargo_reaction_kg: float
    baseline_load_kg: float
    total_load_kg: float
    max_load_kg: float
    utilization_pct: float
    overloaded: bool
    unstable: bool = Field(description="True si la reaccion calculada es negativa mas alla de la tolerancia numerica")


class RoadWeightMetrics(BaseModel):
    """Resultado de evaluar RoadWeightConfig contra un conjunto de piezas
    cargadas. None en PackingResult cuando el LoadSpace no tiene
    road_weight_config habilitado."""

    load_center_x_mm: float | None = Field(default=None, description="Centro de carga longitudinal; None si no hay piezas cargadas")
    total_item_weight_kg: float
    supports: list[SupportLoadOut]
    valid: bool
    errors: list[str] = []


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
    road_weight_config: RoadWeightConfig | None = Field(
        default=None, description="Fase 2B: distribucion de peso longitudinal, opcional"
    )


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

    @property
    def source_dimensions(self) -> Dimensions3D:
        """Representacion generica canonica (Fase 3A) de las dimensiones de
        ORIGEN (no de la caja ya orientada dx/dy/dz), derivada en el momento
        a partir de source_width/source_height/source_thickness."""
        return dimensions_from_legacy(self.source_width, self.source_height, self.source_thickness)


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
    def dimensions(self) -> Dimensions3D:
        return dimensions_from_legacy(self.width, self.height, self.thickness)

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
    road_weight: RoadWeightMetrics | None = Field(
        default=None, description="None si el LoadSpace no tiene RoadWeightConfig habilitado (Fase 2A/legacy)"
    )


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
