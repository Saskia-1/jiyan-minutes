"""Private local search endpoint."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from chat_to_howto.api.export_jobs import SafeValidationRoute
from chat_to_howto.domain.search import SearchPage, SearchRequest
from chat_to_howto.services.search import ChatSearchService


_OPAQUE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
_OPAQUE = re.compile(_OPAQUE_PATTERN)


class SelectionSearchRequest(BaseModel):
    """Search inputs using P0's browser-safe opaque conversation handles."""

    model_config = ConfigDict(
        frozen=True, extra="forbid", strict=True, hide_input_in_errors=True
    )

    source_id: str = Field(min_length=1, max_length=128, pattern=_OPAQUE_PATTERN)
    account_id: str = Field(min_length=1, max_length=256)
    query: str = Field(default="", max_length=512)
    conversation_selection_ids: tuple[str, ...] = Field(min_length=1, max_length=50)
    start_time: datetime
    end_time: datetime
    limit: int = Field(default=25, ge=1, le=100)
    cursor: str | None = Field(default=None, min_length=1, max_length=2048)

    @field_validator("conversation_selection_ids", mode="before")
    @classmethod
    def _parse_handles(cls, value: object) -> object:
        if isinstance(value, list) and len(value) <= 50:
            return tuple(value)
        return value

    @field_validator("conversation_selection_ids")
    @classmethod
    def _validate_handles(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value) or any(
            not _OPAQUE.fullmatch(item) or item in {".", ".."}
            for item in value
        ):
            raise ValueError("selection handles must be unique opaque values")
        return value

    @field_validator("start_time", "end_time", mode="before")
    @classmethod
    def _parse_time(cls, value: object) -> object:
        if isinstance(value, str) and 1 <= len(value) <= 64:
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return value
        return value

    @field_validator("start_time", "end_time")
    @classmethod
    def _normalize_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("search times must be timezone-aware")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def _validate_range(self) -> "SelectionSearchRequest":
        if self.start_time >= self.end_time:
            raise ValueError("search start must precede end")
        return self


def create_search_router(
    *,
    get_search: Callable[[], ChatSearchService],
    require_session: Callable[[], str],
    require_csrf: Callable[[], object],
    resolve_search: Callable[[SelectionSearchRequest, str], SearchRequest] | None = None,
) -> APIRouter:
    """Create a POST-only search route so query text never enters URL logs."""
    router = APIRouter(
        prefix="/api/search",
        tags=["search"],
        route_class=SafeValidationRoute,
    )

    @router.post("/from-selections", response_model=SearchPage)
    def search_from_selections(
        request: SelectionSearchRequest,
        service: ChatSearchService = Depends(get_search),
        session_id: str = Depends(require_session),
        _csrf: object = Depends(require_csrf),
    ) -> SearchPage:
        if resolve_search is None:
            raise HTTPException(status_code=404, detail="selection search is unavailable")
        try:
            resolved = resolve_search(request, session_id)
            return service.search(resolved, session_id=session_id)
        except PermissionError:
            raise HTTPException(status_code=403, detail="search is unavailable") from None
        except ValueError:
            raise HTTPException(status_code=422, detail="invalid search cursor") from None
        except Exception:
            raise HTTPException(status_code=500, detail="local search failed") from None

    @router.post("", response_model=SearchPage)
    def search(
        request: SearchRequest,
        service: ChatSearchService = Depends(get_search),
        session_id: str = Depends(require_session),
        _csrf: object = Depends(require_csrf),
    ) -> SearchPage:
        try:
            return service.search(request, session_id=session_id)
        except PermissionError:
            raise HTTPException(status_code=403, detail="search is unavailable") from None
        except ValueError:
            raise HTTPException(status_code=422, detail="invalid search cursor") from None
        except Exception:
            raise HTTPException(status_code=500, detail="local search failed") from None

    return router
