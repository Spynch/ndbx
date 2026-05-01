import re
from typing import Any

from cassandra import DriverException
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pymongo.errors import DuplicateKeyError, PyMongoError

from app.http_utils import (
    EVENT_CATEGORIES,
    created_by_match_filter,
    event_started_date,
    invalid_field_response,
    is_uint_value,
    parse_object_id,
    parse_rfc3339,
    parse_uint_parameter,
    parse_yyyymmdd_parameter,
    parse_yyyymmdd_value,
    read_json_payload,
    safe_get_json_field,
    serialize_event,
)
from app.reactions import (
    REACTION_DISLIKE,
    REACTION_LIKE,
    empty_reactions,
    invalidate_event_title_reactions_cache,
    reactions_for_event_title,
    reactions_for_event_titles,
    should_include_reactions,
    upsert_event_reaction,
)
from app.routes.common import append_cookie_for_get_if_exists, require_authenticated_user_for_post
from app.session import COOKIE_NAME, expire_session_cookie, set_session_cookie, utc_now_rfc3339

router = APIRouter()


@router.post("/events")
async def create_event(request: Request) -> Response:
    auth_result = require_authenticated_user_for_post(request)
    if isinstance(auth_result, Response):
        return auth_result

    sid, user_id = auth_result

    payload = await read_json_payload(request)

    title = safe_get_json_field(payload, "title")
    if title is None:
        response = invalid_field_response("title")
        set_session_cookie(response, sid, request.app.state.settings.session_ttl)
        return response

    address = safe_get_json_field(payload, "address")
    if address is None:
        response = invalid_field_response("address")
        set_session_cookie(response, sid, request.app.state.settings.session_ttl)
        return response

    started_at = safe_get_json_field(payload, "started_at")
    if started_at is None:
        response = invalid_field_response("started_at")
        set_session_cookie(response, sid, request.app.state.settings.session_ttl)
        return response

    if parse_rfc3339(started_at) is None:
        response = invalid_field_response("started_at")
        set_session_cookie(response, sid, request.app.state.settings.session_ttl)
        return response

    finished_at = safe_get_json_field(payload, "finished_at")
    if finished_at is None:
        response = invalid_field_response("finished_at")
        set_session_cookie(response, sid, request.app.state.settings.session_ttl)
        return response

    if parse_rfc3339(finished_at) is None:
        response = invalid_field_response("finished_at")
        set_session_cookie(response, sid, request.app.state.settings.session_ttl)
        return response

    description = payload.get("description", "")
    if not isinstance(description, str):
        response = invalid_field_response("description")
        set_session_cookie(response, sid, request.app.state.settings.session_ttl)
        return response

    event_document = {
        "title": title,
        "category": "other",
        "price": 0,
        "description": description,
        "location": {"address": address},
        "created_at": utc_now_rfc3339(),
        "created_by": user_id,
        "started_at": started_at,
        "finished_at": finished_at,
    }

    try:
        inserted = request.app.state.mongodb["events"].insert_one(event_document)
    except DuplicateKeyError:
        response = JSONResponse(status_code=409, content={"message": "event already exists"})
        set_session_cookie(response, sid, request.app.state.settings.session_ttl)
        return response
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="MongoDB is unavailable") from exc

    response = JSONResponse(status_code=201, content={"id": str(inserted.inserted_id)})
    set_session_cookie(response, sid, request.app.state.settings.session_ttl)
    return response


def _set_event_reaction(
    event_id: str,
    request: Request,
    like_value: int,
    expire_cookie_on_unauthorized: bool = False,
) -> Response:
    auth_result = require_authenticated_user_for_post(request)
    if isinstance(auth_result, Response):
        if expire_cookie_on_unauthorized and auth_result.status_code == 401:
            response = Response(status_code=401)
            expire_session_cookie(response, request.cookies.get(COOKIE_NAME, ""))
            return response
        return auth_result

    sid, user_id = auth_result
    object_id = parse_object_id(event_id)
    if object_id is None:
        response = JSONResponse(status_code=404, content={"message": "Event not found"})
        set_session_cookie(response, sid, request.app.state.settings.session_ttl)
        return response
    canonical_event_id = str(object_id)

    try:
        event_document = request.app.state.mongodb["events"].find_one({"_id": object_id}, {"title": 1})
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="MongoDB is unavailable") from exc

    if event_document is None:
        response = JSONResponse(status_code=404, content={"message": "Event not found"})
        set_session_cookie(response, sid, request.app.state.settings.session_ttl)
        return response

    try:
        upsert_event_reaction(request, canonical_event_id, user_id, like_value)
    except DriverException as exc:
        raise HTTPException(status_code=503, detail="Cassandra is unavailable") from exc

    event_title = event_document.get("title")
    if isinstance(event_title, str):
        invalidate_event_title_reactions_cache(request, event_title)

    response = Response(status_code=204)
    set_session_cookie(response, sid, request.app.state.settings.session_ttl)
    return response


@router.post("/events/{event_id}/like")
def like_event(event_id: str, request: Request) -> Response:
    return _set_event_reaction(event_id, request, REACTION_LIKE)


@router.post("/events/{event_id}/dislike")
def dislike_event(event_id: str, request: Request) -> Response:
    return _set_event_reaction(event_id, request, REACTION_DISLIKE, expire_cookie_on_unauthorized=True)


@router.get("/events")
def list_events(request: Request) -> Response:
    title_filter = request.query_params.get("title")
    event_id_filter = request.query_params.get("id")
    category_filter = request.query_params.get("category")
    city_filter = request.query_params.get("city")
    user_filter = request.query_params.get("user")

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

    try:
        price_from = parse_uint_parameter(request, "price_from")
    except ValueError:
        response = invalid_field_response("price_from")
        append_cookie_for_get_if_exists(request, response)
        return response

    try:
        price_to = parse_uint_parameter(request, "price_to")
    except ValueError:
        response = invalid_field_response("price_to")
        append_cookie_for_get_if_exists(request, response)
        return response

    try:
        date_from = parse_yyyymmdd_parameter(request, "date_from")
    except ValueError:
        response = invalid_field_response("date_from")
        append_cookie_for_get_if_exists(request, response)
        return response

    try:
        date_to = parse_yyyymmdd_parameter(request, "date_to")
    except ValueError:
        response = invalid_field_response("date_to")
        append_cookie_for_get_if_exists(request, response)
        return response

    legacy_date_from_raw = request.query_params.get("started_date_from")
    if date_from is None and legacy_date_from_raw is not None:
        try:
            date_from = parse_yyyymmdd_value(legacy_date_from_raw, "started_date_from")
        except ValueError:
            response = invalid_field_response("started_date_from")
            append_cookie_for_get_if_exists(request, response)
            return response

    legacy_date_to_raw = request.query_params.get("started_date_to")
    if date_to is None and legacy_date_to_raw is not None:
        try:
            date_to = parse_yyyymmdd_value(legacy_date_to_raw, "started_date_to")
        except ValueError:
            response = invalid_field_response("started_date_to")
            append_cookie_for_get_if_exists(request, response)
            return response

    if price_from is not None and price_to is not None and price_from > price_to:
        response = invalid_field_response("price_to")
        append_cookie_for_get_if_exists(request, response)
        return response

    if date_from is not None and date_to is not None and date_from > date_to:
        response = invalid_field_response("date_to")
        append_cookie_for_get_if_exists(request, response)
        return response

    if category_filter is not None and category_filter not in EVENT_CATEGORIES:
        response = invalid_field_response("category")
        append_cookie_for_get_if_exists(request, response)
        return response

    if city_filter is not None and city_filter.strip() == "":
        response = invalid_field_response("city")
        append_cookie_for_get_if_exists(request, response)
        return response

    if user_filter is not None and user_filter.strip() == "":
        response = invalid_field_response("user")
        append_cookie_for_get_if_exists(request, response)
        return response

    filters: dict[str, Any] = {}
    if title_filter is not None:
        filters["title"] = {"$regex": re.escape(title_filter)}

    if event_id_filter is not None:
        if event_id_filter.strip() == "":
            response = invalid_field_response("id")
            append_cookie_for_get_if_exists(request, response)
            return response

        event_object_id = parse_object_id(event_id_filter)
        if event_object_id is None:
            response = JSONResponse(status_code=200, content={"events": [], "count": 0})
            append_cookie_for_get_if_exists(request, response)
            return response

        filters["_id"] = event_object_id

    if category_filter is not None:
        filters["category"] = category_filter

    if city_filter is not None:
        filters["location.city"] = city_filter

    if price_from is not None or price_to is not None:
        price_filter: dict[str, int] = {}
        if price_from is not None:
            price_filter["$gte"] = price_from
        if price_to is not None:
            price_filter["$lte"] = price_to
        filters["price"] = price_filter

    if user_filter is not None:
        try:
            user_document = request.app.state.mongodb["users"].find_one({"username": user_filter}, {"_id": 1})
        except PyMongoError as exc:
            raise HTTPException(status_code=503, detail="MongoDB is unavailable") from exc

        if user_document is None:
            response = JSONResponse(status_code=200, content={"events": [], "count": 0})
            append_cookie_for_get_if_exists(request, response)
            return response

        filters.update(created_by_match_filter(str(user_document["_id"])))

    try:
        documents = list(request.app.state.mongodb["events"].find(filters))
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="MongoDB is unavailable") from exc

    if date_from is not None or date_to is not None:
        filtered_documents = []
        for document in documents:
            started_date = event_started_date(document)
            if started_date is None:
                continue

            if date_from is not None and started_date < date_from:
                continue

            if date_to is not None and started_date > date_to:
                continue

            filtered_documents.append(document)

        documents = filtered_documents

    if offset is not None:
        documents = documents[offset:]

    if limit is not None:
        documents = documents[:limit]

    events = [serialize_event(document) for document in documents]
    if should_include_reactions(request):
        try:
            reactions_by_title = reactions_for_event_titles(request, [event["title"] for event in events])
        except PyMongoError as exc:
            raise HTTPException(status_code=503, detail="MongoDB is unavailable") from exc
        except DriverException as exc:
            raise HTTPException(status_code=503, detail="Cassandra is unavailable") from exc

        for event in events:
            event["reactions"] = dict(reactions_by_title.get(event["title"], empty_reactions()))

    response = JSONResponse(status_code=200, content={"events": events, "count": len(events)})
    append_cookie_for_get_if_exists(request, response)
    return response


@router.get("/events/{event_id}")
def get_event(event_id: str, request: Request) -> Response:
    object_id = parse_object_id(event_id)
    if object_id is None:
        response = JSONResponse(status_code=404, content={"message": "Not found"})
        append_cookie_for_get_if_exists(request, response)
        return response

    try:
        document = request.app.state.mongodb["events"].find_one({"_id": object_id})
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="MongoDB is unavailable") from exc

    if document is None:
        response = JSONResponse(status_code=404, content={"message": "Not found"})
        append_cookie_for_get_if_exists(request, response)
        return response

    event = serialize_event(document)
    if should_include_reactions(request):
        try:
            event["reactions"] = reactions_for_event_title(request, event["title"])
        except PyMongoError as exc:
            raise HTTPException(status_code=503, detail="MongoDB is unavailable") from exc
        except DriverException as exc:
            raise HTTPException(status_code=503, detail="Cassandra is unavailable") from exc

    response = JSONResponse(status_code=200, content=event)
    append_cookie_for_get_if_exists(request, response)
    return response


@router.patch("/events/{event_id}")
async def update_event(event_id: str, request: Request) -> Response:
    auth_result = require_authenticated_user_for_post(request)
    if isinstance(auth_result, Response):
        return auth_result

    sid, user_id = auth_result

    payload = await read_json_payload(request)
    if not isinstance(payload, dict):
        payload = {}

    update_set: dict[str, Any] = {}
    update_unset: dict[str, str] = {}

    if "category" in payload:
        category = payload.get("category")
        if not isinstance(category, str) or category not in EVENT_CATEGORIES:
            response = invalid_field_response("category")
            set_session_cookie(response, sid, request.app.state.settings.session_ttl)
            return response

        update_set["category"] = category

    if "price" in payload:
        price = payload.get("price")
        if not is_uint_value(price):
            response = invalid_field_response("price")
            set_session_cookie(response, sid, request.app.state.settings.session_ttl)
            return response

        update_set["price"] = price

    if "city" in payload:
        city = payload.get("city")
        if not isinstance(city, str):
            response = invalid_field_response("city")
            set_session_cookie(response, sid, request.app.state.settings.session_ttl)
            return response

        if city == "":
            update_unset["location.city"] = ""
        else:
            update_set["location.city"] = city

    object_id = parse_object_id(event_id)
    if object_id is None:
        response = JSONResponse(
            status_code=404,
            content={"message": "Not found. Be sure that event exists and you are the organizer"},
        )
        set_session_cookie(response, sid, request.app.state.settings.session_ttl)
        return response

    event_filter = {"_id": object_id}
    event_filter.update(created_by_match_filter(user_id))
    try:
        if update_set or update_unset:
            update_document: dict[str, Any] = {}
            if update_set:
                update_document["$set"] = update_set
            if update_unset:
                update_document["$unset"] = update_unset

            result = request.app.state.mongodb["events"].update_one(event_filter, update_document)
            if result.matched_count == 0:
                response = JSONResponse(
                    status_code=404,
                    content={"message": "Not found. Be sure that event exists and you are the organizer"},
                )
                set_session_cookie(response, sid, request.app.state.settings.session_ttl)
                return response
        else:
            exists = request.app.state.mongodb["events"].find_one(event_filter, {"_id": 1})
            if exists is None:
                response = JSONResponse(
                    status_code=404,
                    content={"message": "Not found. Be sure that event exists and you are the organizer"},
                )
                set_session_cookie(response, sid, request.app.state.settings.session_ttl)
                return response
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="MongoDB is unavailable") from exc

    response = Response(status_code=204)
    set_session_cookie(response, sid, request.app.state.settings.session_ttl)
    return response
