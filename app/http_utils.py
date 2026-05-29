import re
from datetime import date, datetime
from typing import Any

from bson import ObjectId
from fastapi import Request
from fastapi.responses import JSONResponse

EVENT_CATEGORIES = {"meetup", "concert", "exhibition", "party", "other"}
YYYYMMDD_PATTERN = re.compile(r"^\d{8}$")


def safe_get_json_field(payload: Any, field_name: str) -> str | None:
    if not isinstance(payload, dict):
        return None

    value = payload.get(field_name)
    if not isinstance(value, str):
        return None

    if value.strip() == "":
        return None

    return value


def invalid_field_response(field_name: str) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={"message": f'invalid "{field_name}" field'},
    )


async def read_json_payload(request: Request) -> Any:
    try:
        return await request.json()
    except Exception:
        return None


def parse_rfc3339(value: str) -> datetime | None:
    if "T" not in value:
        return None

    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return None

    return parsed


def parse_uint_parameter(request: Request, parameter_name: str) -> int | None:
    raw_value = request.query_params.get(parameter_name)
    if raw_value is None:
        return None

    if raw_value == "" or not raw_value.isdigit():
        raise ValueError(parameter_name)

    return int(raw_value)


def parse_yyyymmdd_value(raw_value: str, field_name: str) -> date:
    if raw_value == "" or not YYYYMMDD_PATTERN.fullmatch(raw_value):
        raise ValueError(field_name)

    try:
        parsed = datetime.strptime(raw_value, "%Y%m%d")
    except ValueError as exc:
        raise ValueError(field_name) from exc

    return parsed.date()


def parse_yyyymmdd_parameter(request: Request, parameter_name: str) -> date | None:
    raw_value = request.query_params.get(parameter_name)
    if raw_value is None:
        return None

    return parse_yyyymmdd_value(raw_value, parameter_name)


def parse_object_id(raw_value: str) -> ObjectId | None:
    if not ObjectId.is_valid(raw_value):
        return None
    return ObjectId(raw_value)


def created_by_match_filter(user_id: str) -> dict[str, Any]:
    created_by_values: list[Any] = [user_id]
    created_by_object_id = parse_object_id(user_id)
    if created_by_object_id is not None:
        created_by_values.append(created_by_object_id)

    if len(created_by_values) == 1:
        return {"created_by": created_by_values[0]}

    return {"created_by": {"$in": created_by_values}}


def is_uint_value(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def serialize_event(document: dict[str, Any]) -> dict[str, Any]:
    category = document.get("category")
    if not isinstance(category, str):
        category = "other"

    price = document.get("price")
    if not is_uint_value(price):
        price = 0

    location_document = document.get("location")
    address = ""
    city: str | None = None
    if isinstance(location_document, dict):
        address_value = location_document.get("address")
        if isinstance(address_value, str):
            address = address_value

        city_value = location_document.get("city")
        if isinstance(city_value, str) and city_value != "":
            city = city_value

    location: dict[str, Any] = {"address": address}
    if city is not None:
        location["city"] = city

    created_by = document.get("created_by")
    if isinstance(created_by, ObjectId):
        created_by = str(created_by)
    elif not isinstance(created_by, str):
        created_by = ""

    return {
        "id": str(document["_id"]),
        "title": document.get("title", ""),
        "category": category,
        "price": price,
        "description": document.get("description", ""),
        "location": location,
        "created_at": document.get("created_at", ""),
        "created_by": created_by,
        "started_at": document.get("started_at", ""),
        "finished_at": document.get("finished_at", ""),
    }


def serialize_user(document: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(document["_id"]),
        "full_name": document.get("full_name", ""),
        "username": document.get("username", ""),
    }


def event_started_date(document: dict[str, Any]) -> date | None:
    started_at = document.get("started_at")
    if not isinstance(started_at, str):
        return None

    parsed = parse_rfc3339(started_at)
    if parsed is None:
        return None

    return parsed.date()
