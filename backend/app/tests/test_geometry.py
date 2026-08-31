from app.core.geometry import Box, boxes_overlap, check_support, within_container


def test_boxes_overlap_detects_overlap():
    a = Box("a", 0, 0, 0, 100, 100, 100)
    b = Box("b", 50, 50, 50, 100, 100, 100)
    assert boxes_overlap(a, b) is True


def test_boxes_touching_face_not_overlap():
    a = Box("a", 0, 0, 0, 100, 100, 100)
    b = Box("b", 100, 0, 0, 100, 100, 100)
    assert boxes_overlap(a, b) is False


def test_within_container_bounds():
    box = Box("a", 0, 0, 0, 100, 100, 100)
    assert within_container(box, length=100, width=100, height=100) is True
    box2 = Box("b", 0, 0, 0, 101, 100, 100)
    assert within_container(box2, length=100, width=100, height=100) is False


def test_support_on_floor_valid():
    box = Box("a", 0, 0, 0, 100, 100, 100)
    ok, _ = check_support(box, [])
    assert ok is True


def test_support_requires_stackable_underneath():
    bottom = Box("bottom", 0, 0, 0, 100, 100, 100, stackable=False)
    top = Box("top", 0, 0, 100, 100, 100, 100, stackable=True)
    ok, reason = check_support(top, [bottom])
    assert ok is False
    assert "no apilable" in reason


def test_support_requires_smaller_or_equal_base():
    bottom = Box("bottom", 0, 0, 0, 100, 100, 100, stackable=True)
    top = Box("top", 0, 0, 100, 150, 100, 100, stackable=True)
    ok, reason = check_support(top, [bottom])
    assert ok is False


def test_floating_piece_invalid():
    top = Box("top", 0, 0, 100, 50, 50, 50)
    ok, reason = check_support(top, [])
    assert ok is False
    assert "flotando" in reason
