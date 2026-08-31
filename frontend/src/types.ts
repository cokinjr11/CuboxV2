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
}

export interface ContainerSpec {
  id: string;
  name: string;
  length: number;
  width: number;
  height: number;
  max_weight: number;
}

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
