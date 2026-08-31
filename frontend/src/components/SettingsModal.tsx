export type Theme = "dark" | "light";

interface Props {
  theme: Theme;
  onThemeChange: (theme: Theme) => void;
  onClose: () => void;
}

export function SettingsModal({ theme, onThemeChange, onClose }: Props) {
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
          <h3>Regla de orientacion</h3>
          <div className="settings-row disabled">
            <span>Permitir apoyar la cara Width x Height (vidrio)</span>
            <input type="checkbox" className="toggle-switch" checked={false} disabled title="No disponible" />
          </div>
          <p className="hint">
            Esta regla es un limite fisico de seguridad: una ventana nunca se apoya sobre su cara de vidrio (Width x
            Height). Hoy no se puede desactivar porque todo lo que maneja el programa son ventanas de vidrio. Este
            interruptor queda preparado para el dia que el programa maneje otros tipos de piezas que no tengan esa
            restriccion -por ahora se mantiene siempre activo y no editable.
          </p>
        </div>
      </div>
    </div>
  );
}
