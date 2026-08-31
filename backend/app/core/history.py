"""Undo/redo simple sobre el estado editable (placed + unloaded).

Vive en memoria de proceso, igual que el resto del estado de la sesion local
(`_current_state` en api/routes.py). No se persiste a disco.
"""

from collections import deque

from app.models.schemas import PlacedPiece, UnloadedItem

Snapshot = tuple[list[PlacedPiece], list[UnloadedItem]]

MAX_HISTORY = 30


class EditHistory:
    def __init__(self, max_size: int = MAX_HISTORY):
        self.undo_stack: deque[Snapshot] = deque(maxlen=max_size)
        self.redo_stack: deque[Snapshot] = deque(maxlen=max_size)

    def reset(self) -> None:
        self.undo_stack.clear()
        self.redo_stack.clear()

    @staticmethod
    def _snapshot(placed: list[PlacedPiece], unloaded: list[UnloadedItem]) -> Snapshot:
        return ([p.model_copy() for p in placed], [u.model_copy() for u in unloaded])

    def push(self, placed: list[PlacedPiece], unloaded: list[UnloadedItem]) -> None:
        """Guarda el estado ANTES de aplicar una edicion manual."""
        self.undo_stack.append(self._snapshot(placed, unloaded))
        self.redo_stack.clear()

    def undo(self, current_placed: list[PlacedPiece], current_unloaded: list[UnloadedItem]) -> Snapshot | None:
        if not self.undo_stack:
            return None
        self.redo_stack.append(self._snapshot(current_placed, current_unloaded))
        return self.undo_stack.pop()

    def redo(self, current_placed: list[PlacedPiece], current_unloaded: list[UnloadedItem]) -> Snapshot | None:
        if not self.redo_stack:
            return None
        self.undo_stack.append(self._snapshot(current_placed, current_unloaded))
        return self.redo_stack.pop()
