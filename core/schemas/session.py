from pydantic import BaseModel


class AnswerInput(BaseModel):
    question: int
    # option keys for multiple choice, one key for matching, text otherwise; null clears
    value: str | list[str] | None
