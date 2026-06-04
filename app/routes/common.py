import redis
from fastapi import HTTPException, Request, Response

from app.session import (
    COOKIE_NAME,
    get_session_user_id,
    is_valid_sid,
    refresh_session_if_exists,
    set_session_cookie,
)


def append_cookie_for_get_if_exists(request: Request, response: Response) -> None:
    sid = request.cookies.get(COOKIE_NAME)
    if sid is not None:
        set_session_cookie(response, sid, request.app.state.settings.session_ttl)


def refresh_session_for_post_if_exists(request: Request, response: Response) -> str | None:
    sid = request.cookies.get(COOKIE_NAME)
    if sid is None or not is_valid_sid(sid):
        return None

    try:
        refreshed = refresh_session_if_exists(
            request.app.state.redis,
            sid,
            request.app.state.settings.session_ttl,
        )
    except redis.RedisError as exc:
        raise HTTPException(status_code=503, detail="Redis is unavailable") from exc

    if not refreshed:
        return None

    set_session_cookie(response, sid, request.app.state.settings.session_ttl)
    return sid


def require_authenticated_user(request: Request) -> tuple[str, str] | Response:
    auth_probe_response = Response(status_code=204)
    sid = refresh_session_for_post_if_exists(request, auth_probe_response)

    if sid is None:
        return Response(status_code=401)

    try:
        user_id = get_session_user_id(request.app.state.redis, sid)
    except redis.RedisError as exc:
        raise HTTPException(status_code=503, detail="Redis is unavailable") from exc

    if user_id is None:
        response = Response(status_code=401)
        set_session_cookie(response, sid, request.app.state.settings.session_ttl)
        return response

    return sid, user_id


def require_authenticated_user_for_post(request: Request) -> tuple[str, str] | Response:
    return require_authenticated_user(request)
