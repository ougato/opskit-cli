"""Inherited static candidates do not authorize more debt or other files."""
import pytest

from scripts.check_static_debt import compare


def issue(file="core/example.py", count=1):
    return {"file": file, "code": "F401", "message": "unused import", "count": count}


def test_new_file_and_duplicate_occurrence_are_blocked():
    added, resolved = compare([issue(count=2), issue("core/new.py")], [issue()])
    assert sum(added.values()) == 2 and not resolved


def test_resolved_debt_does_not_create_budget_for_other_debt():
    added, resolved = compare([issue("core/new.py")], [issue()])
    assert sum(added.values()) == sum(resolved.values()) == 1


def test_line_number_movement_does_not_mask_or_create_diagnostics():
    added, resolved = compare([{**issue(), "line": 40}], [{**issue(), "line": 2}])
    assert not added and not resolved


@pytest.mark.parametrize("count", [True, 0, -1, "1"])
def test_invalid_baseline_count_fails_closed(count):
    with pytest.raises(ValueError, match="baseline count"):
        compare([], [issue(count=count)])
