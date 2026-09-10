"""Convert internal failures into data-free public errors."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class PublicError(BaseModel):
    """A stable error safe to return through an API or log as public metadata."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    message: str

    def __str__(self) -> str:
        return self.message


def redact_exception(exc: BaseException) -> PublicError:
    """Discard exception detail, which may contain tokens or source content."""
    del exc
    return PublicError(code="internal_error", message="The operation could not be completed.")
