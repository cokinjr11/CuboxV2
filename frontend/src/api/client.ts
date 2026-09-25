import axios from "axios";
import type {
  AlternativeSolution,
  ContainerSpec,
  CustomLoadSpaceRequestBody,
  ImportDefaults,
  ImportPreview,
  ItemType,
  LoadingAnchor,
  LoadSpaceSpec,
  MoveValidationResult,
  OptimizationMode,
  OptimizeResponse,
  PackingResult,
  PlanDetail,
  PlanHandlingRules,
  PlanSummary,
  ReportDirection,
  ReportMetadata,
  ReportStepsResult,
  ReportValidationResult,
  SortReportBy,
  StepMode,
  WeightBalanceMode,
  WindowItem,
} from "../types";

const API_BASE = "http://localhost:8000/api";

export const api = axios.create({ baseURL: API_BASE });

export async function fetchContainers(): Promise<ContainerSpec[]> {
  const r = await api.get<ContainerSpec[]>("/containers");
  return r.data;
}

export async function importExcel(file: File): Promise<WindowItem[]> {
  const form = new FormData();
  form.append("file", file);
  const r = await api.post<WindowItem[]>("/import-excel", form, {
    headers: { "Content-Type": "multipart/form-data" },
  });
  return r.data;
}

// CUBOX 2.0 Fase 4 - Load Space generico (containers hoy; Truck/Trailer sin
// presets todavia, ver backend app/models/containers.py).
export async function fetchLoadSpaces(): Promise<LoadSpaceSpec[]> {
  const r = await api.get<LoadSpaceSpec[]>("/load-spaces");
  return r.data;
}

// Import Excel profile-aware (Fase 3B). No reemplaza importExcel/import-excel
// (legacy) -son 2 flujos independientes. `defaults` (Fase 5) son los
// defaults del PLAN (Handling Rules) -el backend solo los aplica cuando la
// celda de Excel viene vacia, nunca pisan un valor explicito.
export async function importItemsExcel(file: File, profile: ItemType, defaults?: ImportDefaults): Promise<ImportPreview> {
  const form = new FormData();
  form.append("file", file);
  form.append("profile", profile);
  if (defaults?.orientationPolicy) form.append("default_orientation_policy", defaults.orientationPolicy);
  if (defaults?.stackable !== undefined) form.append("default_stackable", String(defaults.stackable));
  // Fase 5C-FINAL: Tilt es PLAN-LEVEL ONLY, sin equivalente de import default.
  const r = await api.post<ImportPreview>("/import-items-excel", form, {
    headers: { "Content-Type": "multipart/form-data" },
  });
  return r.data;
}

export async function downloadImportTemplate(profile: ItemType): Promise<Blob> {
  const r = await api.get(`/import-template/${profile}`, { responseType: "blob" });
  return r.data;
}

export interface PackOptions {
  optimizationMode: OptimizationMode;
  enableCentralAisle: boolean;
  aisleWidthMm: number;
  clearanceMm: number;
  weightBalanceMode: WeightBalanceMode;
  loadingAnchor: LoadingAnchor;
  /** Fase 5B: defaults del plan (Default Stackable / Default Orientation),
   * reenviados en CADA pack para que items "inherit" (sin override propio)
   * se resuelvan con el default ACTUAL, aunque haya cambiado desde el import. */
  planHandlingRules?: PlanHandlingRules;
}

// CUBOX 2.0 Fase 5: el Load Space a empaquetar es o un preset del catalogo
// (container_id, p.ej. "40ft_high_cube" -comportamiento legacy sin
// cambios) o un espacio definido a mano (custom_load_space: Truck/Trailer/
// Custom Container, ya soportado por el backend desde la Fase 2A). Nunca
// ambos a la vez -ver backend app/models/schemas.py:PackRequest.
export type PackLoadSpace = { containerId: string } | { customLoadSpace: CustomLoadSpaceRequestBody };

export async function packContainer(
  items: WindowItem[],
  loadSpace: PackLoadSpace,
  options: PackOptions
): Promise<OptimizeResponse> {
  const r = await api.post<OptimizeResponse>("/pack", {
    items,
    container_id: "containerId" in loadSpace ? loadSpace.containerId : undefined,
    custom_load_space: "customLoadSpace" in loadSpace ? loadSpace.customLoadSpace : undefined,
    optimization_mode: options.optimizationMode,
    enable_central_aisle: options.enableCentralAisle,
    aisle_width_mm: options.aisleWidthMm,
    clearance_mm: options.clearanceMm,
    weight_balance_mode: options.weightBalanceMode,
    loading_anchor: options.loadingAnchor,
    plan_handling_rules: options.planHandlingRules,
  });
  return r.data;
}

export async function optimizeRemaining(options: {
  optimizationMode: OptimizationMode;
  weightBalanceMode: WeightBalanceMode;
  loadingAnchor: LoadingAnchor;
  planHandlingRules?: PlanHandlingRules;
}): Promise<OptimizeResponse> {
  const r = await api.post<OptimizeResponse>("/optimize-remaining", {
    optimization_mode: options.optimizationMode,
    weight_balance_mode: options.weightBalanceMode,
    loading_anchor: options.loadingAnchor,
    plan_handling_rules: options.planHandlingRules,
  });
  return r.data;
}

export async function exportExcel(allowExportWithErrors = false): Promise<Blob> {
  const r = await api.get("/export-excel", {
    responseType: "blob",
    params: { allow_export_with_errors: allowExportWithErrors },
  });
  return r.data;
}

// Fase 6B.1, seccion 11-17 del pedido: chequeo previo (fail-fast) antes de
// arrancar el loop de capturas de un reporte -reusa /report/validate (ya
// existia en el backend, nunca se habia consumido desde el frontend).
export async function validateReport(): Promise<ReportValidationResult> {
  const r = await api.post<ReportValidationResult>("/report/validate");
  return r.data;
}

export interface ContainerReportOptions {
  meta: ReportMetadata;
  sortBy: SortReportBy;
  includeOverviewImage: boolean;
  overviewImagePngBase64?: string;
  allowExportWithErrors?: boolean;
}

export async function exportContainerReportPdf(options: ContainerReportOptions): Promise<Blob> {
  const r = await api.post(
    "/report/container-pdf",
    {
      meta: { project_name: options.meta.projectName, customer: options.meta.customer },
      sort_by: options.sortBy,
      include_overview_image: options.includeOverviewImage,
      overview_image_png_base64: options.overviewImagePngBase64 ?? null,
      allow_export_with_errors: options.allowExportWithErrors ?? false,
    },
    { responseType: "blob" }
  );
  return r.data;
}

export interface StepModeOptions {
  stepMode: StepMode;
  piecesPerStep?: number;
}

export async function getReportSteps(direction: ReportDirection, options: StepModeOptions): Promise<ReportStepsResult> {
  const r = await api.post<ReportStepsResult>("/report/steps", {
    direction,
    step_mode: options.stepMode,
    pieces_per_step: options.piecesPerStep ?? null,
  });
  return r.data;
}

export interface GuideReportOptions extends StepModeOptions {
  meta: ReportMetadata;
  stepImagesPngBase64: string[];
  allowExportWithErrors?: boolean;
}

function guideReportBody(options: GuideReportOptions) {
  return {
    meta: { project_name: options.meta.projectName, customer: options.meta.customer },
    step_mode: options.stepMode,
    pieces_per_step: options.piecesPerStep ?? null,
    step_images_png_base64: options.stepImagesPngBase64,
    allow_export_with_errors: options.allowExportWithErrors ?? false,
  };
}

export async function exportLoadingGuidePdf(options: GuideReportOptions): Promise<Blob> {
  const r = await api.post("/report/loading-guide-pdf", guideReportBody(options), { responseType: "blob" });
  return r.data;
}

export async function exportUnloadingGuidePdf(options: GuideReportOptions): Promise<Blob> {
  const r = await api.post("/report/unloading-guide-pdf", guideReportBody(options), { responseType: "blob" });
  return r.data;
}

export interface GuideReportByGroupOptions extends GuideReportOptions {
  groups: string[];
}

// Load Organization Model Cleanup: 1 Group seleccionado -> respuesta es un
// PDF suelto (mismo Content-Type que exportUnloadingGuidePdf). 2+ Groups ->
// ZIP (un PDF por Group, nunca fusionados). El caller decide el nombre de
// archivo a partir del content-type de la respuesta (ver handleGenerateReport
// en App.tsx).
export async function exportUnloadingGuidePdfByGroup(
  options: GuideReportByGroupOptions
): Promise<{ blob: Blob; isZip: boolean }> {
  const r = await api.post(
    "/report/unloading-guide-pdf-by-group",
    { ...guideReportBody(options), groups: options.groups },
    { responseType: "blob" }
  );
  const contentType = (r.headers["content-type"] as string | undefined) ?? "";
  return { blob: r.data, isZip: contentType.includes("zip") };
}

export async function validateMove(
  pieceId: string,
  x: number,
  y: number,
  z: number,
  dx: number,
  dy: number,
  dz: number
): Promise<MoveValidationResult> {
  const r = await api.post<MoveValidationResult>("/validate-move", {
    piece_id: pieceId,
    x,
    y,
    z,
    dx,
    dy,
    dz,
  });
  return r.data;
}

export async function applyMove(
  pieceId: string,
  x: number,
  y: number,
  z: number,
  dx: number,
  dy: number,
  dz: number
): Promise<PackingResult> {
  const r = await api.post<PackingResult>("/apply-move", {
    piece_id: pieceId,
    x,
    y,
    z,
    dx,
    dy,
    dz,
  });
  return r.data;
}

export async function removePiece(pieceId: string): Promise<PackingResult> {
  const r = await api.post<PackingResult>("/remove-piece", { piece_id: pieceId });
  return r.data;
}

export async function insertPiece(
  unloadedId: string,
  x: number,
  y: number,
  z: number,
  dx: number,
  dy: number,
  dz: number
): Promise<PackingResult> {
  const r = await api.post<PackingResult>("/insert-piece", {
    unloaded_id: unloadedId,
    x,
    y,
    z,
    dx,
    dy,
    dz,
  });
  return r.data;
}

export async function rotatePiece(pieceId: string): Promise<PackingResult> {
  const r = await api.post<PackingResult>("/rotate-piece", { piece_id: pieceId });
  return r.data;
}

export async function turnPiece(pieceId: string): Promise<PackingResult> {
  const r = await api.post<PackingResult>("/turn-piece", { piece_id: pieceId });
  return r.data;
}

// Fase 5C: cambiar el angulo de Tilt de una pieza ya colocada. El backend
// (core/manual_move.py:validate_tilt_change) es la unica fuente de verdad -
// rechaza (409) si el angulo excede el maximo efectivo, la pieza no admite
// Tilt, o la nueva geometria deja de ser valida (colision/soporte/limites).
export async function setTilt(pieceId: string, tiltAngle: number): Promise<PackingResult> {
  const r = await api.post<PackingResult>("/set-tilt", { piece_id: pieceId, tilt_angle: tiltAngle });
  return r.data;
}

export async function lockPiece(pieceId: string): Promise<PackingResult> {
  const r = await api.post<PackingResult>("/lock-piece", { piece_id: pieceId });
  return r.data;
}

export async function unlockPiece(pieceId: string): Promise<PackingResult> {
  const r = await api.post<PackingResult>("/unlock-piece", { piece_id: pieceId });
  return r.data;
}

export async function undo(): Promise<PackingResult> {
  const r = await api.post<PackingResult>("/undo");
  return r.data;
}

export async function redo(): Promise<PackingResult> {
  const r = await api.post<PackingResult>("/redo");
  return r.data;
}

// ---------------------------------------------------------------------------
// Fase 5D: Recent Plans & Persistence.
// ---------------------------------------------------------------------------

export interface CreatePlanResult {
  planId: string;
  name: string;
  best: PackingResult;
  alternatives: AlternativeSolution[];
}

/** Corre exactamente la misma optimizacion que packContainer (mismo body)
 * pero ademas crea un Load Plan PERSISTENTE -el plan recibe un plan_id en
 * este momento (seccion 12 del pedido: "Create Load Plan" es lo primero
 * que hace un plan real de un borrador de wizard). `name` es opcional -el
 * backend genera un nombre por defecto si se omite. */
export async function createPlan(
  items: WindowItem[],
  loadSpace: PackLoadSpace,
  options: PackOptions,
  name?: string
): Promise<CreatePlanResult> {
  const r = await api.post<{ plan_id: string; name: string; best: PackingResult; alternatives: AlternativeSolution[] }>("/plans", {
    items,
    container_id: "containerId" in loadSpace ? loadSpace.containerId : undefined,
    custom_load_space: "customLoadSpace" in loadSpace ? loadSpace.customLoadSpace : undefined,
    optimization_mode: options.optimizationMode,
    enable_central_aisle: options.enableCentralAisle,
    aisle_width_mm: options.aisleWidthMm,
    clearance_mm: options.clearanceMm,
    weight_balance_mode: options.weightBalanceMode,
    loading_anchor: options.loadingAnchor,
    plan_handling_rules: options.planHandlingRules,
    name: name ?? null,
  });
  return { planId: r.data.plan_id, name: r.data.name, best: r.data.best, alternatives: r.data.alternatives };
}

/** Recent Plans en el Home (seccion 9/10/38 del pedido): liviano, ordenado
 * por updated_at DESC -nunca placed/unloaded completos. */
export async function listRecentPlans(limit = 10): Promise<PlanSummary[]> {
  const r = await api.get<PlanSummary[]>("/plans", { params: { limit } });
  return r.data;
}

/** Abre un plan guardado -el backend reconstruye su sesion activa desde
 * cero (seccion 13/14 del pedido: SIN volver a correr el optimizador). */
export async function getPlan(planId: string): Promise<PlanDetail> {
  const r = await api.get<PlanDetail>(`/plans/${planId}`);
  return r.data;
}

/** Autosave (seccion 24/25 del pedido; `planHandlingRules` es la
 * correccion final, seccion 1/2 del pedido). Sin `planHandlingRules`: el
 * backend guarda cualquiera sea el estado activo actual (ya sincronizado
 * por cada mutacion anterior: apply-move/rotate/turn/tilt/lock/insert/
 * remove/optimize-remaining, ver api/routes.py). Con `planHandlingRules`:
 * ademas aplica esa configuracion a la sesion activa ANTES de guardar -sin
 * tocar placed/unloaded ni volver a correr el optimizador- para que un
 * cambio de Handling Rule se persista aunque el usuario nunca vuelva a
 * pulsar Optimize. */
export async function savePlan(planId: string, planHandlingRules?: PlanHandlingRules): Promise<PlanSummary> {
  const r = await api.put<PlanSummary>(
    `/plans/${planId}`,
    planHandlingRules !== undefined ? { plan_handling_rules: planHandlingRules } : undefined
  );
  return r.data;
}

export async function renamePlan(planId: string, name: string): Promise<PlanSummary> {
  const r = await api.patch<PlanSummary>(`/plans/${planId}`, { name });
  return r.data;
}

export async function deletePlan(planId: string): Promise<void> {
  await api.delete(`/plans/${planId}`);
}
