import { useEffect, useState } from "react";
import App from "./App";
import { Home } from "./components/Home";
import { LoadPlanWizard } from "./components/wizard/LoadPlanWizard";
import "./wizard.css";
import type { LoadPlanDraft } from "./wizardTypes";

// CUBOX 2.0 Fase 4: HOME / SETUP (wizard) / WORKSPACE como un simple estado
// de nivel superior -no hay React Router todavia (no hace falta URL real en
// esta fase; ver seccion 3 del plan).
type Mode = "home" | "wizard" | "workspace";

const THEME_STORAGE_KEY = "cubox-theme";

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

  // "workspace": el LoadPlanWizard ya valido que el import sea is_valid, asi
  // que sus items son seguros para precargar. Un Load Space "custom"
  // (Truck/Trailer/Custom Space) todavia no tiene wiring de optimizacion
  // (ver Fase 4, seccion 24) -en ese caso no se preselecciona ningun
  // contenedor y el workspace usa su comportamiento por defecto de siempre.
  const initialItems = completedDraft?.importPreview?.items;
  const initialContainerId =
    completedDraft?.loadSpace?.category === "container" ? completedDraft.loadSpace.containerId : undefined;

  return <App initialItems={initialItems} initialContainerId={initialContainerId} />;
}

export default AppRoot;
