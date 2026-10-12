"""Explicit local mixed-source search endpoint."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException

from chat_to_howto.api.export_jobs import SafeValidationRoute
from chat_to_howto.domain.mixed_sources import MixedSearchPage, MixedSearchRequest
from chat_to_howto.services.mixed_search import MixedSearchService


def create_mixed_search_router(
    *, get_service: Callable[[], MixedSearchService],
    require_session: Callable[[], str], require_csrf: Callable[[], object],
) -> APIRouter:
    router = APIRouter(prefix="/api/mixed-search", tags=["mixed-search"], route_class=SafeValidationRoute)

    @router.post("", response_model=MixedSearchPage)
    def search(request: MixedSearchRequest, service: MixedSearchService = Depends(get_service),
               session_id: str = Depends(require_session), _csrf: object = Depends(require_csrf)) -> MixedSearchPage:
        try:
            return MixedSearchPage(hits=service.search(request, session_id=session_id))
        except PermissionError:
            raise HTTPException(status_code=403, detail="source selection unavailable") from None
        except ValueError:
            raise HTTPException(status_code=422, detail="mixed search selection invalid") from None
        except Exception:
            raise HTTPException(status_code=500, detail="local mixed search failed") from None

    return router
