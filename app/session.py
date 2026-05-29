import re
import secrets
from datetime import datetime, timezone

import redis
from fastapi import Response

COOKIE_NAME = "X-Session-Id"
SESSION_KEY_PREFIX = "sid:"
SID_BYTES = 16
SID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


def session_key(sid: str) -> str:
    return f"{SESSION_KEY_PREFIX}{sid}"


def utc_now_rfc3339() -> str:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    return now.isoformat().replace("+00:00", "Z")


def is_valid_sid(sid: str) -> bool:
    return bool(SID_PATTERN.fullmatch(sid))


def generate_sid() -> str:
    return secrets.token_hex(SID_BYTES)


def set_session_cookie(response: Response, sid: str, ttl: int) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=sid,
        max_age=ttl,
        httponly=True,
        path="/",
        samesite=None,
    )


def expire_session_cookie(response: Response, sid: str) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=sid,
        max_age=0,
        httponly=True,
        path="/",
        samesite=None,
    )


def create_session(redis_client: redis.Redis, ttl: int, user_id: str = "") -> str:
    for _ in range(10):
        sid = generate_sid()
        key = session_key(sid)
        now = utc_now_rfc3339()

        with redis_client.pipeline() as pipe:
            while True:
                try:
                    pipe.watch(key)
                    if pipe.exists(key):
                        pipe.unwatch()
                        break

                    session_payload = {"created_at": now, "updated_at": now}
                    if user_id != "":
                        session_payload["user_id"] = user_id

                    pipe.multi()
                    pipe.hset(key, mapping=session_payload)
                    pipe.expire(key, ttl)
                    pipe.execute()
                    return sid
                except redis.WatchError:
                    continue

    raise RuntimeError("Could not allocate unique session id")


def refresh_session_if_exists(redis_client: redis.Redis, sid: str, ttl: int) -> bool:
    key = session_key(sid)
    now = utc_now_rfc3339()

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


def assign_user_to_session(
    redis_client: redis.Redis,
    sid: str,
    user_id: str,
    ttl: int,
) -> bool:
    key = session_key(sid)
    now = utc_now_rfc3339()

    with redis_client.pipeline() as pipe:
        while True:
            try:
                pipe.watch(key)
                if not pipe.exists(key):
                    pipe.unwatch()
                    return False

                pipe.multi()
                pipe.hset(key, mapping={"user_id": user_id, "updated_at": now})
                pipe.expire(key, ttl)
                pipe.execute()
                return True
            except redis.WatchError:
                continue


def get_session_user_id(redis_client: redis.Redis, sid: str) -> str | None:
    user_id = redis_client.hget(session_key(sid), "user_id")
    if user_id is None or user_id.strip() == "":
        return None
    return user_id
