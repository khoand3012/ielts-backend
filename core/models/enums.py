from enum import StrEnum


class Skill(StrEnum):
    READING = "reading"
    LISTENING = "listening"
    WRITING = "writing"
    SPEAKING = "speaking"


class Module(StrEnum):
    """Which Reading test a version is: it picks the raw-mark band table."""

    ACADEMIC = "academic"
    GENERAL_TRAINING = "general_training"


class VersionStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    RETIRED = "retired"


class SessionMode(StrEnum):
    PRACTICE = "practice"
    EXAM = "exam"


class SessionStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    SUBMITTED = "submitted"


class SubmitReason(StrEnum):
    STUDENT = "student"
    TIMEOUT = "timeout"


class GradingMethod(StrEnum):
    AUTO = "auto"
    AI = "ai"
    HUMAN = "human"


def check_in(column: str, enum: type[StrEnum]) -> str:
    """SQL for a check constraint limiting `column` to the enum's values."""
    values = ", ".join(f"'{member.value}'" for member in enum)
    return f"{column} IN ({values})"
