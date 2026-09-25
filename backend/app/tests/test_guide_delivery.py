"""Fase 6B: anotacion de Delivery Sequence sobre pasos de descarga ya
resueltos (core/sequence.py:annotate_unload_steps_with_delivery /
group_unload_steps_into_delivery_sections).

Regla critica de todo este archivo: el ORDEN FISICO de `steps` (ya resuelto
por compute_unload_steps/chunk_sequence, Fase 6A) es autoritativo y NUNCA se
reordena por Delivery Sequence -estas funciones solo describen/agrupan,
jamas mueven un paso."""

from app.core.sequence import (
    annotate_unload_steps_with_delivery,
    compute_operational_warnings,
    compute_unload_steps,
)
from app.models.schemas import ContainerSpec, OperationalWarningType, PlacedPiece
from app.core.sequence import group_unload_steps_into_delivery_sections

CONTAINER = ContainerSpec(id="test", name="Test", length=5000, width=5000, height=3000, max_weight=1_000_000)


def _piece(piece_id, x, y=0, z=0, delivery_sequence=None, dx=100, dy=100, dz=100):
    return PlacedPiece(
        id=piece_id, code=piece_id, weight=10, stackable=True, priority=1, delivery_sequence=delivery_sequence,
        x=x, y=y, z=z, dx=dx, dy=dy, dz=dz,
        orientation_label="P1-a", source_width=dx, source_height=dy, source_thickness=dz,
    )


# ---------------------------------------------------------------------------
# annotate_unload_steps_with_delivery
# ---------------------------------------------------------------------------


def test_annotate_step_with_a_single_delivery_sequence():
    steps = [["a", "b"], ["c"]]
    placed = [_piece("a", x=0, delivery_sequence=1), _piece("b", x=0, y=200, delivery_sequence=1), _piece("c", x=200, delivery_sequence=2)]
    info = annotate_unload_steps_with_delivery(steps, placed)
    assert info[0].delivery_sequences == [1]
    assert info[0].is_mixed is False
    assert info[1].delivery_sequences == [2]


def test_annotate_mixed_delivery_step_never_invents_a_single_number():
    """Seccion 15 del pedido: un paso con Delivery Sequence 1 y 3 mezclados
    no colapsa a un numero -se listan ambos y se marca is_mixed."""
    steps = [["a", "b"]]
    placed = [_piece("a", x=0, delivery_sequence=1), _piece("b", x=0, y=200, delivery_sequence=3)]
    info = annotate_unload_steps_with_delivery(steps, placed)
    assert info[0].delivery_sequences == [1, 3]
    assert info[0].is_mixed is True


def test_annotate_step_with_no_delivery_sequence_at_all():
    """Seccion 34: sin Delivery Sequence, no se inventa un valor (ni 0)."""
    steps = [["a", "b"]]
    placed = [_piece("a", x=0), _piece("b", x=0, y=200)]
    info = annotate_unload_steps_with_delivery(steps, placed)
    assert info[0].delivery_sequences == []
    assert info[0].is_mixed is False


def test_annotate_step_with_partial_delivery_sequence_ignores_missing_ones():
    """Un item sin Delivery Sequence en un paso que si tiene otro con valor
    -el valor definido se reporta, el item sin valor simplemente no aporta
    ninguno (no genera 'mixed' con None)."""
    steps = [["a", "b"]]
    placed = [_piece("a", x=0, delivery_sequence=2), _piece("b", x=0, y=200, delivery_sequence=None)]
    info = annotate_unload_steps_with_delivery(steps, placed)
    assert info[0].delivery_sequences == [2]
    assert info[0].is_mixed is False


def test_annotate_attaches_existing_delivery_conflict_message_verbatim():
    """Seccion 16: el mensaje NO se genera de nuevo -es el mismo texto que
    ya produce compute_operational_warnings (Fase 6A), solo se filtra y
    adjunta al paso correspondiente."""
    blocker = _piece("blocker", x=0, y=0, delivery_sequence=3)  # cerca de la puerta
    blocked = _piece("blocked", x=200, y=0, delivery_sequence=1)  # bloqueado por blocker
    placed = [blocker, blocked]
    warnings = compute_operational_warnings(placed, CONTAINER)
    conflicts = [w for w in warnings if w.type == OperationalWarningType.DELIVERY_SEQUENCE_CONFLICT]
    assert len(conflicts) == 1  # confirma el fixture antes de usarlo

    steps = compute_unload_steps(placed)
    info = annotate_unload_steps_with_delivery(steps, placed, warnings)
    blocked_step = next(i for i, s in enumerate(steps) if "blocked" in s)
    assert conflicts[0].message in info[blocked_step].conflict_messages


def test_annotate_does_not_attach_unrelated_warnings():
    steps = [["a"], ["b"]]
    placed = [_piece("a", x=0, delivery_sequence=1), _piece("b", x=200, delivery_sequence=1)]
    # warning inventada que no menciona ni "a" ni "b"
    from app.models.schemas import OperationalWarning

    unrelated = OperationalWarning(
        type=OperationalWarningType.DELIVERY_SEQUENCE_CONFLICT, message="irrelevant", item_id="zzz", blocking_item_id="yyy"
    )
    info = annotate_unload_steps_with_delivery(steps, placed, [unrelated])
    assert info[0].conflict_messages == []
    assert info[1].conflict_messages == []


# ---------------------------------------------------------------------------
# Regla critica: el orden fisico de compute_unload_steps NUNCA se reordena
# por Delivery Sequence, ni por la anotacion ni por la agrupacion en
# secciones (secciones 12, 16, 32 del pedido -test de regresion critico).
# ---------------------------------------------------------------------------


def test_physical_conflict_order_is_preserved_not_reordered_by_delivery():
    """Escenario de la seccion 32 del pedido: B (Delivery 3) bloquea
    fisicamente a A (Delivery 1). B y A son un bloqueo DIRECTO -por diseno
    ya existente de _group_into_modules (no modificado en esta fase), un
    bloqueo directo los agrupa en el MISMO Step (igual criterio que un
    soporte vertical directo, ver test_compute_load_steps_respects_
    support_dependency en test_sequence.py: modulo fisico coherente). Lo
    que NUNCA puede pasar es que, dentro de ese Step (o entre Steps), A
    aparezca ANTES que B solo porque su Delivery Sequence es mas bajo."""
    a = _piece("A", x=200, y=0, delivery_sequence=1)
    b = _piece("B", x=0, y=0, delivery_sequence=3)  # blocker, cerca de la puerta
    placed = [a, b]

    steps = compute_unload_steps(placed)
    flat = [pid for step in steps for pid in step]
    assert flat.index("B") < flat.index("A"), "el orden fisico (B antes que A) no debe invertirse por Delivery Sequence"

    warnings = compute_operational_warnings(placed, CONTAINER)
    info = annotate_unload_steps_with_delivery(steps, placed, warnings)
    sections = group_unload_steps_into_delivery_sections(info)
    # No hay reordenamiento "prolijo" a Delivery 1 primero: al estar B y A
    # en el mismo Step (dependencia directa), la seccion queda marcada como
    # mixta -nunca colapsada a un solo numero (seccion 15).
    flat_step_indices = [i for section in sections for i in section.step_indices]
    assert flat_step_indices == list(range(len(steps))), "las secciones deben cubrir los pasos en su orden original, sin reordenar"
    assert any("Mixed" in (s.label or "") for s in sections)
    # El conflicto ya detectado por Fase 6A sigue visible, sin duplicar el motor.
    conflict_step = next(i for i, s in enumerate(steps) if "A" in s)
    assert any("blocked by" in m for m in info[conflict_step].conflict_messages)


# ---------------------------------------------------------------------------
# group_unload_steps_into_delivery_sections
# ---------------------------------------------------------------------------


def test_sections_merge_contiguous_steps_sharing_the_same_single_delivery():
    """Seccion 14: Step1->Delivery1, Step2->Delivery1, Step3->Delivery2,
    Step4->Delivery2 debe producir 2 secciones limpias."""
    steps = [["a1"], ["a2"], ["b1"], ["b2"]]
    placed = [
        _piece("a1", x=0, delivery_sequence=1), _piece("a2", x=100, delivery_sequence=1),
        _piece("b1", x=200, delivery_sequence=2), _piece("b2", x=300, delivery_sequence=2),
    ]
    info = annotate_unload_steps_with_delivery(steps, placed)
    sections = group_unload_steps_into_delivery_sections(info)
    assert [(s.label, s.step_indices) for s in sections] == [
        ("DELIVERY 1", [0, 1]),
        ("DELIVERY 2", [2, 3]),
    ]


def test_sections_never_merge_out_of_order_non_contiguous_same_delivery():
    """Seccion 14 (ejemplo 'malo' explicito): Step1->Delivery3, Step2->
    Delivery1, Step3->Delivery1 debe quedar en 2 secciones (3, luego 1),
    NUNCA fusionado ni reordenado a Delivery1 primero."""
    steps = [["x"], ["y"], ["z"]]
    placed = [
        _piece("x", x=0, delivery_sequence=3),
        _piece("y", x=100, delivery_sequence=1),
        _piece("z", x=200, delivery_sequence=1),
    ]
    info = annotate_unload_steps_with_delivery(steps, placed)
    sections = group_unload_steps_into_delivery_sections(info)
    assert [(s.label, s.step_indices) for s in sections] == [
        ("DELIVERY 3", [0]),
        ("DELIVERY 1", [1, 2]),
    ]


def test_sections_never_merge_a_mixed_step_with_a_clean_step_of_the_same_value():
    steps = [["a"], ["b", "c"]]
    placed = [
        _piece("a", x=0, delivery_sequence=1),
        _piece("b", x=100, delivery_sequence=1),
        _piece("c", x=100, y=200, delivery_sequence=3),
    ]
    info = annotate_unload_steps_with_delivery(steps, placed)
    sections = group_unload_steps_into_delivery_sections(info)
    assert len(sections) == 2
    assert sections[0].label == "DELIVERY 1"
    assert "Mixed" in sections[1].label


def test_sections_have_no_header_when_no_item_has_delivery_sequence():
    """Seccion 34: sin Delivery Sequence en ningun item del plan, no debe
    haber ningun encabezado 'DELIVERY' -toda la guia queda en 1 seccion sin
    label."""
    steps = [["a"], ["b"]]
    placed = [_piece("a", x=0), _piece("b", x=200)]
    info = annotate_unload_steps_with_delivery(steps, placed)
    sections = group_unload_steps_into_delivery_sections(info)
    assert len(sections) == 1
    assert sections[0].label is None
    assert sections[0].step_indices == [0, 1]


def test_sections_allow_many_items_sharing_the_same_delivery_sequence():
    """Seccion 33: varios items con el mismo Delivery Sequence es normal."""
    steps = [["a"], ["b"], ["c"]]
    placed = [_piece(pid, x=i * 100, delivery_sequence=1) for i, pid in enumerate(["a", "b", "c"])]
    info = annotate_unload_steps_with_delivery(steps, placed)
    sections = group_unload_steps_into_delivery_sections(info)
    assert len(sections) == 1
    assert sections[0].label == "DELIVERY 1"
    assert sections[0].step_indices == [0, 1, 2]
