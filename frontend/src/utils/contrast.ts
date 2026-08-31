/** Colores de texto/outline elegidos automaticamente segun la luminancia
 * percibida del color de fondo, para que las etiquetas sean legibles sin
 * importar el color de la pieza (nunca texto amarillo sobre caja amarilla,
 * rojo sobre rojo, ni oscuro sobre un fondo oscuro). */
export interface LabelColors {
  text: string;
  outline: string;
}

const LIGHT_TEXT = "#ffffff";
const DARK_TEXT = "#111111";

export function pickLabelColors(baseColor: string): LabelColors {
  const rgb = parseColor(baseColor);
  if (!rgb) return { text: LIGHT_TEXT, outline: "#000000" };

  const luminance = relativeLuminance(rgb);
  const isLight = luminance > 0.55;
  return isLight ? { text: DARK_TEXT, outline: LIGHT_TEXT } : { text: LIGHT_TEXT, outline: "#000000" };
}

function parseColor(value: string): [number, number, number] | null {
  const trimmed = value.trim();
  if (trimmed.startsWith("#")) return parseHex(trimmed);
  if (trimmed.startsWith("hsl")) return parseHsl(trimmed);
  return null;
}

function parseHex(hex: string): [number, number, number] | null {
  let h = hex.slice(1);
  if (h.length === 3) {
    h = h
      .split("")
      .map((c) => c + c)
      .join("");
  }
  if (h.length !== 6) return null;
  const num = parseInt(h, 16);
  if (Number.isNaN(num)) return null;
  return [(num >> 16) & 255, (num >> 8) & 255, num & 255];
}

function parseHsl(hsl: string): [number, number, number] | null {
  const match = hsl.match(/hsl\(\s*([\d.]+)\s*,\s*([\d.]+)%\s*,\s*([\d.]+)%\s*\)/i);
  if (!match) return null;
  const h = Number(match[1]) / 360;
  const s = Number(match[2]) / 100;
  const l = Number(match[3]) / 100;
  return hslToRgb(h, s, l);
}

function hslToRgb(h: number, s: number, l: number): [number, number, number] {
  if (s === 0) {
    const v = Math.round(l * 255);
    return [v, v, v];
  }
  const hue2rgb = (p: number, q: number, t: number) => {
    let tt = t;
    if (tt < 0) tt += 1;
    if (tt > 1) tt -= 1;
    if (tt < 1 / 6) return p + (q - p) * 6 * tt;
    if (tt < 1 / 2) return q;
    if (tt < 2 / 3) return p + (q - p) * (2 / 3 - tt) * 6;
    return p;
  };
  const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
  const p = 2 * l - q;
  const r = hue2rgb(p, q, h + 1 / 3);
  const g = hue2rgb(p, q, h);
  const b = hue2rgb(p, q, h - 1 / 3);
  return [Math.round(r * 255), Math.round(g * 255), Math.round(b * 255)];
}

/** Luminancia relativa estilo WCAG 2.x sobre canales sRGB linealizados. */
function relativeLuminance([r, g, b]: [number, number, number]): number {
  const linearize = (c: number) => {
    const cs = c / 255;
    return cs <= 0.04045 ? cs / 12.92 : Math.pow((cs + 0.055) / 1.055, 2.4);
  };
  const [lr, lg, lb] = [linearize(r), linearize(g), linearize(b)];
  return 0.2126 * lr + 0.7152 * lg + 0.0722 * lb;
}
