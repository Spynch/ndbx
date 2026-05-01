import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone

import redis
import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse

COOKIE_NAME = "X-Session-Id"
SESSION_KEY_PREFIX = "sid:"
SID_BYTES = 16
SID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


def _read_required_env(name: str) -> str:
    value = os.getenv(name)
    if value is None:
        raise ValueError(f"{name} is required")
    return value


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
    app_host: str
    app_port: int
    session_ttl: int
    redis_host: str
    redis_port: int
    redis_password: str
    redis_db: int

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            app_host=_read_required_env("APP_HOST"),
            app_port=_read_int_env("APP_PORT", min_value=1),
            session_ttl=_read_int_env("APP_USER_SESSION_TTL", min_value=1),
            redis_host=_read_required_env("REDIS_HOST"),
            redis_port=_read_int_env("REDIS_PORT", min_value=1),
            redis_password=_read_required_env("REDIS_PASSWORD"),
            redis_db=_read_int_env("REDIS_DB", min_value=0),
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


def _create_session(redis_client: redis.Redis, ttl: int) -> str:
    for _ in range(10):
        sid = _generate_sid()
        key = _session_key(sid)
        now = _utc_now_rfc3339()

        with redis_client.pipeline() as pipe:
            while True:
                try:
                    pipe.watch(key)
                    if pipe.exists(key):
                        pipe.unwatch()
                        break

                    pipe.multi()
                    pipe.hset(key, mapping={"created_at": now, "updated_at": now})
                    pipe.expire(key, ttl)
                    pipe.execute()
                    return sid
                except redis.WatchError:
                    continue

    raise RuntimeError("Could not allocate unique session id")


def _refresh_session_if_exists(redis_client: redis.Redis, sid: str, ttl: int) -> bool:
    key = _session_key(sid)
    now = _utc_now_rfc3339()

    with redis_client.pipeline() as pipe:
        while True:
            try:
                pipe.watch(key)
                if not pipe.exists(key):
                    pipe.unwatch()
                    return False

                pipe.multi()
                pipe.hset(key, "updated_at", now)
                pipe.expire(key, ttl)
                pipe.execute()
                return True
            except redis.WatchError:
                continue


settings = Settings.from_env()
app = FastAPI()
app.state.settings = settings
app.state.redis = _build_redis_client(settings)


@app.on_event("shutdown")
def shutdown() -> None:
    app.state.redis.close()


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


def main() -> None:
    uvicorn.run(app, host=settings.app_host, port=settings.app_port)


if __name__ == "__main__":
    main()
