import type { ReactNode } from "react";

interface Props {
  title: string;
  description?: string;
  selected?: boolean;
  disabled?: boolean;
  badge?: string;
  onSelect: () => void;
  children?: ReactNode;
}

// <button> nativo: focuseable y activable con teclado (Enter/Space) sin
// necesidad de role/tabIndex/onKeyDown manual. El estado seleccionado nunca
// depende solo del color (borde + check "✓" + aria-pressed).
export function OptionCard({ title, description, selected, disabled, badge, onSelect, children }: Props) {
  return (
    <button
      type="button"
      className={`option-card${selected ? " selected" : ""}${disabled ? " disabled" : ""}`}
      onClick={onSelect}
      disabled={disabled}
      aria-pressed={selected ?? false}
    >
      <span className="option-card-title">
        {title}
        {selected && (
          <span className="option-card-check" aria-hidden="true">
            ✓
          </span>
        )}
      </span>
      {badge && <span className="option-card-badge">{badge}</span>}
      {description && <span className="option-card-desc">{description}</span>}
      {children}
    </button>
  );
}
