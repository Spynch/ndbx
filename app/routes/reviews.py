from typing import Any

from cassandra import DriverException
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pymongo.errors import PyMongoError

from app.http_utils import (
    invalid_field_response,
    parse_object_id,
    parse_uint_parameter,
    read_json_payload,
)
from app.reviews import (
    create_event_review,
    is_valid_review_comment,
    is_valid_review_rating,
    list_event_reviews,
    parse_review_id,
    refresh_event_title_reviews_cache,
    update_event_review,
)
from app.routes.common import append_cookie_for_get_if_exists, require_authenticated_user_for_post
from app.session import set_session_cookie

router = APIRouter()


def _event_not_found_response() -> JSONResponse:
    return JSONResponse(status_code=404, content={"message": "Event not found"})


def _review_not_found_response() -> JSONResponse:
    return JSONResponse(status_code=404, content={"message": "Review not found"})


def _load_event_title(request: Request, event_id: str) -> tuple[str, str] | None:
    object_id = parse_object_id(event_id)
    if object_id is None:
        return None

    try:
        event = request.app.state.mongodb["events"].find_one({"_id": object_id}, {"title": 1})
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="MongoDB is unavailable") from exc

    if event is None:
        return None

    title = event.get("title")
    if not isinstance(title, str):
        title = ""

    return str(object_id), title


@router.post("/events/{event_id}/reviews")
async def create_review(event_id: str, request: Request) -> Response:
    auth_result = require_authenticated_user_for_post(request)
    if isinstance(auth_result, Response):
        return auth_result

    sid, user_id = auth_result
    payload = await read_json_payload(request)

    comment: Any = payload.get("comment") if isinstance(payload, dict) else None
    if not is_valid_review_comment(comment):
        response = invalid_field_response("comment")
        set_session_cookie(response, sid, request.app.state.settings.session_ttl)
        return response

    rating: Any = payload.get("rating") if isinstance(payload, dict) else None
    if not is_valid_review_rating(rating):
        response = invalid_field_response("rating")
        set_session_cookie(response, sid, request.app.state.settings.session_ttl)
        return response

    event = _load_event_title(request, event_id)
    if event is None:
        response = _event_not_found_response()
        set_session_cookie(response, sid, request.app.state.settings.session_ttl)
        return response

    canonical_event_id, event_title = event
    try:
        review_id = create_event_review(request, canonical_event_id, user_id, comment, rating)
    except DriverException as exc:
        raise HTTPException(status_code=503, detail="Cassandra is unavailable") from exc

    if review_id is None:
        response = JSONResponse(status_code=409, content={"message": "Already exists"})
        set_session_cookie(response, sid, request.app.state.settings.session_ttl)
        return response

    try:
        refresh_event_title_reviews_cache(request, event_title)
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="MongoDB is unavailable") from exc
    except DriverException as exc:
        raise HTTPException(status_code=503, detail="Cassandra is unavailable") from exc

    response = JSONResponse(status_code=201, content={"id": str(review_id)})
    set_session_cookie(response, sid, request.app.state.settings.session_ttl)
    return response


@router.get("/events/{event_id}/reviews")
def get_reviews(event_id: str, request: Request) -> Response:
    try:
        limit = parse_uint_parameter(request, "limit")
    except ValueError:
        response = invalid_field_response("limit")
        append_cookie_for_get_if_exists(request, response)
        return response

    try:
        offset = parse_uint_parameter(request, "offset")
    except ValueError:
        response = invalid_field_response("offset")
        append_cookie_for_get_if_exists(request, response)
        return response

    event = _load_event_title(request, event_id)
    if event is None:
        response = _event_not_found_response()
        append_cookie_for_get_if_exists(request, response)
        return response

    canonical_event_id, _ = event
    try:
        reviews = list_event_reviews(request, canonical_event_id, limit, offset)
    except DriverException as exc:
        raise HTTPException(status_code=503, detail="Cassandra is unavailable") from exc

    response = JSONResponse(status_code=200, content={"reviews": reviews, "count": len(reviews)})
    append_cookie_for_get_if_exists(request, response)
    return response


@router.patch("/events/{event_id}/reviews/{review_id}")
async def update_review(event_id: str, review_id: str, request: Request) -> Response:
    auth_result = require_authenticated_user_for_post(request)
    if isinstance(auth_result, Response):
        return auth_result

    sid, user_id = auth_result
    payload = await read_json_payload(request)
    if not isinstance(payload, dict):
        payload = {}

    comment: str | None = None
    if "comment" in payload:
        raw_comment = payload.get("comment")
        if not is_valid_review_comment(raw_comment):
            response = invalid_field_response("comment")
            set_session_cookie(response, sid, request.app.state.settings.session_ttl)
            return response
        comment = raw_comment

    rating: int | None = None
    if "rating" in payload:
        raw_rating = payload.get("rating")
        if not is_valid_review_rating(raw_rating):
            response = invalid_field_response("rating")
            set_session_cookie(response, sid, request.app.state.settings.session_ttl)
            return response
        rating = raw_rating

    event = _load_event_title(request, event_id)
    if event is None:
        response = _event_not_found_response()
        set_session_cookie(response, sid, request.app.state.settings.session_ttl)
        return response

    parsed_review_id = parse_review_id(review_id)
    if parsed_review_id is None:
        response = _review_not_found_response()
        set_session_cookie(response, sid, request.app.state.settings.session_ttl)
        return response

    canonical_event_id, event_title = event
    try:
        updated = update_event_review(
            request,
            canonical_event_id,
            user_id,
            parsed_review_id,
            comment,
            rating,
        )
    except DriverException as exc:
        raise HTTPException(status_code=503, detail="Cassandra is unavailable") from exc

    if not updated:
        response = _review_not_found_response()
        set_session_cookie(response, sid, request.app.state.settings.session_ttl)
        return response

    try:
        refresh_event_title_reviews_cache(request, event_title)
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="MongoDB is unavailable") from exc
    except DriverException as exc:
        raise HTTPException(status_code=503, detail="Cassandra is unavailable") from exc

    response = Response(status_code=204)
    set_session_cookie(response, sid, request.app.state.settings.session_ttl)
    return response
