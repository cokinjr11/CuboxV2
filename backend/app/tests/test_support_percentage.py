"""Soporte multiple por porcentaje y MaxStackWeight (secciones 12-16 de V2)."""

from app.core.geometry import Box, check_stack_weight, check_support


def test_multiple_supporters_above_threshold_is_valid():
    top = Box("top", x=0, y=0, z=100, dx=200, dy=100, dz=50)
    s1 = Box("s1", x=0, y=0, z=0, dx=100, dy=100, dz=100, stackable=True)
    s2 = Box("s2", x=100, y=0, z=0, dx=70, dy=100, dz=100, stackable=True)  # (100+70)/200 = 85%
    ok, reason = check_support(top, [s1, s2])
    assert ok is True, reason


def test_multiple_supporters_below_threshold_is_invalid():
    top = Box("top", x=0, y=0, z=100, dx=200, dy=100, dz=50)
    s1 = Box("s1", x=0, y=0, z=0, dx=100, dy=100, dz=100, stackable=True)  # solo 50%
    ok, reason = check_support(top, [s1])
    assert ok is False
    assert "insuficiente" in reason.lower()


def test_support_exactly_at_threshold_is_valid():
    top = Box("top", x=0, y=0, z=100, dx=200, dy=100, dz=50)
    s1 = Box("s1", x=0, y=0, z=0, dx=160, dy=100, dz=100, stackable=True)  # 160/200 = 80%
    ok, reason = check_support(top, [s1])
    assert ok is True, reason


def test_corner_only_support_is_invalid():
    """Apoyarse solo en una esquina nunca debe alcanzar el minimo de soporte."""
    top = Box("top", x=0, y=0, z=100, dx=200, dy=200, dz=50)
    corner = Box("corner", x=0, y=0, z=0, dx=40, dy=40, dz=100, stackable=True)  # ~4% de area
    ok, reason = check_support(top, [corner])
    assert ok is False


def test_stack_weight_within_limit_is_valid():
    supporter = Box("s", 0, 0, 0, 200, 200, 100, stackable=True, max_stack_weight=100)
    existing_top = Box("existing", 0, 0, 100, 100, 200, 50, stackable=True)
    candidate = Box("candidate", 100, 0, 100, 100, 200, 50, stackable=True)
    ok, reason = check_stack_weight(candidate, 30, [supporter, existing_top], {"existing": 60})
    assert ok is True, reason


def test_stack_weight_exceeded_is_invalid():
    supporter = Box("s", 0, 0, 0, 200, 200, 100, stackable=True, max_stack_weight=100)
    existing_top = Box("existing", 0, 0, 100, 100, 200, 50, stackable=True)
    candidate = Box("candidate", 100, 0, 100, 100, 200, 50, stackable=True)
    ok, reason = check_stack_weight(candidate, 50, [supporter, existing_top], {"existing": 60})
    assert ok is False
    assert "peso maximo apilable" in reason.lower()


def test_stack_weight_none_means_unlimited():
    supporter = Box("s", 0, 0, 0, 200, 200, 100, stackable=True, max_stack_weight=None)
    candidate = Box("candidate", 0, 0, 100, 200, 200, 50, stackable=True)
    ok, reason = check_stack_weight(candidate, 100000, [supporter], {})
    assert ok is True, reason


def test_check_stack_weight_with_two_simultaneous_supporters():
    """CUBOX V4 (auditoria Stackable, prioridad 10): una pieza que descansa a
    la vez sobre DOS soportes distintos debe evaluar el limite de cada uno por
    separado -si cualquiera de los dos lo excede, debe rechazarse, aunque el
    otro tenga margen de sobra."""
    a1 = Box("a1", x=0, y=0, z=0, dx=100, dy=100, dz=50, stackable=True, max_stack_weight=10)
    a2 = Box("a2", x=100, y=0, z=0, dx=100, dy=100, dz=50, stackable=True, max_stack_weight=100)
    top = Box("top", x=0, y=0, z=50, dx=200, dy=100, dz=50, stackable=True)

    ok, reason = check_stack_weight(top, box_weight=15, others=[a1, a2], weights_by_id={})
    assert ok is False, "deberia rechazar por exceder el limite de a1 (10kg), aunque a2 tenga margen (100kg)"
    assert "a1" in reason

    ok2, reason2 = check_stack_weight(top, box_weight=8, others=[a1, a2], weights_by_id={})
    assert ok2 is True, reason2
