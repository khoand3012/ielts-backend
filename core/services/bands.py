"""IELTS band arithmetic, shared by human and AI grading.

Criterion bands are 0-9 in half-band steps. Averages are rounded with the British
Council rule: to the nearest half band, where .25 rounds up to the next half band and
.75 up to the next whole band (i.e. nearest 0.5, ties up).
"""

from collections.abc import Sequence
from decimal import ROUND_FLOOR, Decimal

_HALF = Decimal("0.5")
_ONE_DECIMAL = Decimal("0.1")
_CRITERIA_PER_SCORE = 4


def validate_criterion_band(value: Decimal) -> Decimal:
    if not (Decimal(0) <= value <= Decimal(9)) or value % _HALF != 0:
        raise ValueError(f"criterion band must be 0-9 in steps of 0.5, got {value}")
    return value


def round_band(value: Decimal) -> Decimal:
    halves = (value * 2 + _HALF).to_integral_value(rounding=ROUND_FLOOR)
    return (halves / 2).quantize(_ONE_DECIMAL)


def _mean(criteria: Sequence[Decimal]) -> Decimal:
    if len(criteria) != _CRITERIA_PER_SCORE:
        raise ValueError(f"expected {_CRITERIA_PER_SCORE} criteria, got {len(criteria)}")
    return sum((validate_criterion_band(c) for c in criteria), Decimal(0)) / len(criteria)


def task_band(criteria: Sequence[Decimal]) -> Decimal:
    return round_band(_mean(criteria))


def speaking_overall(criteria: Sequence[Decimal]) -> Decimal:
    return round_band(_mean(criteria))


def writing_overall(task1: Sequence[Decimal], task2: Sequence[Decimal]) -> Decimal:
    # Task 2 counts double; the task means stay unrounded so only the final band rounds.
    return round_band((_mean(task1) + 2 * _mean(task2)) / 3)
