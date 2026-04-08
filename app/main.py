import os
import re
import secrets
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import quote_plus

import bcrypt
import redis
import uvicorn
from bson import ObjectId
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pymongo import ASCENDING, MongoClient
from pymongo.errors import DuplicateKeyError, PyMongoError

COOKIE_NAME = "X-Session-Id"
SESSION_KEY_PREFIX = "sid:"
SID_BYTES = 16
SID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
INDEX_INIT_ATTEMPTS = 20
INDEX_INIT_DELAY_SECONDS = 0.5
EVENT_CATEGORIES = {"meetup", "concert", "exhibition", "party", "other"}
YYYYMMDD_PATTERN = re.compile(r"^\d{8}$")

CREATE_SESSION_SCRIPT = """
if redis.call('EXISTS', KEYS[1]) == 1 then
    return 0
end
redis.call('HSET', KEYS[1], 'created_at', ARGV[1], 'updated_at', ARGV[1])
if ARGV[3] ~= '' then
    redis.call('HSET', KEYS[1], 'user_id', ARGV[3])
end
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]))
return 1
"""

REFRESH_SESSION_SCRIPT = """
if redis.call('EXISTS', KEYS[1]) == 0 then
    return 0
end
redis.call('HSET', KEYS[1], 'updated_at', ARGV[1])
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]))
return 1
"""

ASSIGN_USER_TO_SESSION_SCRIPT = """
if redis.call('EXISTS', KEYS[1]) == 0 then
    return 0
end
redis.call('HSET', KEYS[1], 'user_id', ARGV[1], 'updated_at', ARGV[2])
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[3]))
return 1
"""


def _read_required_env(name: str) -> str:
    value = os.getenv(name)
    if value is None:
        raise ValueError(f"{name} is required")
    return value


def _read_mongodb_database_env() -> str:
    value = os.getenv("MONGODB_DATABSE")
    if value is not None:
        return value

    fallback = os.getenv("MONGODB_DATABASE")
    if fallback is not None:
        return fallback

    raise ValueError("MONGODB_DATABSE is required")


def _read_int_env(name: str, min_value: int = 0) -> int:
    value = _read_required_env(name)
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got: {value}") from exc

    if parsed < min_value:
        raise ValueError(f"{name} must be >= {min_value}, got: {parsed}")
    return parsed


@dataclass(frozen=True)
class Settings:
    app_port: int
    session_ttl: int
    redis_host: str
    redis_port: int
    redis_password: str
    redis_db: int
    mongodb_database: str
    mongodb_user: str
    mongodb_password: str
    mongodb_host: str
    mongodb_port: int

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            app_port=_read_int_env("APP_PORT", min_value=1),
            session_ttl=_read_int_env("APP_USER_SESSION_TTL", min_value=1),
            redis_host=_read_required_env("REDIS_HOST"),
            redis_port=_read_int_env("REDIS_PORT", min_value=1),
            redis_password=_read_required_env("REDIS_PASSWORD"),
            redis_db=_read_int_env("REDIS_DB", min_value=0),
            mongodb_database=_read_mongodb_database_env(),
            mongodb_user=_read_required_env("MONGODB_USER"),
            mongodb_password=_read_required_env("MONGODB_PASSWORD"),
            mongodb_host=_read_required_env("MONGODB_HOST"),
            mongodb_port=_read_int_env("MONGODB_PORT", min_value=1),
        )


def _build_redis_client(settings: Settings) -> redis.Redis:
    password = settings.redis_password or None
    return redis.Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        password=password,
        db=settings.redis_db,
        decode_responses=True,
    )


def _build_mongodb_client(settings: Settings) -> MongoClient:
    auth = ""
    query = ""

    if settings.mongodb_user:
        user = quote_plus(settings.mongodb_user)
        password = quote_plus(settings.mongodb_password)
        auth = f"{user}:{password}@"
        query = "?authSource=admin"

    uri = f"mongodb://{auth}{settings.mongodb_host}:{settings.mongodb_port}/{query}"
    return MongoClient(uri, serverSelectionTimeoutMS=5000)


def _ensure_indexes(app_instance: FastAPI) -> None:
    database = app_instance.state.mongodb
    users = database["users"]
    events = database["events"]

    users.create_index([("username", ASCENDING)], unique=True)
    users.create_index([("full_name", ASCENDING)])
    events.create_index([("title", ASCENDING), ("created_by", ASCENDING)])
    events.create_index([("created_by", ASCENDING), ("title", ASCENDING)])
    events.create_index([("created_by", ASCENDING)])
    events.create_index([("title", ASCENDING)])
    events.create_index([("category", ASCENDING)])
    events.create_index([("price", ASCENDING)])
    events.create_index([("location.city", ASCENDING)])
    events.create_index([("started_at", ASCENDING)])


def _session_key(sid: str) -> str:
    return f"{SESSION_KEY_PREFIX}{sid}"


def _utc_now_rfc3339() -> str:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    return now.isoformat().replace("+00:00", "Z")


def _is_valid_sid(sid: str) -> bool:
    return bool(SID_PATTERN.fullmatch(sid))


def _generate_sid() -> str:
    return secrets.token_hex(SID_BYTES)


def _set_session_cookie(response: Response, sid: str, ttl: int) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=sid,
        max_age=ttl,
        httponly=True,
        path="/",
        samesite=None,
    )


def _expire_session_cookie(response: Response, sid: str) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=sid,
        max_age=0,
        httponly=True,
        path="/",
        samesite=None,
    )


def _create_session(redis_client: redis.Redis, ttl: int, user_id: str = "") -> str:
    for _ in range(10):
        sid = _generate_sid()
        now = _utc_now_rfc3339()
        created = redis_client.eval(
            CREATE_SESSION_SCRIPT,
            1,
            _session_key(sid),
            now,
            ttl,
            user_id,
        )
        if int(created) == 1:
            return sid

    raise RuntimeError("Could not allocate unique session id")


def _refresh_session_if_exists(redis_client: redis.Redis, sid: str, ttl: int) -> bool:
    now = _utc_now_rfc3339()
    updated = redis_client.eval(
        REFRESH_SESSION_SCRIPT,
        1,
        _session_key(sid),
        now,
        ttl,
    )
    return int(updated) == 1


def _assign_user_to_session(
    redis_client: redis.Redis,
    sid: str,
    user_id: str,
    ttl: int,
) -> bool:
    now = _utc_now_rfc3339()
    updated = redis_client.eval(
        ASSIGN_USER_TO_SESSION_SCRIPT,
        1,
        _session_key(sid),
        user_id,
        now,
        ttl,
    )
    return int(updated) == 1


def _safe_get_json_field(payload: Any, field_name: str) -> str | None:
    if not isinstance(payload, dict):
        return None

    value = payload.get(field_name)
    if not isinstance(value, str):
        return None

    if value.strip() == "":
        return None

    return value


def _invalid_field_response(field_name: str) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={"message": f'invalid "{field_name}" field'},
    )


def _invalid_parameter_response(field_name: str) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={"message": f'invalid "{field_name}" parameter'},
    )


def _append_cookie_for_get_if_exists(request: Request, response: Response) -> None:
    sid = request.cookies.get(COOKIE_NAME)
    if sid is not None:
        _set_session_cookie(response, sid, app.state.settings.session_ttl)


def _refresh_session_for_post_if_exists(request: Request, response: Response) -> str | None:
    sid = request.cookies.get(COOKIE_NAME)
    if sid is None or not _is_valid_sid(sid):
        return None

    try:
        refreshed = _refresh_session_if_exists(
            app.state.redis,
            sid,
            app.state.settings.session_ttl,
        )
    except redis.RedisError as exc:
        raise HTTPException(status_code=503, detail="Redis is unavailable") from exc

    if not refreshed:
        return None

    _set_session_cookie(response, sid, app.state.settings.session_ttl)
    return sid


async def _read_json_payload(request: Request) -> Any:
    try:
        return await request.json()
    except Exception:
        return None


def _parse_rfc3339(value: str) -> datetime | None:
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


def _get_session_user_id(redis_client: redis.Redis, sid: str) -> str | None:
    user_id = redis_client.hget(_session_key(sid), "user_id")
    if user_id is None or user_id.strip() == "":
        return None
    return user_id


def _parse_uint_parameter(request: Request, parameter_name: str) -> int | None:
    raw_value = request.query_params.get(parameter_name)
    if raw_value is None:
        return None

    if raw_value == "" or not raw_value.isdigit():
        raise ValueError(parameter_name)

    return int(raw_value)


def _parse_yyyymmdd_value(raw_value: str, field_name: str) -> date:
    if raw_value == "" or not YYYYMMDD_PATTERN.fullmatch(raw_value):
        raise ValueError(field_name)

    try:
        parsed = datetime.strptime(raw_value, "%Y%m%d")
    except ValueError as exc:
        raise ValueError(field_name) from exc

    return parsed.date()


def _parse_yyyymmdd_parameter(request: Request, parameter_name: str) -> date | None:
    raw_value = request.query_params.get(parameter_name)
    if raw_value is None:
        return None

    return _parse_yyyymmdd_value(raw_value, parameter_name)


def _parse_object_id(raw_value: str) -> ObjectId | None:
    if not ObjectId.is_valid(raw_value):
        return None
    return ObjectId(raw_value)


def _created_by_match_filter(user_id: str) -> dict[str, Any]:
    created_by_values: list[Any] = [user_id]
    created_by_object_id = _parse_object_id(user_id)
    if created_by_object_id is not None:
        created_by_values.append(created_by_object_id)

    if len(created_by_values) == 1:
        return {"created_by": created_by_values[0]}

    return {"created_by": {"$in": created_by_values}}


def _is_uint_value(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _serialize_event(document: dict[str, Any]) -> dict[str, Any]:
    category = document.get("category")
    if not isinstance(category, str):
        category = "other"

    price = document.get("price")
    if not _is_uint_value(price):
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


def _serialize_user(document: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(document["_id"]),
        "full_name": document.get("full_name", ""),
        "username": document.get("username", ""),
    }


def _event_started_date(document: dict[str, Any]) -> date | None:
    started_at = document.get("started_at")
    if not isinstance(started_at, str):
        return None

    parsed = _parse_rfc3339(started_at)
    if parsed is None:
        return None

    return parsed.date()


settings = Settings.from_env()
app = FastAPI()
app.state.settings = settings
app.state.redis = _build_redis_client(settings)
app.state.mongodb_client = _build_mongodb_client(settings)
app.state.mongodb = app.state.mongodb_client[settings.mongodb_database]


@app.on_event("startup")
def startup() -> None:
    last_error: Exception | None = None

    for _ in range(INDEX_INIT_ATTEMPTS):
        try:
            _ensure_indexes(app)
            return
        except PyMongoError as exc:
            last_error = exc
            time.sleep(INDEX_INIT_DELAY_SECONDS)

    raise RuntimeError("MongoDB is unavailable") from last_error


@app.on_event("shutdown")
def shutdown() -> None:
    app.state.redis.close()
    app.state.mongodb_client.close()


@app.get("/health")
def health(request: Request) -> JSONResponse:
    response = JSONResponse({"status": "ok"})

    sid = request.cookies.get(COOKIE_NAME)
    if sid is not None:
        _set_session_cookie(response, sid, app.state.settings.session_ttl)

    return response


@app.post("/session")
def session(request: Request) -> Response:
    redis_client: redis.Redis = app.state.redis
    ttl: int = app.state.settings.session_ttl

    sid = request.cookies.get(COOKIE_NAME)
    if sid is not None and _is_valid_sid(sid):
        try:
            if _refresh_session_if_exists(redis_client, sid, ttl):
                response = Response(status_code=200)
                _set_session_cookie(response, sid, ttl)
                return response
        except redis.RedisError as exc:
            raise HTTPException(status_code=503, detail="Redis is unavailable") from exc

    try:
        new_sid = _create_session(redis_client, ttl)
    except redis.RedisError as exc:
        raise HTTPException(status_code=503, detail="Redis is unavailable") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail="Could not create session") from exc

    response = Response(status_code=201)
    _set_session_cookie(response, new_sid, ttl)
    return response


@app.post("/users")
async def create_user(request: Request) -> Response:
    payload = await _read_json_payload(request)

    full_name = _safe_get_json_field(payload, "full_name")
    if full_name is None:
        response = _invalid_field_response("full_name")
        _refresh_session_for_post_if_exists(request, response)
        return response

    username = _safe_get_json_field(payload, "username")
    if username is None:
        response = _invalid_field_response("username")
        _refresh_session_for_post_if_exists(request, response)
        return response

    password = _safe_get_json_field(payload, "password")
    if password is None:
        response = _invalid_field_response("password")
        _refresh_session_for_post_if_exists(request, response)
        return response

    password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    try:
        inserted = app.state.mongodb["users"].insert_one(
            {
                "full_name": full_name,
                "username": username,
                "password_hash": password_hash,
            }
        )
    except DuplicateKeyError:
        response = JSONResponse(status_code=409, content={"message": "user already exists"})
        _refresh_session_for_post_if_exists(request, response)
        return response
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="MongoDB is unavailable") from exc

    user_id = str(inserted.inserted_id)
    try:
        sid = _create_session(app.state.redis, app.state.settings.session_ttl, user_id=user_id)
    except redis.RedisError as exc:
        raise HTTPException(status_code=503, detail="Redis is unavailable") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail="Could not create session") from exc

    response = Response(status_code=201)
    _set_session_cookie(response, sid, app.state.settings.session_ttl)
    return response


@app.post("/auth/login")
async def login(request: Request) -> Response:
    payload = await _read_json_payload(request)

    username = _safe_get_json_field(payload, "username")
    if username is None:
        response = _invalid_field_response("username")
        _refresh_session_for_post_if_exists(request, response)
        return response

    password = _safe_get_json_field(payload, "password")
    if password is None:
        response = _invalid_field_response("password")
        _refresh_session_for_post_if_exists(request, response)
        return response

    try:
        user = app.state.mongodb["users"].find_one({"username": username})
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="MongoDB is unavailable") from exc

    if user is None:
        response = JSONResponse(status_code=401, content={"message": "invalid credentials"})
        _refresh_session_for_post_if_exists(request, response)
        return response

    password_hash = user.get("password_hash")
    if not isinstance(password_hash, str):
        response = JSONResponse(status_code=401, content={"message": "invalid credentials"})
        _refresh_session_for_post_if_exists(request, response)
        return response

    password_matches = bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    if not password_matches:
        response = JSONResponse(status_code=401, content={"message": "invalid credentials"})
        _refresh_session_for_post_if_exists(request, response)
        return response

    user_id = str(user.get("_id"))
    ttl = app.state.settings.session_ttl
    sid = request.cookies.get(COOKIE_NAME)

    try:
        if sid is not None and _is_valid_sid(sid):
            if _assign_user_to_session(app.state.redis, sid, user_id, ttl):
                response = Response(status_code=204)
                _set_session_cookie(response, sid, ttl)
                return response

        new_sid = _create_session(app.state.redis, ttl, user_id=user_id)
    except redis.RedisError as exc:
        raise HTTPException(status_code=503, detail="Redis is unavailable") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail="Could not create session") from exc

    response = Response(status_code=204)
    _set_session_cookie(response, new_sid, ttl)
    return response


@app.post("/auth/logout")
def logout(request: Request) -> Response:
    auth_probe_response = Response(status_code=204)
    sid = _refresh_session_for_post_if_exists(request, auth_probe_response)

    if sid is None:
        return Response(status_code=401)

    try:
        user_id = _get_session_user_id(app.state.redis, sid)
    except redis.RedisError as exc:
        raise HTTPException(status_code=503, detail="Redis is unavailable") from exc

    if user_id is None:
        response = Response(status_code=401)
        _set_session_cookie(response, sid, app.state.settings.session_ttl)
        return response

    try:
        app.state.redis.delete(_session_key(sid))
    except redis.RedisError as exc:
        raise HTTPException(status_code=503, detail="Redis is unavailable") from exc

    response = Response(status_code=204)
    _expire_session_cookie(response, sid)
    return response


@app.post("/events")
async def create_event(request: Request) -> Response:
    auth_probe_response = Response(status_code=204)
    sid = _refresh_session_for_post_if_exists(request, auth_probe_response)

    if sid is None:
        return Response(status_code=401)

    try:
        user_id = _get_session_user_id(app.state.redis, sid)
    except redis.RedisError as exc:
        raise HTTPException(status_code=503, detail="Redis is unavailable") from exc

    if user_id is None:
        response = Response(status_code=401)
        _set_session_cookie(response, sid, app.state.settings.session_ttl)
        return response

    payload = await _read_json_payload(request)

    title = _safe_get_json_field(payload, "title")
    if title is None:
        response = _invalid_field_response("title")
        _set_session_cookie(response, sid, app.state.settings.session_ttl)
        return response

    address = _safe_get_json_field(payload, "address")
    if address is None:
        response = _invalid_field_response("address")
        _set_session_cookie(response, sid, app.state.settings.session_ttl)
        return response

    started_at = _safe_get_json_field(payload, "started_at")
    if started_at is None:
        response = _invalid_field_response("started_at")
        _set_session_cookie(response, sid, app.state.settings.session_ttl)
        return response

    if _parse_rfc3339(started_at) is None:
        response = _invalid_field_response("started_at")
        _set_session_cookie(response, sid, app.state.settings.session_ttl)
        return response

    finished_at = _safe_get_json_field(payload, "finished_at")
    if finished_at is None:
        response = _invalid_field_response("finished_at")
        _set_session_cookie(response, sid, app.state.settings.session_ttl)
        return response

    if _parse_rfc3339(finished_at) is None:
        response = _invalid_field_response("finished_at")
        _set_session_cookie(response, sid, app.state.settings.session_ttl)
        return response

    description = payload.get("description", "")
    if not isinstance(description, str):
        response = _invalid_field_response("description")
        _set_session_cookie(response, sid, app.state.settings.session_ttl)
        return response

    event_document = {
        "title": title,
        "category": "other",
        "price": 0,
        "description": description,
        "location": {"address": address},
        "created_at": _utc_now_rfc3339(),
        "created_by": user_id,
        "started_at": started_at,
        "finished_at": finished_at,
    }

    try:
        inserted = app.state.mongodb["events"].insert_one(event_document)
    except DuplicateKeyError:
        response = JSONResponse(status_code=409, content={"message": "event already exists"})
        _set_session_cookie(response, sid, app.state.settings.session_ttl)
        return response
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="MongoDB is unavailable") from exc

    response = JSONResponse(status_code=201, content={"id": str(inserted.inserted_id)})
    _set_session_cookie(response, sid, app.state.settings.session_ttl)
    return response


@app.get("/events")
def list_events(request: Request) -> Response:
    title_filter = request.query_params.get("title")
    event_id_filter = request.query_params.get("id")
    category_filter = request.query_params.get("category")
    city_filter = request.query_params.get("city")
    user_filter = request.query_params.get("user")

    try:
        limit = _parse_uint_parameter(request, "limit")
    except ValueError:
        response = _invalid_field_response("limit")
        _append_cookie_for_get_if_exists(request, response)
        return response

    try:
        offset = _parse_uint_parameter(request, "offset")
    except ValueError:
        response = _invalid_field_response("offset")
        _append_cookie_for_get_if_exists(request, response)
        return response

    try:
        price_from = _parse_uint_parameter(request, "price_from")
    except ValueError:
        response = _invalid_field_response("price_from")
        _append_cookie_for_get_if_exists(request, response)
        return response

    try:
        price_to = _parse_uint_parameter(request, "price_to")
    except ValueError:
        response = _invalid_field_response("price_to")
        _append_cookie_for_get_if_exists(request, response)
        return response

    try:
        date_from = _parse_yyyymmdd_parameter(request, "date_from")
    except ValueError:
        response = _invalid_field_response("date_from")
        _append_cookie_for_get_if_exists(request, response)
        return response

    try:
        date_to = _parse_yyyymmdd_parameter(request, "date_to")
    except ValueError:
        response = _invalid_field_response("date_to")
        _append_cookie_for_get_if_exists(request, response)
        return response

    legacy_date_from_raw = request.query_params.get("started_date_from")
    if date_from is None and legacy_date_from_raw is not None:
        try:
            date_from = _parse_yyyymmdd_value(legacy_date_from_raw, "started_date_from")
        except ValueError:
            response = _invalid_field_response("started_date_from")
            _append_cookie_for_get_if_exists(request, response)
            return response

    legacy_date_to_raw = request.query_params.get("started_date_to")
    if date_to is None and legacy_date_to_raw is not None:
        try:
            date_to = _parse_yyyymmdd_value(legacy_date_to_raw, "started_date_to")
        except ValueError:
            response = _invalid_field_response("started_date_to")
            _append_cookie_for_get_if_exists(request, response)
            return response

    if price_from is not None and price_to is not None and price_from > price_to:
        response = _invalid_field_response("price_to")
        _append_cookie_for_get_if_exists(request, response)
        return response

    if date_from is not None and date_to is not None and date_from > date_to:
        response = _invalid_field_response("date_to")
        _append_cookie_for_get_if_exists(request, response)
        return response

    if category_filter is not None and category_filter not in EVENT_CATEGORIES:
        response = _invalid_field_response("category")
        _append_cookie_for_get_if_exists(request, response)
        return response

    if city_filter is not None and city_filter.strip() == "":
        response = _invalid_field_response("city")
        _append_cookie_for_get_if_exists(request, response)
        return response

    if user_filter is not None and user_filter.strip() == "":
        response = _invalid_field_response("user")
        _append_cookie_for_get_if_exists(request, response)
        return response

    filters: dict[str, Any] = {}
    if title_filter is not None:
        filters["title"] = {"$regex": re.escape(title_filter)}

    if event_id_filter is not None:
        if event_id_filter.strip() == "":
            response = _invalid_field_response("id")
            _append_cookie_for_get_if_exists(request, response)
            return response

        event_object_id = _parse_object_id(event_id_filter)
        if event_object_id is None:
            response = JSONResponse(status_code=200, content={"events": [], "count": 0})
            _append_cookie_for_get_if_exists(request, response)
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
            user_document = app.state.mongodb["users"].find_one({"username": user_filter}, {"_id": 1})
        except PyMongoError as exc:
            raise HTTPException(status_code=503, detail="MongoDB is unavailable") from exc

        if user_document is None:
            response = JSONResponse(status_code=200, content={"events": [], "count": 0})
            _append_cookie_for_get_if_exists(request, response)
            return response

        filters.update(_created_by_match_filter(str(user_document["_id"])))

    try:
        documents = list(app.state.mongodb["events"].find(filters))
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="MongoDB is unavailable") from exc

    if date_from is not None or date_to is not None:
        filtered_documents = []
        for document in documents:
            started_date = _event_started_date(document)
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

    events = [_serialize_event(document) for document in documents]

    response = JSONResponse(status_code=200, content={"events": events, "count": len(events)})
    _append_cookie_for_get_if_exists(request, response)
    return response


@app.get("/events/{event_id}")
def get_event(event_id: str, request: Request) -> Response:
    object_id = _parse_object_id(event_id)
    if object_id is None:
        response = JSONResponse(status_code=404, content={"message": "Not found"})
        _append_cookie_for_get_if_exists(request, response)
        return response

    try:
        document = app.state.mongodb["events"].find_one({"_id": object_id})
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="MongoDB is unavailable") from exc

    if document is None:
        response = JSONResponse(status_code=404, content={"message": "Not found"})
        _append_cookie_for_get_if_exists(request, response)
        return response

    response = JSONResponse(status_code=200, content=_serialize_event(document))
    _append_cookie_for_get_if_exists(request, response)
    return response


@app.patch("/events/{event_id}")
async def update_event(event_id: str, request: Request) -> Response:
    auth_probe_response = Response(status_code=204)
    sid = _refresh_session_for_post_if_exists(request, auth_probe_response)

    if sid is None:
        return Response(status_code=401)

    try:
        user_id = _get_session_user_id(app.state.redis, sid)
    except redis.RedisError as exc:
        raise HTTPException(status_code=503, detail="Redis is unavailable") from exc

    if user_id is None:
        response = Response(status_code=401)
        _set_session_cookie(response, sid, app.state.settings.session_ttl)
        return response

    payload = await _read_json_payload(request)
    if not isinstance(payload, dict):
        payload = {}

    update_set: dict[str, Any] = {}
    update_unset: dict[str, str] = {}

    if "category" in payload:
        category = payload.get("category")
        if not isinstance(category, str) or category not in EVENT_CATEGORIES:
            response = _invalid_field_response("category")
            _set_session_cookie(response, sid, app.state.settings.session_ttl)
            return response

        update_set["category"] = category

    if "price" in payload:
        price = payload.get("price")
        if not _is_uint_value(price):
            response = _invalid_field_response("price")
            _set_session_cookie(response, sid, app.state.settings.session_ttl)
            return response

        update_set["price"] = price

    if "city" in payload:
        city = payload.get("city")
        if not isinstance(city, str):
            response = _invalid_field_response("city")
            _set_session_cookie(response, sid, app.state.settings.session_ttl)
            return response

        if city == "":
            update_unset["location.city"] = ""
        else:
            update_set["location.city"] = city

    object_id = _parse_object_id(event_id)
    if object_id is None:
        response = JSONResponse(
            status_code=404,
            content={"message": "Not found. Be sure that event exists and you are the organizer"},
        )
        _set_session_cookie(response, sid, app.state.settings.session_ttl)
        return response

    event_filter = {"_id": object_id}
    event_filter.update(_created_by_match_filter(user_id))
    try:
        if update_set or update_unset:
            update_document: dict[str, Any] = {}
            if update_set:
                update_document["$set"] = update_set
            if update_unset:
                update_document["$unset"] = update_unset

            result = app.state.mongodb["events"].update_one(event_filter, update_document)
            if result.matched_count == 0:
                response = JSONResponse(
                    status_code=404,
                    content={"message": "Not found. Be sure that event exists and you are the organizer"},
                )
                _set_session_cookie(response, sid, app.state.settings.session_ttl)
                return response
        else:
            exists = app.state.mongodb["events"].find_one(event_filter, {"_id": 1})
            if exists is None:
                response = JSONResponse(
                    status_code=404,
                    content={"message": "Not found. Be sure that event exists and you are the organizer"},
                )
                _set_session_cookie(response, sid, app.state.settings.session_ttl)
                return response
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="MongoDB is unavailable") from exc

    response = Response(status_code=204)
    _set_session_cookie(response, sid, app.state.settings.session_ttl)
    return response


@app.get("/users")
def list_users(request: Request) -> Response:
    try:
        limit = _parse_uint_parameter(request, "limit")
    except ValueError:
        response = _invalid_field_response("limit")
        _append_cookie_for_get_if_exists(request, response)
        return response

    try:
        offset = _parse_uint_parameter(request, "offset")
    except ValueError:
        response = _invalid_field_response("offset")
        _append_cookie_for_get_if_exists(request, response)
        return response

    name_filter = request.query_params.get("name")
    id_filter = request.query_params.get("id")

    if name_filter is not None and name_filter.strip() == "":
        response = _invalid_field_response("name")
        _append_cookie_for_get_if_exists(request, response)
        return response

    if id_filter is not None and id_filter.strip() == "":
        response = _invalid_field_response("id")
        _append_cookie_for_get_if_exists(request, response)
        return response

    filters: dict[str, Any] = {}
    if name_filter is not None:
        filters["full_name"] = {"$regex": re.escape(name_filter)}

    if id_filter is not None:
        object_id = _parse_object_id(id_filter)
        if object_id is None:
            response = JSONResponse(status_code=200, content={"users": [], "count": 0})
            _append_cookie_for_get_if_exists(request, response)
            return response

        filters["_id"] = object_id

    try:
        documents = list(app.state.mongodb["users"].find(filters, {"password_hash": 0}))
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="MongoDB is unavailable") from exc

    if offset is not None:
        documents = documents[offset:]

    if limit is not None:
        documents = documents[:limit]

    users = [_serialize_user(document) for document in documents]
    response = JSONResponse(status_code=200, content={"users": users, "count": len(users)})
    _append_cookie_for_get_if_exists(request, response)
    return response


@app.get("/users/{user_id}")
def get_user(user_id: str, request: Request) -> Response:
    object_id = _parse_object_id(user_id)
    if object_id is None:
        response = JSONResponse(status_code=404, content={"message": "Not found"})
        _append_cookie_for_get_if_exists(request, response)
        return response

    try:
        document = app.state.mongodb["users"].find_one({"_id": object_id}, {"password_hash": 0})
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="MongoDB is unavailable") from exc

    if document is None:
        response = JSONResponse(status_code=404, content={"message": "Not found"})
        _append_cookie_for_get_if_exists(request, response)
        return response

    response = JSONResponse(status_code=200, content=_serialize_user(document))
    _append_cookie_for_get_if_exists(request, response)
    return response


@app.get("/users/{user_id}/events")
def list_user_events(user_id: str, request: Request) -> Response:
    object_id = _parse_object_id(user_id)
    if object_id is None:
        response = JSONResponse(status_code=404, content={"message": "User not found"})
        _append_cookie_for_get_if_exists(request, response)
        return response

    try:
        user = app.state.mongodb["users"].find_one({"_id": object_id}, {"_id": 1})
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="MongoDB is unavailable") from exc

    if user is None:
        response = JSONResponse(status_code=404, content={"message": "User not found"})
        _append_cookie_for_get_if_exists(request, response)
        return response

    title_filter = request.query_params.get("title")
    event_id_filter = request.query_params.get("id")
    category_filter = request.query_params.get("category")
    city_filter = request.query_params.get("city")

    try:
        limit = _parse_uint_parameter(request, "limit")
    except ValueError:
        response = _invalid_field_response("limit")
        _append_cookie_for_get_if_exists(request, response)
        return response

    try:
        offset = _parse_uint_parameter(request, "offset")
    except ValueError:
        response = _invalid_field_response("offset")
        _append_cookie_for_get_if_exists(request, response)
        return response

    try:
        price_from = _parse_uint_parameter(request, "price_from")
    except ValueError:
        response = _invalid_field_response("price_from")
        _append_cookie_for_get_if_exists(request, response)
        return response

    try:
        price_to = _parse_uint_parameter(request, "price_to")
    except ValueError:
        response = _invalid_field_response("price_to")
        _append_cookie_for_get_if_exists(request, response)
        return response

    try:
        date_from = _parse_yyyymmdd_parameter(request, "date_from")
    except ValueError:
        response = _invalid_field_response("date_from")
        _append_cookie_for_get_if_exists(request, response)
        return response

    try:
        date_to = _parse_yyyymmdd_parameter(request, "date_to")
    except ValueError:
        response = _invalid_field_response("date_to")
        _append_cookie_for_get_if_exists(request, response)
        return response

    legacy_date_from_raw = request.query_params.get("started_date_from")
    if date_from is None and legacy_date_from_raw is not None:
        try:
            date_from = _parse_yyyymmdd_value(legacy_date_from_raw, "started_date_from")
        except ValueError:
            response = _invalid_field_response("started_date_from")
            _append_cookie_for_get_if_exists(request, response)
            return response

    legacy_date_to_raw = request.query_params.get("started_date_to")
    if date_to is None and legacy_date_to_raw is not None:
        try:
            date_to = _parse_yyyymmdd_value(legacy_date_to_raw, "started_date_to")
        except ValueError:
            response = _invalid_field_response("started_date_to")
            _append_cookie_for_get_if_exists(request, response)
            return response

    if price_from is not None and price_to is not None and price_from > price_to:
        response = _invalid_field_response("price_to")
        _append_cookie_for_get_if_exists(request, response)
        return response

    if date_from is not None and date_to is not None and date_from > date_to:
        response = _invalid_field_response("date_to")
        _append_cookie_for_get_if_exists(request, response)
        return response

    if category_filter is not None and category_filter not in EVENT_CATEGORIES:
        response = _invalid_field_response("category")
        _append_cookie_for_get_if_exists(request, response)
        return response

    if city_filter is not None and city_filter.strip() == "":
        response = _invalid_field_response("city")
        _append_cookie_for_get_if_exists(request, response)
        return response

    filters: dict[str, Any] = _created_by_match_filter(user_id)
    if title_filter is not None:
        filters["title"] = {"$regex": re.escape(title_filter)}

    if event_id_filter is not None:
        if event_id_filter.strip() == "":
            response = _invalid_field_response("id")
            _append_cookie_for_get_if_exists(request, response)
            return response

        event_object_id = _parse_object_id(event_id_filter)
        if event_object_id is None:
            response = JSONResponse(status_code=200, content={"events": [], "count": 0})
            _append_cookie_for_get_if_exists(request, response)
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
        documents = list(app.state.mongodb["events"].find(filters))
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="MongoDB is unavailable") from exc

    if date_from is not None or date_to is not None:
        filtered_documents = []
        for document in documents:
            started_date = _event_started_date(document)
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

    events = [_serialize_event(document) for document in documents]
    response = JSONResponse(status_code=200, content={"events": events, "count": len(events)})
    _append_cookie_for_get_if_exists(request, response)
    return response


def main() -> None:
    uvicorn.run(app, host="0.0.0.0", port=settings.app_port)


if __name__ == "__main__":
    main()
