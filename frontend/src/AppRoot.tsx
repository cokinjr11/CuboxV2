import { useEffect, useState } from "react";
import App from "./App";
import { getPlan } from "./api/client";
import { Home } from "./components/Home";
import { LoadPlanWizard } from "./components/wizard/LoadPlanWizard";
import { extractErrorMessage } from "./utils/errors";
import "./wizard.css";
import type { InitialWorkspaceConfig, PlanDetail } from "./types";
import type { LoadPlanDraft } from "./wizardTypes";

// CUBOX 2.0 Fase 4: HOME / SETUP (wizard) / WORKSPACE como un simple estado
// de nivel superior -no hay React Router todavia (no hace falta URL real en
// esta fase; ver seccion 3 del plan).
type Mode = "home" | "wizard" | "workspace";

const THEME_STORAGE_KEY = "cubox-theme";

// Fase 5: convierte el LoadPlanDraft (ya validado por el wizard: Load Space
// completo, ImportPreview.is_valid) en el contrato estructurado que espera
// App.tsx. undefined solo puede pasar si se llega a "workspace" sin haber
// completado el wizard (Open Legacy Workspace), nunca despues de un Create
// Load Plan real -el wizard no deja avanzar sin loadSpace/handlingRules.
function buildInitialWorkspace(draft: LoadPlanDraft | null): InitialWorkspaceConfig | undefined {
  if (!draft || !draft.loadSpace || !draft.handlingRules) return undefined;

  const loadSpace: InitialWorkspaceConfig["loadSpace"] =
    draft.loadSpace.category === "container" && draft.loadSpace.containerId
      ? { containerId: draft.loadSpace.containerId }
      : draft.loadSpace.custom
        ? {
            customLoadSpace: {
              name: draft.loadSpace.custom.name,
              load_space_type: draft.loadSpace.custom.loadSpaceType,
              length: draft.loadSpace.custom.length,
              width: draft.loadSpace.custom.width,
              height: draft.loadSpace.custom.height,
              max_weight: draft.loadSpace.custom.maxWeight,
              road_weight_config: draft.loadSpace.custom.roadWeightConfig,
            },
          }
        : { containerId: "" };

  return {
    items: draft.importPreview?.items ?? [],
    loadSpace,
    handlingRules: {
      enableCentralAisle: draft.handlingRules.enableCentralAisle,
      aisleWidthMm: draft.handlingRules.aisleWidthMm,
      clearanceMm: draft.handlingRules.clearanceMm,
      weightBalanceMode: draft.handlingRules.weightBalanceMode,
      loadingAnchor: draft.handlingRules.loadingAnchor,
      defaultStackable: draft.handlingRules.defaultStackable,
      orientationPolicy: draft.handlingRules.orientationPolicy,
      defaultAllowTilt: draft.handlingRules.defaultAllowTilt,
      defaultMaxTiltAngle: draft.handlingRules.defaultAllowTilt ? draft.handlingRules.defaultMaxTiltAngle : null,
    },
  };
}

function AppRoot() {
  const [mode, setMode] = useState<Mode>("home");
  // Se preserva el LoadPlanDraft completo (incluidos los items importados)
  // para que una fase futura pueda conectar "Create Load Plan" con
  // Optimize de punta a punta -ver Fase 4, seccion 24.
  const [completedDraft, setCompletedDraft] = useState<LoadPlanDraft | null>(null);
  // Fase 5D: cuando el usuario abre un plan guardado desde Recent Plans, el
  // Workspace se hidrata DIRECTO desde esta respuesta -nunca desde
  // completedDraft/buildInitialWorkspace (eso solo aplica al flujo del
  // wizard). Mutuamente excluyente con completedDraft: cualquier entrada
  // nueva a "wizard" u "Open Legacy Workspace" limpia esto explicitamente
  // (seccion 36 del pedido: New Load Plan nunca hereda un plan anterior).
  const [openedPlan, setOpenedPlan] = useState<PlanDetail | null>(null);
  const [openPlanError, setOpenPlanError] = useState("");

  useEffect(() => {
    // App.tsx sincroniza data-theme en su propio useEffect, pero solo se
    // monta en mode="workspace" -sin esto, Home/el wizard siempre se verian
    // en el tema oscuro por defecto aunque el usuario haya elegido claro.
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
    document.documentElement.setAttribute("data-theme", stored === "light" ? "light" : "dark");
  }, []);

  function exitToHome() {
    // Fase 5D: el Workspace autoguarda solos sus propios cambios (ver
    // App.tsx) cuando hay un plan_id activo -la confirmacion de "se pierde
    // el trabajo" solo importa de verdad para "Open Legacy Workspace" (sin
    // persistencia, seccion 12 del pedido: fuera de alcance de esta fase) o
    // para un plan que todavia no llego a su primer Optimize (sin plan_id
    // todavia). Mantenerla simple para ambos casos evita un sistema de
    // "unsaved changes" mas complejo.
    if (window.confirm("Leave the current workspace?")) {
      setCompletedDraft(null);
      setOpenedPlan(null);
      setMode("home");
    }
  }

  async function openPlan(planId: string) {
    setOpenPlanError("");
    try {
      const detail = await getPlan(planId);
      setCompletedDraft(null);
      setOpenedPlan(detail);
      setMode("workspace");
    } catch (e) {
      setOpenPlanError(extractErrorMessage(e, "No se pudo abrir el Load Plan."));
    }
  }

  if (mode === "home") {
    return (
      <Home
        onNewLoadPlan={() => {
          setOpenedPlan(null);
          setMode("wizard");
        }}
        onOpenLegacyWorkspace={() => {
          setOpenedPlan(null);
          setMode("workspace");
        }}
        onOpenPlan={openPlan}
        openPlanError={openPlanError}
      />
    );
  }

  if (mode === "wizard") {
    return (
      <LoadPlanWizard
        onCancel={() => setMode("home")}
        onComplete={(draft) => {
          setOpenedPlan(null);
          setCompletedDraft(draft);
          setMode("workspace");
        }}
      />
    );
  }

  // "workspace": si se abrio un plan guardado (openedPlan), tiene prioridad
  // -restaura el Workspace EXACTO sin pasar por el wizard. Si no, el
  // LoadPlanWizard ya valido que el import sea is_valid y que el Load Space
  // este completo, asi que buildInitialWorkspace siempre devuelve un config
  // real en ese caso. Si se llego aca via "Open Legacy Workspace" (sin
  // draft ni plan abierto), ambos son undefined -y <App/> arranca con su
  // comportamiento de siempre.
  return (
    <App
      initialWorkspace={buildInitialWorkspace(completedDraft)}
      restoredPlan={openedPlan ?? undefined}
      onExitToHome={exitToHome}
    />
  );
}

export default AppRoot;
