// CUBOX 2.0 - clasificacion generica del item (ver backend app/models/schemas.py).
// item_type=PANEL / orientation_policy=null reproduce el comportamiento
// legacy exacto de ventanas; ambos campos son opcionales aqui para no
// forzar a actualizar el resto del frontend en esta fase.
export type ItemType = "box" | "pallet" | "panel" | "custom";

export type OrientationPolicy = "free" | "upright" | "panel_edge_only" | "fixed";

// CUBOX 2.0 Fase 5C - dominio seguro de Tilt/Inclination (ver backend
// app/models/schemas.py:TILT_MAX_ANGLE_DEG). El backend es la autoridad
// (Field(le=...) + resolver); esta constante es solo para validar en el
// Wizard antes de enviar el request (seccion 15 del pedido).
export const TILT_MAX_ANGLE_DEG = 30;

// CUBOX 2.0 Fase 3A/3A.1 - representacion generica canonica (length/width/
// height); width/height/thickness siguen siendo los campos legacy que ya
// consume el resto del frontend, derivados de esta (ver backend
// app/models/schemas.py:legacy_from_dimensions).
export interface Dimensions3D {
  length: number;
  width: number;
  height: number;
}

export interface WindowItem {
  code: string;
  description: string;
  width: number;
  height: number;
  thickness: number;
  weight: number;
  quantity: number;
  system: string;
  group: string;
  stackable: boolean;
  priority: number;
  max_stack_weight: number | null;
  delivery_sequence: number | null;
  /** Informativo (trazabilidad/inventario, Fase 6.2): cuantas cajas/unidades
   * contiene este Load Unit (tipicamente un pallet). Nunca afecta packing. */
  boxes_inside?: number | null;
  item_type?: ItemType;
  orientation_policy?: OrientationPolicy | null;
  dimensions?: Dimensions3D;
  /** Fase 5B: valor CRUDO de Stackable tal como vino del Excel -None/undefined
   * = celda vacia (herencia del default del plan). Distinto de `stackable`
   * (ya materializado). Ver backend app/models/schemas.py:LoadItem. */
  stackable_override?: boolean | null;
  /** Fase 5B: igual idea que stackable_override, para Orientation. */
  orientation_override?: OrientationPolicy | null;
  /** Fase 5C-FINAL: si este item puede inclinarse (Tilt), ya resuelto del
   * plan. PLAN-LEVEL ONLY -no hay override de item ni columna de Excel (ver
   * backend app/core/handling_rules.py:resolve_plan_tilt). Solo tiene
   * efecto real para item_type="panel". */
  allow_tilt?: boolean;
  /** Fase 5C-FINAL: angulo MAXIMO de Tilt en grados (magnitud, sin signo),
   * ya resuelto del plan. null/undefined = sin Tilt definido. */
  max_tilt_angle?: number | null;
}

// CUBOX 2.0 - generalizacion de ContainerSpec (ver backend app/models/schemas.py).
export type LoadSpaceType = "container" | "truck" | "trailer" | "custom";

export type LoadingOpeningType = "rear" | "side" | "top" | "multiple";

// CUBOX 2.0 Fase 2B - distribucion de peso longitudinal (ver backend
// app/core/road_weight.py). Solo mirrors de tipos; sin componentes de UI.
export interface RoadSupport {
  id: string;
  name: string;
  position_x_mm: number;
  max_load_kg: number;
  baseline_load_kg: number;
}

export interface RoadWeightConfig {
  enabled: boolean;
  supports: RoadSupport[];
}

export interface ContainerSpec {
  id: string;
  name: string;
  length: number;
  width: number;
  height: number;
  max_weight: number;
  load_space_type?: LoadSpaceType;
  loading_opening_type?: LoadingOpeningType | null;
  rear_opening_width?: number | null;
  rear_opening_height?: number | null;
  road_weight_config?: RoadWeightConfig | null;
}

// Mismo shape que ContainerSpec, nombre generico para CUBOX 2.0.
export type LoadSpaceSpec = ContainerSpec;

export type OptimizationMode = "best_space" | "keep_groups" | "keep_systems";

export type ColorByMode = "default" | "group" | "system" | "priority";

export type WeightBalanceMode = "ignore" | "normal" | "important";

export type LoadingAnchor = "back_right" | "back_left";

export type SortReportBy = "group" | "system";

export type StepMode = "automatic" | "manual";

export type ReportDirection = "load" | "unload";

export interface ReportMetadata {
  projectName: string;
  customer: string;
}

export interface PlacedPiece {
  id: string;
  code: string;
  description: string;
  system: string;
  group: string;
  weight: number;
  stackable: boolean;
  priority: number;
  max_stack_weight: number | null;
  delivery_sequence: number | null;
  boxes_inside?: number | null;
  locked: boolean;
  x: number;
  y: number;
  z: number;
  dx: number;
  dy: number;
  dz: number;
  orientation_label: string;
  source_width: number;
  source_height: number;
  source_thickness: number;
  item_type?: ItemType;
  orientation_policy?: OrientationPolicy | null;
  source_dimensions?: Dimensions3D;
  /** Fase 5B: ver WindowItem.stackable_override -copiado tal cual del item de origen. */
  stackable_override?: boolean | null;
  orientation_override?: OrientationPolicy | null;
  /** Fase 5C-FINAL: ver WindowItem.allow_tilt/max_tilt_angle -PLAN-LEVEL
   * ONLY, ya resuelto (sin badge de Override: no existe override de item). */
  allow_tilt?: boolean;
  max_tilt_angle?: number | null;
  /** Fase 5C-FINAL: angulo de Tilt REALMENTE usado por esta pieza, SIGNED
   * (grados; positivo/negativo = direccion, 0 = sin inclinar) -el mismo
   * valor que debe renderizar Scene3D. */
  tilt_angle?: number;
  /** Fase 5C: 'x' o 'y' -eje horizontal de Thickness sobre el que se inclina
   * esta pieza en su orientacion actual (null = sin eje de Tilt aplicable). */
  tilt_axis?: "x" | "y" | null;
  /** Fase 5C: dx/dy/dz SIN Tilt (0 grados) para la orientacion actual -usados
   * para recalcular la geometria al pedir un nuevo angulo (ver /api/set-tilt). */
  base_dx?: number | null;
  base_dy?: number | null;
  base_dz?: number | null;
}

export interface UnloadedItem {
  id: string;
  code: string;
  description: string;
  width: number;
  height: number;
  thickness: number;
  weight: number;
  system: string;
  group: string;
  stackable: boolean;
  priority: number;
  max_stack_weight: number | null;
  delivery_sequence: number | null;
  boxes_inside?: number | null;
  reason: string;
  reason_code: string;
  item_type?: ItemType;
  orientation_policy?: OrientationPolicy | null;
  dimensions?: Dimensions3D;
  /** Fase 5B: ver WindowItem.stackable_override -copiado tal cual del item de origen. */
  stackable_override?: boolean | null;
  orientation_override?: OrientationPolicy | null;
  allow_tilt?: boolean;
  max_tilt_angle?: number | null;
}

export interface PackingMetrics {
  total_pieces: number;
  loaded_pieces: number;
  unloaded_pieces: number;
  used_volume_pct: number;
  total_weight: number;
  weight_utilization_pct: number;
  floor_utilization_pct: number;
  container_floor_area: number;
  used_floor_area: number;
  max_payload: number;
  number_of_groups: number;
  number_of_systems: number;
  weight_balance_pct: number;
  left_weight_kg: number;
  right_weight_kg: number;
  left_weight_pct: number;
  right_weight_pct: number;
  front_weight_kg: number;
  back_weight_kg: number;
  front_weight_pct: number;
  back_weight_pct: number;
  center_of_mass_x: number;
  center_of_mass_y: number;
  center_of_mass_z: number;
}

export interface ReservedZone {
  x: number;
  y: number;
  z: number;
  length: number;
  width: number;
  height: number;
  label: string;
}

export interface SupportLoadOut {
  id: string;
  name: string;
  position_x_mm: number;
  cargo_reaction_kg: number;
  baseline_load_kg: number;
  total_load_kg: number;
  max_load_kg: number;
  utilization_pct: number;
  overloaded: boolean;
  unstable: boolean;
}

export interface RoadWeightMetrics {
  load_center_x_mm: number | null;
  total_item_weight_kg: number;
  supports: SupportLoadOut[];
  valid: boolean;
  errors: string[];
}

export interface PackingResult {
  container: ContainerSpec;
  placed: PlacedPiece[];
  unloaded: UnloadedItem[];
  metrics: PackingMetrics;
  load_sequence: string[];
  unload_sequence: string[];
  load_sequence_warnings: string[];
  reserved_zones: ReservedZone[];
  road_weight?: RoadWeightMetrics | null;
}

export interface AlternativeSolution {
  strategy: string;
  score: number;
  breakdown: Record<string, number>;
  result: PackingResult;
}

export interface OptimizeResponse {
  best: PackingResult;
  alternatives: AlternativeSolution[];
}

export interface MoveValidationResult {
  valid: boolean;
  reason: string;
}

// CUBOX 2.0 Fase 3B - import Excel profile-aware (ver backend
// app/models/import_schemas.py). No relacionado con el importador legacy
// (WindowItem[] via /api/import-excel), que sigue igual.
export type ImportIssueSeverity = "error" | "warning";

export interface ImportIssue {
  row: number | null;
  column: string | null;
  code: string;
  message: string;
  severity: ImportIssueSeverity;
}

export interface ImportSummary {
  total_rows: number;
  valid_rows: number;
  invalid_rows: number;
  total_units: number;
  total_weight: number;
  unique_codes: number;
}

export interface ImportPreview {
  profile: ItemType;
  is_valid: boolean;
  items: WindowItem[];
  errors: ImportIssue[];
  warnings: ImportIssue[];
  summary: ImportSummary;
}

// CUBOX 2.0 Fase 5 - defaults del PLAN (Handling Rules del wizard) para el
// import profile-aware. Ver backend app/models/import_schemas.py:
// ImportDefaults. Precedencia: valor explicito de Excel > este default >
// default de sistema (nunca al reves).
export interface ImportDefaults {
  orientationPolicy?: OrientationPolicy;
  stackable?: boolean;
  // Fase 5C-FINAL: Tilt NO tiene equivalente aca -es PLAN-LEVEL ONLY, sin
  // columna de Excel (ver PlanHandlingRules.default_allow_tilt/max_tilt_angle).
}

// CUBOX 2.0 Fase 5B - defaults de HANDLING RULES a nivel de PLAN, enviados
// en CADA /api/pack y /api/optimize-remaining (ver backend
// app/models/schemas.py:PlanHandlingRules) para que el backend pueda
// re-resolver items "inherit" aunque el plan default haya cambiado despues
// del import. Todos los campos opcionales: omitir el objeto entero (o dejar
// un campo undefined) se comporta igual que antes de que esto existiera.
export interface PlanHandlingRules {
  default_stackable?: boolean | null;
  default_orientation_policy?: OrientationPolicy | null;
  default_max_stack_weight?: number | null;
  /** Fase 5C: solo tiene efecto real para Panels & Fragile (item_type="panel"). */
  default_allow_tilt?: boolean | null;
  default_max_tilt_angle?: number | null;
}

// Espacio de carga definido a mano (Truck/Trailer/Custom Container), tal
// como lo espera POST /api/pack en `custom_load_space` (ver backend
// app/models/schemas.py:CustomLoadSpaceRequest). No confundir con
// LoadSpaceSpec (la salida ya resuelta del backend).
export interface CustomLoadSpaceRequestBody {
  name: string;
  load_space_type: LoadSpaceType;
  length: number;
  width: number;
  height: number;
  max_weight: number;
  // Fase 5, seccion 26: el wizard todavia no tiene UI para configurar Road
  // Supports -queda undefined salvo que un paso futuro lo complete. El
  // backend ya lo soporta desde la Fase 2B (comportamiento normal si es None).
  road_weight_config?: RoadWeightConfig;
}

// CUBOX 2.0 Fase 5 - limite estructurado wizard -> workspace (App.tsx),
// reemplaza los 2 props sueltos initialItems/initialContainerId de la Fase
// 4. Un solo objeto opcional: si se omite, <App/> arranca exactamente
// igual que el flujo legacy (Open Legacy Workspace).
export interface InitialWorkspaceConfig {
  items: WindowItem[];
  loadSpace: { containerId: string } | { customLoadSpace: CustomLoadSpaceRequestBody };
  handlingRules: {
    enableCentralAisle: boolean;
    aisleWidthMm: number;
    clearanceMm: number;
    weightBalanceMode: WeightBalanceMode;
    loadingAnchor: LoadingAnchor;
    /** Fase 5B: defaults del plan elegidos en el wizard (Default Stackable /
     * Default Orientation), ahora si forwarded al workspace -antes se
     * consumian una sola vez en el import y se descartaban. */
    defaultStackable: boolean;
    orientationPolicy: OrientationPolicy;
    /** Fase 5C: defaults de Tilt elegidos en el wizard (solo aplican para
     * Panels & Fragile) -ver HandlingRulesStep.tsx. */
    defaultAllowTilt: boolean;
    defaultMaxTiltAngle: number | null;
  };
}
