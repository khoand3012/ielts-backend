import pytest
from pydantic import ValidationError

from core.models.enums import Skill
from core.schemas.content import AnswerKey
from core.services.content import asset_keys, item_index, parse_content, validate_version
from tests.content_factories import (
    listening_content,
    listening_key,
    reading_content,
    reading_key,
    speaking_content,
    valid_cases,
    writing_content,
)


def codes(skill: Skill, content: dict[str, object], key: AnswerKey) -> set[str]:
    return {p.code for p in validate_version(skill, parse_content(skill, content), key)}


def test_valid_versions_have_no_problems() -> None:
    for skill, content, key in valid_cases():
        assert validate_version(skill, parse_content(skill, content), key) == []


def test_reading_needs_three_passages() -> None:
    c = reading_content()
    c["passages"].pop()
    assert "wrong_section_count" in codes(Skill.READING, c, reading_key())


def test_listening_needs_four_parts() -> None:
    c = listening_content()
    c["parts"].pop()
    assert "wrong_section_count" in codes(Skill.LISTENING, c, listening_key())


def test_writing_needs_tasks_1_and_2() -> None:
    c = writing_content()
    c["tasks"][1]["number"] = 3
    assert "wrong_section_count" in codes(Skill.WRITING, c, {})


def test_question_numbers_must_be_1_to_40() -> None:
    c = reading_content()
    c["passages"][0]["question_groups"][0]["questions"][6]["numbers"] = [6]
    assert "bad_question_numbers" in codes(Skill.READING, c, reading_key())


def test_every_question_needs_an_answer() -> None:
    key = reading_key()
    del key[12]
    problems = validate_version(Skill.READING, parse_content(Skill.READING, reading_content()), key)
    missing = [p for p in problems if p.code == "missing_answer"]
    assert [p.question for p in missing] == [12]
    assert missing[0].section == 1
    assert missing[0].severity == "error"


def test_choose_two_item_keyed_by_first_number() -> None:
    idx = item_index(Skill.LISTENING, parse_content(Skill.LISTENING, listening_content()))
    assert idx[21].numbers == (21, 22)
    assert idx[21].max_select == 2
    assert idx[21].kind == "choice"
    assert 22 not in idx
    assert idx[16].kind == "single_choice"
    assert idx[1].kind == "text"


def test_choose_two_key_needs_two_letters() -> None:
    key = listening_key()
    key[21] = ["B"]
    assert "wrong_answer_count" in codes(Skill.LISTENING, listening_content(), key)


def test_matching_key_needs_one_letter() -> None:
    key = listening_key()
    key[16] = ["A", "B"]
    assert "wrong_answer_count" in codes(Skill.LISTENING, listening_content(), key)


def test_answer_must_be_an_option() -> None:
    key = listening_key()
    key[11] = ["Z"]
    assert "answer_not_an_option" in codes(Skill.LISTENING, listening_content(), key)


def test_unknown_answer_key() -> None:
    key = listening_key()
    key[22] = ["D"]
    assert "unknown_answer_key" in codes(Skill.LISTENING, listening_content(), key)


def test_writing_has_no_answer_key() -> None:
    assert "unknown_answer_key" in codes(Skill.WRITING, writing_content(), {1: ["x"]})


def test_listening_part_needs_audio() -> None:
    c = listening_content()
    c["parts"][0]["audio"] = None
    problems = validate_version(Skill.LISTENING, parse_content(Skill.LISTENING, c), listening_key())
    assert [(p.code, p.section) for p in problems] == [("missing_audio", 1)]


def test_writing_task1_needs_image() -> None:
    c = writing_content()
    c["tasks"][0]["image"] = None
    assert codes(Skill.WRITING, c, {}) == {"missing_image"}


def test_speaking_question_needs_text_or_audio() -> None:
    c = speaking_content()
    c["part3"]["questions"][0]["text"] = None
    assert codes(Skill.SPEAKING, c, {}) == {"speaking_question_empty"}


def test_speaking_cue_card_needs_text() -> None:
    c = speaking_content()
    c["part2"]["cue_card"]["task"] = "  "
    assert "missing_cue_card" in codes(Skill.SPEAKING, c, {})


def test_speaking_missing_part() -> None:
    c = speaking_content()
    c["part3"] = None
    assert "wrong_section_count" in codes(Skill.SPEAKING, c, {})


def test_speaking_numbers_in_order() -> None:
    c = speaking_content()
    c["part3"]["questions"][0]["number"] = 9
    assert "bad_question_numbers" in codes(Skill.SPEAKING, c, {})


def test_speaking_items_are_recordings() -> None:
    idx = item_index(Skill.SPEAKING, parse_content(Skill.SPEAKING, speaking_content()))
    assert sorted(idx) == [1, 2, 3, 4, 5]
    assert {s.kind for s in idx.values()} == {"recording"}


def test_extra_fields_rejected() -> None:
    with pytest.raises(ValidationError):
        parse_content(Skill.WRITING, {**writing_content(), "answers": {}})


def test_asset_keys_lists_audio_and_images() -> None:
    lc = parse_content(Skill.LISTENING, listening_content())
    assert asset_keys(Skill.LISTENING, lc) == [f"tests/f/part{n}.mp3" for n in range(1, 5)]
    wc = parse_content(Skill.WRITING, writing_content())
    assert asset_keys(Skill.WRITING, wc) == ["tests/f/task1.svg"]
    sc = parse_content(Skill.SPEAKING, speaking_content())
    assert asset_keys(Skill.SPEAKING, sc) == ["tests/f/q2.mp3"]
