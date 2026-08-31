import io

import pytest
from openpyxl import Workbook

from app.core.excel_import import parse_excel


def _build_excel(rows, headers=None):
    headers = headers or [
        "Code",
        "Description",
        "Width",
        "Height",
        "Thickness",
        "Weight",
        "Quantity",
        "System",
        "Group",
        "Stackable",
        "Priority",
    ]
    wb = Workbook()
    ws = wb.active
    ws.append(headers)
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_parse_excel_happy_path():
    data = _build_excel(
        [
            ["W1", "Ventana A", 1200, 2000, 100, 45, 5, "SysA", "G1", "Yes", 1],
            ["W2", "Ventana B", 900, 1500, 80, 30, 3, "SysB", "G2", "No", 2],
        ]
    )
    items = parse_excel(data)
    assert len(items) == 2
    assert items[0].code == "W1"
    assert items[0].stackable is True
    assert items[1].stackable is False


def test_parse_excel_without_v3_columns_defaults_to_none():
    """Compatibilidad hacia atras: un Excel de V1/V2 (sin MaxStackWeight ni
    DeliverySequence) debe seguir importando, con esos campos en None."""
    data = _build_excel([["W1", "Ventana A", 1200, 2000, 100, 45, 5, "SysA", "G1", "Yes", 1]])
    items = parse_excel(data)
    assert items[0].max_stack_weight is None
    assert items[0].delivery_sequence is None


def test_parse_excel_with_delivery_sequence_column():
    headers = [
        "Code", "Description", "Width", "Height", "Thickness", "Weight", "Quantity",
        "System", "Group", "Stackable", "Priority", "MaxStackWeight", "DeliverySequence",
    ]
    data = _build_excel(
        [["W1", "Ventana A", 1200, 2000, 100, 45, 5, "SysA", "G1", "Yes", 1, 150, 2]],
        headers=headers,
    )
    items = parse_excel(data)
    assert items[0].max_stack_weight == 150
    assert items[0].delivery_sequence == 2


def test_parse_excel_missing_required_column_raises():
    headers = ["Code", "Description", "Width", "Height"]
    data = _build_excel([["W1", "Ventana A", 1200, 2000]], headers=headers)
    with pytest.raises(ValueError):
        parse_excel(data)


def test_parse_excel_empty_raises():
    wb = Workbook()
    buf = io.BytesIO()
    wb.save(buf)
    with pytest.raises(ValueError):
        parse_excel(buf.getvalue())
