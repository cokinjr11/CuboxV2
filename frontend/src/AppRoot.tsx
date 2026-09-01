import { useEffect, useState } from "react";
import App from "./App";
import { Home } from "./components/Home";
import { LoadPlanWizard } from "./components/wizard/LoadPlanWizard";
import "./wizard.css";
import type { InitialWorkspaceConfig } from "./types";
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
    },
  };
}

function AppRoot() {
  const [mode, setMode] = useState<Mode>("home");
  // Se preserva el LoadPlanDraft completo (incluidos los items importados)
  // para que una fase futura pueda conectar "Create Load Plan" con
  // Optimize de punta a punta -ver Fase 4, seccion 24.
  const [completedDraft, setCompletedDraft] = useState<LoadPlanDraft | null>(null);

  useEffect(() => {
    // App.tsx sincroniza data-theme en su propio useEffect, pero solo se
    // monta en mode="workspace" -sin esto, Home/el wizard siempre se verian
    // en el tema oscuro por defecto aunque el usuario haya elegido claro.
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
    document.documentElement.setAttribute("data-theme", stored === "light" ? "light" : "dark");
  }, []);

  function exitToHome() {
    // Sin persistencia todavia (Fase 5, seccion 35): una confirmacion simple
    // alcanza -no hace falta un sistema de "unsaved changes" completo.
    if (window.confirm("Leave the current workspace? Unsaved changes will be lost.")) {
      setCompletedDraft(null);
      setMode("home");
    }
  }

  if (mode === "home") {
    return <Home onNewLoadPlan={() => setMode("wizard")} onOpenLegacyWorkspace={() => setMode("workspace")} />;
  }

  if (mode === "wizard") {
    return (
      <LoadPlanWizard
        onCancel={() => setMode("home")}
        onComplete={(draft) => {
          setCompletedDraft(draft);
          setMode("workspace");
        }}
      />
    );
  }

  // "workspace": el LoadPlanWizard ya valido que el import sea is_valid y
  // que el Load Space este completo, asi que buildInitialWorkspace siempre
  // devuelve un config real en ese caso. Si se llego aca via "Open Legacy
  // Workspace" (sin draft), devuelve undefined -y <App/> arranca con su
  // comportamiento de siempre.
  return <App initialWorkspace={buildInitialWorkspace(completedDraft)} onExitToHome={exitToHome} />;
}

export default AppRoot;
