"""Valid test content per skill, as plain dicts. Tests copy and mutate one thing."""

from typing import Any

from core.models.enums import Skill

ABC = [{"key": k, "text": f"option {k}"} for k in "ABC"]
ABCDE = [{"key": k, "text": f"option {k}"} for k in "ABCDE"]
TFNG = [{"key": k, "text": k} for k in ("TRUE", "FALSE", "NOT GIVEN")]


def _fill(numbers: range) -> dict[str, Any]:
    return {
        "type": "fill_blank",
        "instructions": "Write ONE WORD ONLY.",
        "word_limit": "ONE WORD ONLY",
        "questions": [{"numbers": [n], "prompt": f"gap {n} ____"} for n in numbers],
    }


def _mc(numbers: range) -> dict[str, Any]:
    return {
        "type": "multiple_choice",
        "instructions": "Choose A, B or C.",
        "questions": [{"numbers": [n], "prompt": f"question {n}", "options": ABC} for n in numbers],
    }


def _matching(numbers: range, options: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "type": "matching",
        "instructions": "Choose the correct letter.",
        "options": options,
        "questions": [{"numbers": [n], "prompt": f"item {n}"} for n in numbers],
    }


def _paragraphs() -> list[dict[str, Any]]:
    return [{"label": None, "text": "Some passage text."}]


def reading_content() -> dict[str, Any]:
    tfng = {
        "type": "multiple_choice",
        "variant": "true_false_not_given",
        "instructions": "TRUE, FALSE or NOT GIVEN.",
        "options": TFNG,
        "questions": [{"numbers": [n], "prompt": f"statement {n}"} for n in range(1, 8)],
    }
    return {
        "passages": [
            {
                "number": 1,
                "title": "P1",
                "paragraphs": _paragraphs(),
                "question_groups": [tfng, _fill(range(8, 14))],
            },
            {
                "number": 2,
                "title": "P2",
                "paragraphs": _paragraphs(),
                "question_groups": [_mc(range(14, 27))],
            },
            {
                "number": 3,
                "title": "P3",
                "paragraphs": _paragraphs(),
                "question_groups": [_matching(range(27, 41), ABCDE)],
            },
        ]
    }


def reading_key() -> dict[int, list[str]]:
    key: dict[int, list[str]] = {n: ["TRUE"] for n in range(1, 8)}
    key |= {n: [f"word{n}"] for n in range(8, 14)}
    key |= {n: ["A"] for n in range(14, 27)}
    key |= {n: ["B"] for n in range(27, 41)}
    return key


def listening_content() -> dict[str, Any]:
    choose_two = {
        "type": "multiple_choice",
        "instructions": "Choose TWO letters, A-E.",
        "questions": [{"numbers": [21, 22], "prompt": "Which TWO?", "options": ABCDE}],
    }
    return {
        "parts": [
            {
                "number": 1,
                "title": "L1",
                "audio": "tests/f/part1.mp3",
                "question_groups": [_fill(range(1, 11))],
            },
            {
                "number": 2,
                "title": "L2",
                "audio": "tests/f/part2.mp3",
                "question_groups": [_mc(range(11, 16)), _matching(range(16, 21), ABC)],
            },
            {
                "number": 3,
                "title": "L3",
                "audio": "tests/f/part3.mp3",
                "question_groups": [
                    choose_two,
                    _mc(range(23, 27)),
                    _matching(range(27, 31), ABCDE),
                ],
            },
            {
                "number": 4,
                "title": "L4",
                "audio": "tests/f/part4.mp3",
                "question_groups": [_fill(range(31, 41))],
            },
        ]
    }


def listening_key() -> dict[int, list[str]]:
    key: dict[int, list[str]] = {n: [f"word{n}"] for n in range(1, 11)}
    key |= {n: ["A"] for n in range(11, 16)}
    key |= {n: ["C"] for n in range(16, 21)}
    key |= {21: ["B", "D"]}
    key |= {n: ["B"] for n in range(23, 27)}
    key |= {n: ["E"] for n in range(27, 31)}
    key |= {n: [f"word{n}", f"alt{n}"] for n in range(31, 41)}
    return key


def listening_staff() -> dict[str, Any]:
    return {
        "transcripts": {
            n: [{"speaker": "Narrator", "text": f"Secret transcript line for part {n}."}]
            for n in range(1, 5)
        }
    }


def writing_content() -> dict[str, Any]:
    return {
        "tasks": [
            {
                "number": 1,
                "prompt": "Describe the chart.",
                "min_words": 150,
                "image": "tests/f/task1.svg",
            },
            {"number": 2, "prompt": "Discuss.", "min_words": 250, "image": None},
        ]
    }


def speaking_content() -> dict[str, Any]:
    return {
        "part1": {
            "topics": [
                {
                    "topic": "Home",
                    "questions": [
                        {"number": 1, "text": "Where do you live?", "audio": None},
                        {"number": 2, "text": None, "audio": "tests/f/q2.mp3"},
                    ],
                }
            ]
        },
        "part2": {
            "number": 3,
            "cue_card": {
                "task": "Describe a teacher.",
                "you_should_say": ["who", "what"],
                "and_explain": "and explain why.",
            },
            "audio": None,
            "rounding_off": [{"number": 4, "text": "Do you still see them?", "audio": None}],
        },
        "part3": {
            "theme": "Education",
            "questions": [{"number": 5, "text": "Why learn?", "audio": None}],
        },
    }


def valid_cases() -> list[tuple[Skill, dict[str, Any], dict[int, list[str]]]]:
    return [
        (Skill.READING, reading_content(), reading_key()),
        (Skill.LISTENING, listening_content(), listening_key()),
        (Skill.WRITING, writing_content(), {}),
        (Skill.SPEAKING, speaking_content(), {}),
    ]
