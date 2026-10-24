"""Shape of a test version's documents: student-visible content, answer key, staff content.

Asset fields (`audio`, `image`) hold R2 object keys, never URLs.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Option(_Strict):
    key: str
    text: str


class QuestionItem(_Strict):
    numbers: list[int] = Field(min_length=1)
    prompt: str | None = None
    options: list[Option] | None = None


class QuestionGroup(_Strict):
    type: Literal["multiple_choice", "fill_blank", "matching", "short_answer"]
    variant: str | None = None
    instructions: str
    title: str | None = None
    context: str | None = None
    word_limit: str | None = None
    options: list[Option] | None = None
    questions: list[QuestionItem]


class Paragraph(_Strict):
    label: str | None = None
    text: str


class Passage(_Strict):
    number: int
    title: str
    paragraphs: list[Paragraph]
    question_groups: list[QuestionGroup]


class ReadingContent(_Strict):
    passages: list[Passage]


class ListeningPart(_Strict):
    number: int
    title: str
    audio: str | None = None
    question_groups: list[QuestionGroup]


class ListeningContent(_Strict):
    parts: list[ListeningPart]


class WritingTask(_Strict):
    number: int
    prompt: str
    min_words: int
    image: str | None = None


class WritingContent(_Strict):
    tasks: list[WritingTask]


class SpeakingQuestion(_Strict):
    number: int
    text: str | None = None
    audio: str | None = None


class SpeakingTopic(_Strict):
    topic: str
    questions: list[SpeakingQuestion]


class SpeakingPart1(_Strict):
    topics: list[SpeakingTopic]


class CueCard(_Strict):
    task: str
    you_should_say: list[str] = []
    and_explain: str | None = None


class SpeakingPart2(_Strict):
    number: int
    cue_card: CueCard
    audio: str | None = None
    rounding_off: list[SpeakingQuestion] = []


class SpeakingPart3(_Strict):
    theme: str | None = None
    questions: list[SpeakingQuestion]


class SpeakingContent(_Strict):
    part1: SpeakingPart1 | None = None
    part2: SpeakingPart2 | None = None
    part3: SpeakingPart3 | None = None


class TranscriptLine(_Strict):
    speaker: str
    text: str


class StaffContent(_Strict):
    transcripts: dict[int, list[TranscriptLine]] = {}


Content = ReadingContent | ListeningContent | WritingContent | SpeakingContent
AnswerKey = dict[int, list[str]]


class Problem(BaseModel):
    code: str
    message: str
    severity: Literal["error", "warning"] = "error"
    section: int | None = None
    question: int | None = None
