from decimal import Decimal as D

import pytest

from core.services.bands import (
    round_band,
    speaking_overall,
    task_band,
    validate_criterion_band,
    writing_overall,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("6.125", "6.0"),
        ("6.25", "6.5"),
        ("6.375", "6.5"),
        ("6.625", "6.5"),
        ("6.75", "7.0"),
        ("6.875", "7.0"),
        ("6.0", "6.0"),
        ("0", "0.0"),
        ("9", "9.0"),
    ],
)
def test_round_band(raw: str, expected: str) -> None:
    assert str(round_band(D(raw))) == expected


@pytest.mark.parametrize("bad", ["9.5", "-0.5", "6.25", "10"])
def test_validate_criterion_band_rejects(bad: str) -> None:
    with pytest.raises(ValueError):
        validate_criterion_band(D(bad))


def test_validate_criterion_band_accepts_half() -> None:
    assert validate_criterion_band(D("6.5")) == D("6.5")


def test_speaking_overall() -> None:
    assert speaking_overall([D("6"), D("6.5"), D("7"), D("7")]) == D("6.5")  # mean 6.625


def test_speaking_overall_rejects_invalid_criterion() -> None:
    with pytest.raises(ValueError):
        speaking_overall([D("6"), D("6"), D("6"), D("6.25")])


def test_writing_overall_weights_task2_double() -> None:
    assert writing_overall([D("6")] * 4, [D("7")] * 4) == D("6.5")  # (6 + 14) / 3 = 6.67


def test_writing_overall_uses_unrounded_task_means() -> None:
    t1 = [D("5")] * 4
    t2 = [D("5"), D("5"), D("5.5"), D("5.5")]  # mean 5.25, shown as 5.5
    assert task_band(t2) == D("5.5")
    # (5 + 10.5) / 3 = 5.17; rounding Task 2 first would give 5.5
    assert writing_overall(t1, t2) == D("5.0")


def test_wrong_criteria_count() -> None:
    with pytest.raises(ValueError):
        speaking_overall([D("6")] * 3)
    with pytest.raises(ValueError):
        writing_overall([D("6")] * 4, [D("6")] * 5)
