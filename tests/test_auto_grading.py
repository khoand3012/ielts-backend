from decimal import Decimal as D
from typing import Any

import pytest

from core.models import Module, SessionMode, Skill
from core.services.auto_grading import mark_item, normalize, score
from core.services.content import ItemSpec, item_index, parse_content
from tests.content_factories import (
    listening_content,
    listening_key,
    reading_content,
    reading_key,
)
from tests.helpers import correct_answers

TEXT = ItemSpec(8, (8,), "text")
MATCH = ItemSpec(27, (27,), "single_choice", frozenset("ABCDE"))
CHOOSE_TWO = ItemSpec(21, (21, 22), "choice", frozenset("ABCDE"), 2)


def listening_index() -> dict[int, ItemSpec]:
    return item_index(Skill.LISTENING, parse_content(Skill.LISTENING, listening_content()))


def reading_index() -> dict[int, ItemSpec]:
    return item_index(Skill.READING, parse_content(Skill.READING, reading_content()))


def test_normalize() -> None:
    assert normalize("  Dry \t Stone\u00a0") == "dry stone"
    assert normalize("Dry-Stone") == "dry-stone"
    assert normalize("Farmer\u2019s \u201cbest\u201d") == 'farmer\'s "best"'


def test_text_matching_whitespace_and_case() -> None:
    for answer in ("dry  stone", "DRY STONE", "dry\u00a0stone "):
        assert mark_item(TEXT, ["Dry Stone "], answer).marks == 1, answer
    for answer in ("dry-stone", "drystone"):
        assert mark_item(TEXT, ["Dry Stone "], answer).marks == 0, answer


def test_curly_apostrophes_match() -> None:
    assert mark_item(TEXT, ["farmer's"], "farmer\u2019s").marks == 1
    assert mark_item(TEXT, ["farmer's"], "Farmer\u2018s").marks == 1
    assert mark_item(TEXT, ["farmer's"], "farmers").marks == 0


def test_text_alternatives() -> None:
    assert mark_item(TEXT, ["eleven", "11"], "11").marks == 1
    assert mark_item(TEXT, ["eleven", "11"], " Eleven").marks == 1


def test_single_choice() -> None:
    right = mark_item(MATCH, ["B"], "B")
    assert (right.marks, right.correct_choices, right.max_marks) == (1, None, 1)
    assert mark_item(MATCH, ["B"], "C").marks == 0


def test_choose_two_partial_and_order() -> None:
    both = mark_item(CHOOSE_TWO, ["B", "D"], ["D", "B"])
    assert (both.marks, both.correct_choices, both.max_marks) == (2, ["D", "B"], 2)
    one = mark_item(CHOOSE_TWO, ["B", "D"], ["B", "C"])
    assert (one.marks, one.correct_choices, one.answered) == (1, ["B"], 2)
    single = mark_item(CHOOSE_TWO, ["B", "D"], ["B"])
    assert (single.marks, single.answered) == (1, 1)


def test_unanswered_items() -> None:
    text = mark_item(TEXT, ["word"], None)
    assert (text.marks, text.answered, text.answer) == (0, 0, None)
    choice = mark_item(CHOOSE_TWO, ["B", "D"], None)
    assert (choice.marks, choice.answered, choice.correct_choices) == (0, 0, [])


def test_wrong_answer_type_raises() -> None:
    with pytest.raises(ValueError):
        mark_item(TEXT, ["word"], ["word"])
    with pytest.raises(ValueError):
        mark_item(CHOOSE_TWO, ["B", "D"], "B")


def test_score_full_marks() -> None:
    result = score(
        Skill.LISTENING,
        None,
        SessionMode.EXAM,
        listening_index(),
        listening_key(),
        dict(correct_answers(Skill.LISTENING)),
    )
    assert (result.raw_score, result.max_score, result.answered) == (40, 40, 40)
    assert result.band == D("9.0")
    keys = [i.spec.key for i in result.items]
    assert keys == sorted(keys)
    assert sum(i.marks for i in result.items) == result.raw_score


def test_score_practice_band_needs_every_answer() -> None:
    answers: dict[int, Any] = dict(correct_answers(Skill.LISTENING)) | {21: ["B"]}
    args = (listening_index(), listening_key(), answers)
    practice = score(Skill.LISTENING, None, SessionMode.PRACTICE, *args)
    assert (practice.raw_score, practice.answered, practice.band) == (39, 39, None)
    exam = score(Skill.LISTENING, None, SessionMode.EXAM, *args)
    assert exam.band == D("9.0")

    one_wrong: dict[int, Any] = dict(correct_answers(Skill.LISTENING)) | {1: "wrong"}
    complete = score(
        Skill.LISTENING, None, SessionMode.PRACTICE, listening_index(), listening_key(), one_wrong
    )
    assert (complete.raw_score, complete.band) == (39, D("9.0"))


def test_score_empty_answers() -> None:
    empty: dict[int, Any] = {}
    args = (reading_index(), reading_key(), empty)
    exam = score(Skill.READING, Module.ACADEMIC, SessionMode.EXAM, *args)
    assert (exam.raw_score, exam.answered, exam.band) == (0, 0, D("0.0"))
    practice = score(Skill.READING, Module.ACADEMIC, SessionMode.PRACTICE, *args)
    assert practice.band is None
    assert all(i.answer is None for i in practice.items)


def test_score_ignores_unknown_answer_numbers() -> None:
    answers: dict[int, Any] = dict(correct_answers(Skill.LISTENING))
    base = score(
        Skill.LISTENING, None, SessionMode.EXAM, listening_index(), listening_key(), answers
    )
    extra = score(
        Skill.LISTENING,
        None,
        SessionMode.EXAM,
        listening_index(),
        listening_key(),
        answers | {22: ["A"]},
    )
    assert extra == base


def test_score_reading_module() -> None:
    first_30: dict[int, Any] = {q: a for q, a in correct_answers(Skill.READING).items() if q <= 30}
    args = (reading_index(), reading_key(), first_30)
    academic = score(Skill.READING, Module.ACADEMIC, SessionMode.EXAM, *args)
    general = score(Skill.READING, Module.GENERAL_TRAINING, SessionMode.EXAM, *args)
    assert academic.raw_score == 30
    assert (academic.band, general.band) == (D("7.0"), D("6.0"))
