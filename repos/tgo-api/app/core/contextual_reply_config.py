"""Channel-scoped, expiring answers reviewed by the operator."""

from datetime import UTC, datetime

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class ReviewedFAQ(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    question: str = Field(min_length=1, max_length=500)
    subject: str = Field(min_length=1, max_length=120)
    answer: str = Field(min_length=1, max_length=1000)
    valid_until: AwareDatetime

    def answers(self, question: str) -> bool:
        return (
            question.strip() == self.question and self.subject in question
            and datetime.now(UTC) < self.valid_until
        )
