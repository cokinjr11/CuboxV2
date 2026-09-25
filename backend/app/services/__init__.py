"""Integracion NAGSA: capa de LOGICA DE APLICACION.

Funciones puras sobre el estado de un Load Plan (`PlanState`), compartidas
por los dos modos de la API:

  - /api     (modo local, con estado en memoria + SQLite) -> api/routes.py
  - /api/v1  (modo integrado, sin estado)                 -> api/routes_v1.py

Reglas de esta capa (ver docs/CHECKLIST_INTEGRACION.md, "Regla de
dependencias"):
  - NO conoce HTTP (nada de FastAPI/HTTPException).
  - NO lee ni escribe la sesion local (`_current_state`) ni el historial.
  - NO conoce SQLite.
  - Solo depende de core/ y models/. Nunca al reves.
  - Un modulo = una responsabilidad."""
