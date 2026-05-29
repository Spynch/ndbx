import redis
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from app.session import COOKIE_NAME, create_session, is_valid_sid, refresh_session_if_exists, set_session_cookie

router = APIRouter()


@router.get("/health")
def health(request: Request) -> JSONResponse:
    response = JSONResponse({"status": "ok"})

    sid = request.cookies.get(COOKIE_NAME)
    if sid is not None:
        set_session_cookie(response, sid, request.app.state.settings.session_ttl)

    return response


@router.post("/session")
def session(request: Request) -> Response:
    redis_client: redis.Redis = request.app.state.redis
    ttl: int = request.app.state.settings.session_ttl

    sid = request.cookies.get(COOKIE_NAME)
    if sid is not None and is_valid_sid(sid):
        try:
            if refresh_session_if_exists(redis_client, sid, ttl):
                response = Response(status_code=200)
                set_session_cookie(response, sid, ttl)
                return response
        except redis.RedisError as exc:
            raise HTTPException(status_code=503, detail="Redis is unavailable") from exc

    try:
        new_sid = create_session(redis_client, ttl)
    except redis.RedisError as exc:
        raise HTTPException(status_code=503, detail="Redis is unavailable") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail="Could not create session") from exc

    response = Response(status_code=201)
    set_session_cookie(response, new_sid, ttl)
    return response
