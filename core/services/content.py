"""Everything that knows the shape of test content: parsing, the answerable items, the asset
keys it references, and the problems list that gates publishing."""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel

from core.models.enums import Skill
from core.schemas.content import (
    AnswerKey,
    Content,
    ListeningContent,
    Problem,
    QuestionGroup,
    QuestionItem,
    ReadingContent,
    SpeakingContent,
    SpeakingQuestion,
    WritingContent,
)

_CONTENT_MODELS: dict[Skill, type[BaseModel]] = {
    Skill.READING: ReadingContent,
    Skill.LISTENING: ListeningContent,
    Skill.WRITING: WritingContent,
    Skill.SPEAKING: SpeakingContent,
}

# Fixed IELTS structure: sections per skill, and the question numbers Reading/Listening use.
_SECTION_COUNTS = {Skill.READING: 3, Skill.LISTENING: 4}
_RL_NUMBERS = list(range(1, 41))

ItemKind = Literal["choice", "single_choice", "text", "essay", "recording"]


@dataclass(frozen=True)
class ItemSpec:
    key: int
    numbers: tuple[int, ...]
    kind: ItemKind
    option_keys: frozenset[str] = frozenset()
    max_select: int = 1
    section: int | None = None


def parse_content(skill: Skill, data: dict[str, Any]) -> Content:
    content: Content = _CONTENT_MODELS[skill].model_validate(data)  # type: ignore[assignment]
    return content


def _rl_items(content: Content) -> Iterator[tuple[int, QuestionGroup, QuestionItem]]:
    if isinstance(content, ReadingContent):
        sections = [(p.number, p.question_groups) for p in content.passages]
    elif isinstance(content, ListeningContent):
        sections = [(p.number, p.question_groups) for p in content.parts]
    else:
        return
    for number, groups in sections:
        for group in groups:
            for item in group.questions:
                yield number, group, item


def _speaking_questions(content: SpeakingContent) -> Iterator[tuple[int, SpeakingQuestion]]:
    """Every recorded answer in order, with its part. Part 2's long turn is a question too."""
    if content.part1:
        for topic in content.part1.topics:
            for q in topic.questions:
                yield 1, q
    if content.part2:
        p2 = content.part2
        yield 2, SpeakingQuestion(number=p2.number, text=p2.cue_card.task, audio=p2.audio)
        for q in p2.rounding_off:
            yield 2, q
    if content.part3:
        for q in content.part3.questions:
            yield 3, q


def _rl_spec(section: int, group: QuestionGroup, item: QuestionItem) -> ItemSpec:
    numbers = tuple(item.numbers)
    options = frozenset(o.key for o in (item.options or group.options or []))
    if group.type == "multiple_choice":
        return ItemSpec(numbers[0], numbers, "choice", options, len(numbers), section)
    if group.type == "matching":
        return ItemSpec(numbers[0], numbers, "single_choice", options, 1, section)
    return ItemSpec(numbers[0], numbers, "text", section=section)


def item_index(skill: Skill, content: Content) -> dict[int, ItemSpec]:
    """Answerable items keyed by their first question number."""
    specs: list[ItemSpec] = []
    if isinstance(content, ReadingContent | ListeningContent):
        specs = [_rl_spec(s, g, i) for s, g, i in _rl_items(content)]
    elif isinstance(content, WritingContent):
        specs = [ItemSpec(t.number, (t.number,), "essay", section=t.number) for t in content.tasks]
    elif isinstance(content, SpeakingContent):
        specs = [
            ItemSpec(q.number, (q.number,), "recording", section=part)
            for part, q in _speaking_questions(content)
        ]
    return {s.key: s for s in specs}


def asset_keys(skill: Skill, content: Content) -> list[str]:
    keys: list[str | None] = []
    if isinstance(content, ListeningContent):
        keys = [p.audio for p in content.parts]
    elif isinstance(content, WritingContent):
        keys = [t.image for t in content.tasks]
    elif isinstance(content, SpeakingContent):
        keys = [q.audio for _, q in _speaking_questions(content)]
    return [k for k in keys if k]


def _blank(text: str | None) -> bool:
    return text is None or not text.strip()


def validate_version(skill: Skill, content: Content, answer_key: AnswerKey) -> list[Problem]:
    """Every problem in a version. Any `error` blocks publishing."""
    problems: list[Problem] = []
    index = item_index(skill, content)

    if isinstance(content, ReadingContent | ListeningContent):
        problems += _validate_rl(skill, content, index, answer_key)
    elif isinstance(content, WritingContent):
        problems += _validate_writing(content)
    elif isinstance(content, SpeakingContent):
        problems += _validate_speaking(content)

    for number in answer_key:
        if number not in index or skill in (Skill.WRITING, Skill.SPEAKING):
            problems.append(
                Problem(
                    code="unknown_answer_key",
                    message=f"answer key has an entry for {number}, which is not a question key",
                    question=number,
                )
            )
    return problems


def _validate_rl(
    skill: Skill,
    content: ReadingContent | ListeningContent,
    index: dict[int, ItemSpec],
    answer_key: AnswerKey,
) -> list[Problem]:
    problems: list[Problem] = []
    sections = content.passages if isinstance(content, ReadingContent) else content.parts
    expected = _SECTION_COUNTS[skill]
    if len(sections) != expected:
        problems.append(
            Problem(
                code="wrong_section_count",
                message=f"{skill} needs {expected} sections, found {len(sections)}",
            )
        )

    numbers = [n for _, _, item in _rl_items(content) for n in item.numbers]
    if numbers != _RL_NUMBERS:
        problems.append(
            Problem(
                code="bad_question_numbers",
                message="questions must be numbered 1-40 in order, each number once",
            )
        )

    for spec in index.values():
        answers = answer_key.get(spec.key, [])
        where = {"section": spec.section, "question": spec.key}
        if not answers:
            problems.append(
                Problem(
                    code="missing_answer", message=f"question {spec.key} has no answer", **where
                )
            )
            continue
        if spec.kind in ("choice", "single_choice"):
            if len(answers) != spec.max_select:
                problems.append(
                    Problem(
                        code="wrong_answer_count",
                        message=f"question {spec.key} needs {spec.max_select} answer(s), "
                        f"has {len(answers)}",
                        **where,
                    )
                )
            for answer in answers:
                if answer not in spec.option_keys:
                    problems.append(
                        Problem(
                            code="answer_not_an_option",
                            message=f"answer {answer!r} to question {spec.key} is not an option",
                            **where,
                        )
                    )

    if isinstance(content, ListeningContent):
        for part in content.parts:
            if part.audio is None:
                problems.append(
                    Problem(
                        code="missing_audio",
                        message=f"part {part.number} has no audio",
                        section=part.number,
                    )
                )
    return problems


def _validate_writing(content: WritingContent) -> list[Problem]:
    problems: list[Problem] = []
    if sorted(t.number for t in content.tasks) != [1, 2]:
        problems.append(
            Problem(code="wrong_section_count", message="writing needs exactly tasks 1 and 2")
        )
    for task in content.tasks:
        if task.number == 1 and task.image is None:
            problems.append(Problem(code="missing_image", message="task 1 has no image", section=1))
    return problems


def _validate_speaking(content: SpeakingContent) -> list[Problem]:
    problems: list[Problem] = []
    for n, section in ((1, content.part1), (2, content.part2), (3, content.part3)):
        if section is None:
            problems.append(
                Problem(code="wrong_section_count", message=f"part {n} is missing", section=n)
            )

    questions = list(_speaking_questions(content))
    if [q.number for _, q in questions] != list(range(1, len(questions) + 1)):
        problems.append(
            Problem(
                code="bad_question_numbers",
                message="speaking questions must be numbered 1, 2, 3... in order across parts",
            )
        )

    long_turn = content.part2.number if content.part2 else None
    for part, q in questions:
        where = {"section": part, "question": q.number}
        if q.number == long_turn and part == 2:
            if _blank(q.text):
                problems.append(
                    Problem(code="missing_cue_card", message="part 2 has no cue card", **where)
                )
        elif _blank(q.text) and q.audio is None:
            problems.append(
                Problem(
                    code="speaking_question_empty",
                    message=f"question {q.number} has neither text nor audio",
                    **where,
                )
            )
    return problems
