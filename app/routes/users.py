import re
from typing import Any

from cassandra import DriverException
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pymongo.errors import PyMongoError

from app.http_utils import (
    EVENT_CATEGORIES,
    created_by_match_filter,
    event_started_date,
    invalid_field_response,
    parse_object_id,
    parse_uint_parameter,
    parse_yyyymmdd_parameter,
    parse_yyyymmdd_value,
    serialize_event,
    serialize_user,
)
from app.reactions import empty_reactions, reactions_for_event_titles, should_include_reactions
from app.reviews import empty_reviews, reviews_for_event_titles, should_include_reviews
from app.routes.common import append_cookie_for_get_if_exists

router = APIRouter()


@router.get("/users")
def list_users(request: Request) -> Response:
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

    name_filter = request.query_params.get("name")
    id_filter = request.query_params.get("id")

    if name_filter is not None and name_filter.strip() == "":
        response = invalid_field_response("name")
        append_cookie_for_get_if_exists(request, response)
        return response

    if id_filter is not None and id_filter.strip() == "":
        response = invalid_field_response("id")
        append_cookie_for_get_if_exists(request, response)
        return response

    filters: dict[str, Any] = {}
    if name_filter is not None:
        filters["full_name"] = {"$regex": re.escape(name_filter)}

    if id_filter is not None:
        object_id = parse_object_id(id_filter)
        if object_id is None:
            response = JSONResponse(status_code=200, content={"users": [], "count": 0})
            append_cookie_for_get_if_exists(request, response)
            return response

        filters["_id"] = object_id

    try:
        documents = list(request.app.state.mongodb["users"].find(filters, {"password_hash": 0}))
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="MongoDB is unavailable") from exc

    if offset is not None:
        documents = documents[offset:]

    if limit is not None:
        documents = documents[:limit]

    users = [serialize_user(document) for document in documents]
    response = JSONResponse(status_code=200, content={"users": users, "count": len(users)})
    append_cookie_for_get_if_exists(request, response)
    return response


@router.get("/users/{user_id}")
def get_user(user_id: str, request: Request) -> Response:
    object_id = parse_object_id(user_id)
    if object_id is None:
        response = JSONResponse(status_code=404, content={"message": "Not found"})
        append_cookie_for_get_if_exists(request, response)
        return response

    try:
        document = request.app.state.mongodb["users"].find_one({"_id": object_id}, {"password_hash": 0})
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="MongoDB is unavailable") from exc

    if document is None:
        response = JSONResponse(status_code=404, content={"message": "Not found"})
        append_cookie_for_get_if_exists(request, response)
        return response

    response = JSONResponse(status_code=200, content=serialize_user(document))
    append_cookie_for_get_if_exists(request, response)
    return response


@router.get("/users/{user_id}/events")
def list_user_events(user_id: str, request: Request) -> Response:
    object_id = parse_object_id(user_id)
    if object_id is None:
        response = JSONResponse(status_code=404, content={"message": "User not found"})
        append_cookie_for_get_if_exists(request, response)
        return response

    try:
        user = request.app.state.mongodb["users"].find_one({"_id": object_id}, {"_id": 1})
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="MongoDB is unavailable") from exc

    if user is None:
        response = JSONResponse(status_code=404, content={"message": "User not found"})
        append_cookie_for_get_if_exists(request, response)
        return response

    title_filter = request.query_params.get("title")
    event_id_filter = request.query_params.get("id")
    category_filter = request.query_params.get("category")
    city_filter = request.query_params.get("city")

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

    filters: dict[str, Any] = created_by_match_filter(user_id)
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

    if should_include_reviews(request):
        try:
            reviews_by_title = reviews_for_event_titles(request, [event["title"] for event in events])
        except PyMongoError as exc:
            raise HTTPException(status_code=503, detail="MongoDB is unavailable") from exc
        except DriverException as exc:
            raise HTTPException(status_code=503, detail="Cassandra is unavailable") from exc

        for event in events:
            event["reviews"] = dict(reviews_by_title.get(event["title"], empty_reviews()))

    response = JSONResponse(status_code=200, content={"events": events, "count": len(events)})
    append_cookie_for_get_if_exists(request, response)
    return response
