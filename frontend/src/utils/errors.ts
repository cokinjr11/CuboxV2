// CUBOX 2.0 - Bug 2 de la ronda de fixes runtime: `e?.response?.data?.detail`
// asume que `detail` siempre es un string (el formato de las HTTPException
// hechas a mano, "reason" en texto plano). Pero cuando el backend rechaza el
// REQUEST BODY antes de llegar al handler (422 de validacion automatica de
// Pydantic/FastAPI -por ejemplo un Field(ge=/le=) fuera de rango), `detail`
// es un ARRAY de objetos {type, loc, msg, input, ctx}, no un string. Renderizar
// eso directo como children de React ("{feedback.message}") tira "Objects are
// not valid as a React child" y se cae toda la arbol -en la practica, la
// pantalla 3D completa se ponia en negro. Esta funcion normaliza CUALQUIER
// forma de `detail` (string, array de errores de Pydantic, o ausente) a un
// string legible, para que ningun caller pueda volver a pisar este bug.
export function extractErrorMessage(error: unknown, fallback: string): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;

  if (typeof detail === "string" && detail.trim() !== "") {
    return detail;
  }

  if (Array.isArray(detail) && detail.length > 0) {
    return detail
      .map((item) => {
        if (item && typeof item === "object" && "msg" in item) {
          return String((item as { msg: unknown }).msg);
        }
        return typeof item === "string" ? item : JSON.stringify(item);
      })
      .join("; ");
  }

  return fallback;
}
