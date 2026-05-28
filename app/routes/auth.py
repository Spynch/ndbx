import bcrypt
import redis
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from neo4j.exceptions import Neo4jError, ServiceUnavailable, SessionExpired
from pymongo.errors import DuplicateKeyError, PyMongoError

from app.http_utils import invalid_field_response, read_json_payload, safe_get_json_field
from app.neo4j_graph import create_user_node
from app.routes.common import refresh_session_for_post_if_exists, require_authenticated_user_for_post
from app.session import (
    COOKIE_NAME,
    assign_user_to_session,
    create_session,
    expire_session_cookie,
    is_valid_sid,
    session_key,
    set_session_cookie,
)

router = APIRouter()


@router.post("/users")
async def create_user(request: Request) -> Response:
    payload = await read_json_payload(request)

    full_name = safe_get_json_field(payload, "full_name")
    if full_name is None:
        response = invalid_field_response("full_name")
        refresh_session_for_post_if_exists(request, response)
        return response

    username = safe_get_json_field(payload, "username")
    if username is None:
        response = invalid_field_response("username")
        refresh_session_for_post_if_exists(request, response)
        return response

    password = safe_get_json_field(payload, "password")
    if password is None:
        response = invalid_field_response("password")
        refresh_session_for_post_if_exists(request, response)
        return response

    password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    try:
        inserted = request.app.state.mongodb["users"].insert_one(
            {
                "full_name": full_name,
                "username": username,
                "password_hash": password_hash,
            }
        )
    except DuplicateKeyError:
        response = JSONResponse(status_code=409, content={"message": "user already exists"})
        refresh_session_for_post_if_exists(request, response)
        return response
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="MongoDB is unavailable") from exc

    user_id = str(inserted.inserted_id)
    try:
        create_user_node(request.app.state.neo4j_driver, user_id)
    except (Neo4jError, ServiceUnavailable, SessionExpired) as exc:
        try:
            request.app.state.mongodb["users"].delete_one({"_id": inserted.inserted_id})
        except PyMongoError:
            pass
        raise HTTPException(status_code=503, detail="Neo4j is unavailable") from exc

    try:
        sid = create_session(request.app.state.redis, request.app.state.settings.session_ttl, user_id=user_id)
    except redis.RedisError as exc:
        raise HTTPException(status_code=503, detail="Redis is unavailable") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail="Could not create session") from exc

    response = Response(status_code=201)
    set_session_cookie(response, sid, request.app.state.settings.session_ttl)
    return response


@router.post("/auth/login")
async def login(request: Request) -> Response:
    payload = await read_json_payload(request)

    username = safe_get_json_field(payload, "username")
    if username is None:
        response = invalid_field_response("username")
        refresh_session_for_post_if_exists(request, response)
        return response

    password = safe_get_json_field(payload, "password")
    if password is None:
        response = invalid_field_response("password")
        refresh_session_for_post_if_exists(request, response)
        return response

    try:
        user = request.app.state.mongodb["users"].find_one({"username": username})
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="MongoDB is unavailable") from exc

    if user is None:
        response = JSONResponse(status_code=401, content={"message": "invalid credentials"})
        refresh_session_for_post_if_exists(request, response)
        return response

    password_hash = user.get("password_hash")
    if not isinstance(password_hash, str):
        response = JSONResponse(status_code=401, content={"message": "invalid credentials"})
        refresh_session_for_post_if_exists(request, response)
        return response

    password_matches = bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    if not password_matches:
        response = JSONResponse(status_code=401, content={"message": "invalid credentials"})
        refresh_session_for_post_if_exists(request, response)
        return response

    user_id = str(user.get("_id"))
    ttl = request.app.state.settings.session_ttl
    sid = request.cookies.get(COOKIE_NAME)

    try:
        if sid is not None and is_valid_sid(sid):
            if assign_user_to_session(request.app.state.redis, sid, user_id, ttl):
                response = Response(status_code=204)
                set_session_cookie(response, sid, ttl)
                return response

        new_sid = create_session(request.app.state.redis, ttl, user_id=user_id)
    except redis.RedisError as exc:
        raise HTTPException(status_code=503, detail="Redis is unavailable") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail="Could not create session") from exc

    response = Response(status_code=204)
    set_session_cookie(response, new_sid, ttl)
    return response


@router.post("/auth/logout")
def logout(request: Request) -> Response:
    auth_result = require_authenticated_user_for_post(request)
    if isinstance(auth_result, Response):
        return auth_result

    sid, _ = auth_result

    try:
        request.app.state.redis.delete(session_key(sid))
    except redis.RedisError as exc:
        raise HTTPException(status_code=503, detail="Redis is unavailable") from exc

    response = Response(status_code=204)
    expire_session_cookie(response, sid)
    return response
