from decimal import Decimal as D

import pytest

from core.models import Module, Skill
from core.services.bands import (
    raw_to_band,
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


# Spec §4.2, as (lowest raw mark, band) from the top.
LISTENING = [(39, "9.0"), (37, "8.5"), (35, "8.0"), (32, "7.5"), (30, "7.0"), (26, "6.5"),
             (23, "6.0"), (18, "5.5"), (16, "5.0"), (13, "4.5"), (10, "4.0"), (8, "3.5"),
             (6, "3.0"), (4, "2.5"), (2, "2.0"), (1, "1.0"), (0, "0.0")]  # fmt: skip
ACADEMIC = [(39, "9.0"), (37, "8.5"), (35, "8.0"), (33, "7.5"), (30, "7.0"), (27, "6.5"),
            (23, "6.0"), (19, "5.5"), (15, "5.0"), (13, "4.5"), (10, "4.0"), (8, "3.5"),
            (6, "3.0"), (4, "2.5"), (2, "2.0"), (1, "1.0"), (0, "0.0")]  # fmt: skip
GENERAL = [(40, "9.0"), (39, "8.5"), (37, "8.0"), (36, "7.5"), (35, "7.0"), (32, "6.5"),
           (30, "6.0"), (27, "5.5"), (23, "5.0"), (19, "4.5"), (15, "4.0"), (12, "3.5"),
           (9, "3.0"), (6, "2.5"), (3, "2.0"), (1, "1.0"), (0, "0.0")]  # fmt: skip


@pytest.mark.parametrize(
    ("skill", "module", "table"),
    [
        (Skill.LISTENING, None, LISTENING),
        (Skill.READING, Module.ACADEMIC, ACADEMIC),
        (Skill.READING, Module.GENERAL_TRAINING, GENERAL),
    ],
)
def test_raw_to_band_every_mark(
    skill: Skill, module: Module | None, table: list[tuple[int, str]]
) -> None:
    for raw in range(41):
        expected = next(band for low, band in table if raw >= low)
        result = raw_to_band(skill, module, raw)
        assert result == D(expected), raw
        assert str(result) == expected


def test_same_mark_differs_by_module() -> None:
    assert raw_to_band(Skill.READING, Module.ACADEMIC, 30) == D("7.0")
    assert raw_to_band(Skill.READING, Module.GENERAL_TRAINING, 30) == D("6.0")


@pytest.mark.parametrize(
    ("skill", "module", "raw"),
    [
        (Skill.LISTENING, None, -1),
        (Skill.LISTENING, None, 41),
        (Skill.READING, None, 20),
        (Skill.WRITING, None, 20),
    ],
)
def test_raw_to_band_errors(skill: Skill, module: Module | None, raw: int) -> None:
    with pytest.raises(ValueError):
        raw_to_band(skill, module, raw)
