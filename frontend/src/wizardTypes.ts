// CUBOX 2.0 Fase 4 - New Load Plan Wizard. Tipos exclusivos del wizard
// (frontend-only, sin persistencia todavia) -no confundir con los tipos
// espejo del backend en types.ts.
import type { ImportPreview, ItemType, LoadingAnchor, LoadSpaceType, OrientationPolicy, WeightBalanceMode } from "./types";

// "Build Pallets" NO es un ItemType: es un flujo de preparacion futuro
// (BOX -> Pallet Builder -> PALLET) que todavia no existe. Por eso vive en
// PlanningMode pero no tiene ItemType asociado (ver PLANNING_MODE_ITEM_TYPE).
export type PlanningMode = "loose_boxes" | "palletized_load" | "build_pallets" | "panels_fragile" | "custom_load";

export const PLANNING_MODE_ITEM_TYPE: Partial<Record<PlanningMode, ItemType>> = {
  loose_boxes: "box",
  palletized_load: "pallet",
  panels_fragile: "panel",
  custom_load: "custom",
  // build_pallets: sin ItemType -Coming Soon, no se puede avanzar.
};

export type LoadSpaceCategory = "container" | "truck" | "trailer" | "custom";

export interface CustomLoadSpaceDraft {
  name: string;
  loadSpaceType: LoadSpaceType;
  length: number;
  width: number;
  height: number;
  maxWeight: number;
}

export interface LoadSpaceDraft {
  category: LoadSpaceCategory;
  // "container" con preset del catalogo (GET /api/load-spaces).
  containerId?: string;
  // "container" custom, o "truck"/"trailer"/"custom" (sin presets confiables).
  custom?: CustomLoadSpaceDraft;
}

export interface HandlingRulesDraft {
  orientationPolicy: OrientationPolicy;
  defaultStackable: boolean;
  clearanceMm: number;
  enableCentralAisle: boolean;
  aisleWidthMm: number;
  weightBalanceMode: WeightBalanceMode;
  loadingAnchor: LoadingAnchor;
}

export interface LoadPlanDraft {
  planningMode: PlanningMode | null;
  loadSpace: LoadSpaceDraft | null;
  handlingRules: HandlingRulesDraft | null;
  importPreview: ImportPreview | null;
}

export function defaultHandlingRulesFor(mode: PlanningMode): HandlingRulesDraft {
  const base: HandlingRulesDraft = {
    orientationPolicy: "free",
    defaultStackable: false,
    clearanceMm: 0,
    enableCentralAisle: false,
    aisleWidthMm: 500,
    weightBalanceMode: "normal",
    loadingAnchor: "back_right",
  };

  switch (mode) {
    case "palletized_load":
      return { ...base, orientationPolicy: "upright" };
    case "panels_fragile":
      // PANEL_EDGE_ONLY: regla fija, no editable desde esta pantalla. Los
      // items PANEL legacy conservan Stackable=Si por defecto (igual que el
      // importador Excel legacy y el perfil PANEL de Fase 3B).
      return { ...base, orientationPolicy: "panel_edge_only", defaultStackable: true };
    case "loose_boxes":
    case "custom_load":
    default:
      return base;
  }
}

export function createEmptyLoadPlanDraft(): LoadPlanDraft {
  return { planningMode: null, loadSpace: null, handlingRules: null, importPreview: null };
}
