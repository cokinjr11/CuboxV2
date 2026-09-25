export type Theme = "dark" | "light";

interface Props {
  theme: Theme;
  onThemeChange: (theme: Theme) => void;
  blockExportsOnErrors: boolean;
  onBlockExportsOnErrorsChange: (value: boolean) => void;
  onClose: () => void;
}

export function SettingsModal({ theme, onThemeChange, blockExportsOnErrors, onBlockExportsOnErrorsChange, onClose }: Props) {
  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-box" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>Ajustes</h2>
          <button className="btn modal-close-btn" onClick={onClose}>
            Cerrar
          </button>
        </div>

        <div className="settings-section">
          <h3>Tema</h3>
          <div className="settings-row">
            <span>{theme === "dark" ? "Oscuro" : "Claro"}</span>
            <input
              type="checkbox"
              className="toggle-switch"
              checked={theme === "light"}
              onChange={(e) => onThemeChange(e.target.checked ? "light" : "dark")}
              title="Cambiar entre tema oscuro y claro"
            />
          </div>
        </div>

        <div className="settings-section">
          <h3>Export & Reports</h3>
          <div className="settings-row">
            <span>Block exports when validation errors exist</span>
            <input
              type="checkbox"
              className="toggle-switch"
              checked={blockExportsOnErrors}
              onChange={(e) => onBlockExportsOnErrorsChange(e.target.checked)}
              title="Block exports when validation errors exist"
            />
          </div>
          <p className="hint">
            When enabled, Cubox prevents operational reports from being exported while blocking validation errors
            remain. Turning it off does not fix or hide those errors -every export generated this way is still
            clearly marked "NOT READY / EXPORTED WITH VALIDATION ERRORS" on its cover and on every page.
          </p>
        </div>

        <div className="settings-section">
          <h3>Regla de orientacion</h3>
          <div className="settings-row disabled">
            <span>Permitir apoyar la cara Width x Height (vidrio)</span>
            <input type="checkbox" className="toggle-switch" checked={false} disabled title="No disponible" />
          </div>
          <p className="hint">
            Esta regla (Panel Edge Only) es especifica de items tipo Panel/ventana: la cara Width x Height nunca
            puede quedar como base. Otros tipos de carga (Box, Pallet, Custom) ya usan reglas de orientacion propias
            (Free, Upright, Fixed), configurables desde Handling Rules en el New Load Plan Wizard -este interruptor
            queda siempre activo y no editable porque afecta unicamente a los items Panel.
          </p>
        </div>
      </div>
    </div>
  );
}
