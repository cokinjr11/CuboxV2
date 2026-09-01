import axios from "axios";
import type {
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
  ReportDirection,
  ReportMetadata,
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
  });
  return r.data;
}

export async function optimizeRemaining(options: {
  optimizationMode: OptimizationMode;
  weightBalanceMode: WeightBalanceMode;
  loadingAnchor: LoadingAnchor;
}): Promise<OptimizeResponse> {
  const r = await api.post<OptimizeResponse>("/optimize-remaining", {
    optimization_mode: options.optimizationMode,
    weight_balance_mode: options.weightBalanceMode,
    loading_anchor: options.loadingAnchor,
  });
  return r.data;
}

export async function exportExcel(): Promise<Blob> {
  const r = await api.get("/export-excel", { responseType: "blob" });
  return r.data;
}

export interface ContainerReportOptions {
  meta: ReportMetadata;
  sortBy: SortReportBy;
  includeOverviewImage: boolean;
  overviewImagePngBase64?: string;
}

export async function exportContainerReportPdf(options: ContainerReportOptions): Promise<Blob> {
  const r = await api.post(
    "/report/container-pdf",
    {
      meta: { project_name: options.meta.projectName, customer: options.meta.customer },
      sort_by: options.sortBy,
      include_overview_image: options.includeOverviewImage,
      overview_image_png_base64: options.overviewImagePngBase64 ?? null,
    },
    { responseType: "blob" }
  );
  return r.data;
}

export interface StepModeOptions {
  stepMode: StepMode;
  piecesPerStep?: number;
}

export async function getReportSteps(direction: ReportDirection, options: StepModeOptions): Promise<string[][]> {
  const r = await api.post<{ steps: string[][] }>("/report/steps", {
    direction,
    step_mode: options.stepMode,
    pieces_per_step: options.piecesPerStep ?? null,
  });
  return r.data.steps;
}

export interface GuideReportOptions extends StepModeOptions {
  meta: ReportMetadata;
  stepImagesPngBase64: string[];
}

function guideReportBody(options: GuideReportOptions) {
  return {
    meta: { project_name: options.meta.projectName, customer: options.meta.customer },
    step_mode: options.stepMode,
    pieces_per_step: options.piecesPerStep ?? null,
    step_images_png_base64: options.stepImagesPngBase64,
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
