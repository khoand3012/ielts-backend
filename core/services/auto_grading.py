"""Marking rules for Reading and Listening (spec §3): pure functions, no database.

The backend is the only place right and wrong are decided; the result endpoint shows the
marks computed here, so they always agree with the stored total.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from core.models.enums import Module, SessionMode, Skill
from core.schemas.content import AnswerKey
from core.services.bands import raw_to_band
from core.services.content import ItemSpec

AnswerValue = str | list[str]

# Phones type curly quotes by default; they are the same mark as the straight ones.
_STRAIGHTEN = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"'})


def normalize(text: str) -> str:
    """Trim, lowercase, collapse whitespace and straighten quotes. Nothing else."""
    return " ".join(text.lower().translate(_STRAIGHTEN).split())


@dataclass(frozen=True)
class MarkedItem:
    spec: ItemSpec
    answer: AnswerValue | None
    accepted: list[str]
    # choice items: the student's picks that are in the key, in their order
    correct_choices: list[str] | None
    marks: int
    # question numbers this item counts as answered
    answered: int

    @property
    def max_marks(self) -> int:
        return len(self.spec.numbers)


@dataclass(frozen=True)
class Score:
    items: list[MarkedItem]
    raw_score: int
    max_score: int
    answered: int
    band: Decimal | None


def mark_item(spec: ItemSpec, accepted: list[str], answer: AnswerValue | None) -> MarkedItem:
    key = {normalize(a) for a in accepted}
    if spec.kind == "choice":
        if answer is not None and not isinstance(answer, list):
            raise ValueError(f"question {spec.key} expects a list of options")
        picks = answer or []
        right = [p for p in picks if normalize(p) in key][: len(spec.numbers)]
        answered = min(len(picks), len(spec.numbers))
        return MarkedItem(spec, answer, accepted, right, len(right), answered)
    if spec.kind in ("text", "single_choice"):
        if answer is not None and not isinstance(answer, str):
            raise ValueError(f"question {spec.key} expects a single answer")
        marks = int(answer is not None and normalize(answer) in key)
        return MarkedItem(spec, answer, accepted, None, marks, int(answer is not None))
    raise ValueError(f"question {spec.key} is a {spec.kind} item, which is not auto-marked")


def score(
    skill: Skill,
    module: Module | None,
    mode: SessionMode,
    index: dict[int, ItemSpec],
    key: AnswerKey,
    answers: dict[int, Any],
) -> Score:
    """Mark every item; a practice session gets a band only if every question is answered."""
    items = [mark_item(index[q], key.get(q, []), answers.get(q)) for q in sorted(index)]
    raw = sum(i.marks for i in items)
    max_score = sum(i.max_marks for i in items)
    answered = sum(i.answered for i in items)
    band = (
        None
        if mode == SessionMode.PRACTICE and answered < max_score
        else raw_to_band(skill, module, raw)
    )
    return Score(items, raw, max_score, answered, band)
