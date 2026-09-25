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

// Fase 6B.1, seccion 11 del pedido: bug real encontrado en produccion -las
// llamadas de export PDF usan `responseType: "blob"` (necesario para poder
// descargar el PDF exitoso como archivo binario), pero eso hace que Axios
// tambien devuelva el CUERPO DE ERROR como Blob en vez de JSON ya parseado
// cuando el backend responde con un status distinto de 2xx (422/400/500) -
// `error.response.data` nunca es el objeto {detail: ...} en ese caso, es un
// Blob, asi que extractErrorMessage() siempre caia al fallback generico
// ("Error al generar el reporte") sin importar que tan especifico fuera el
// error real del backend (ej. "BOX-011-003: Pieza flotando: no hay soporte
// debajo" de core/final_validation.py). Esta version lee el Blob como texto,
// lo parsea como JSON, y reusa extractErrorMessage sobre el resultado -sin
// duplicar esa logica de extraccion.
export async function extractErrorMessageFromBlobResponse(error: unknown, fallback: string): Promise<string> {
  const data = (error as { response?: { data?: unknown } })?.response?.data;
  if (data instanceof Blob) {
    try {
      const text = await data.text();
      const parsed = JSON.parse(text);
      return extractErrorMessage({ response: { data: parsed } }, fallback);
    } catch {
      return fallback;
    }
  }
  return extractErrorMessage(error, fallback);
}
