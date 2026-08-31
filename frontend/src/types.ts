// CUBOX 2.0 - clasificacion generica del item (ver backend app/models/schemas.py).
// item_type=PANEL / orientation_policy=null reproduce el comportamiento
// legacy exacto de ventanas; ambos campos son opcionales aqui para no
// forzar a actualizar el resto del frontend en esta fase.
export type ItemType = "box" | "pallet" | "panel" | "custom";

export type OrientationPolicy = "free" | "upright" | "panel_edge_only" | "fixed";

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
  item_type?: ItemType;
  orientation_policy?: OrientationPolicy | null;
}

// CUBOX 2.0 - generalizacion de ContainerSpec (ver backend app/models/schemas.py).
export type LoadSpaceType = "container" | "truck" | "trailer" | "custom";

export type LoadingOpeningType = "rear" | "side" | "top" | "multiple";

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
  reason: string;
  reason_code: string;
  item_type?: ItemType;
  orientation_policy?: OrientationPolicy | null;
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

export interface PackingResult {
  container: ContainerSpec;
  placed: PlacedPiece[];
  unloaded: UnloadedItem[];
  metrics: PackingMetrics;
  load_sequence: string[];
  unload_sequence: string[];
  load_sequence_warnings: string[];
  reserved_zones: ReservedZone[];
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
