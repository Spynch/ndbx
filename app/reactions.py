import hashlib
from datetime import datetime, timezone
from typing import Any

import redis
from cassandra.cluster import Session
from cassandra.query import PreparedStatement
from fastapi import Request

REACTIONS_INCLUDE_TOKEN = "reactions"
REACTIONS_CACHE_PREFIX = "event:"
REACTIONS_CACHE_SUFFIX = ":reactions"
REACTION_LIKE = 1
REACTION_DISLIKE = -1


def should_include_reactions(request: Request) -> bool:
    include_values = request.query_params.getlist("include")
    for include_value in include_values:
        for token in include_value.split(","):
            if token.strip().lower() == REACTIONS_INCLUDE_TOKEN:
                return True
    return False


def empty_reactions() -> dict[str, int]:
    return {"likes": 0, "dislikes": 0}


def reactions_cache_key(event_title: str) -> str:
    title_hash = hashlib.md5(event_title.encode("utf-8")).hexdigest()
    return f"{REACTIONS_CACHE_PREFIX}{title_hash}{REACTIONS_CACHE_SUFFIX}"


def _parse_reactions_payload(payload: Any) -> dict[str, int] | None:
    if not isinstance(payload, dict):
        return None

    likes = payload.get("likes")
    dislikes = payload.get("dislikes")
    if not isinstance(likes, int) or likes < 0:
        return None
    if not isinstance(dislikes, int) or dislikes < 0:
        return None

    return {"likes": likes, "dislikes": dislikes}


def _read_reactions_from_cache(redis_client: redis.Redis, cache_key: str) -> dict[str, int] | None:
    raw_payload = redis_client.hgetall(cache_key)
    if not raw_payload:
        return None

    likes_raw = raw_payload.get("likes")
    dislikes_raw = raw_payload.get("dislikes")
    try:
        likes = int(likes_raw) if likes_raw is not None else 0
        dislikes = int(dislikes_raw) if dislikes_raw is not None else 0
    except (TypeError, ValueError):
        return None

    return _parse_reactions_payload({"likes": likes, "dislikes": dislikes})


def _cache_reactions(redis_client: redis.Redis, cache_key: str, reactions: dict[str, int], ttl: int) -> None:
    with redis_client.pipeline() as pipeline:
        pipeline.delete(cache_key)
        pipeline.hset(cache_key, mapping=reactions)
        pipeline.expire(cache_key, ttl)
        pipeline.execute()


def _in_memory_reaction_totals_for_event_ids(request: Request, event_ids: list[str]) -> tuple[dict[str, int], bool]:
    totals = empty_reactions()
    has_rows = False

    statements = cassandra_statements(request)
    select_statement = statements["select_event_reactions"]
    session: Session = request.app.state.cassandra

    for event_id in event_ids:
        for row in session.execute(select_statement, [event_id]):
            has_rows = True
            like_value = getattr(row, "like_value", None)
            if like_value == REACTION_LIKE:
                totals["likes"] += 1
            elif like_value == REACTION_DISLIKE:
                totals["dislikes"] += 1

    return totals, has_rows


def reactions_for_event_title(request: Request, event_title: str) -> dict[str, int]:
    cache_key = reactions_cache_key(event_title)
    redis_client: redis.Redis = request.app.state.redis

    try:
        cached = _read_reactions_from_cache(redis_client, cache_key)
    except redis.RedisError:
        cached = None

    if cached is not None:
        return cached

    events = list(request.app.state.mongodb["events"].find({"title": event_title}, {"_id": 1}))
    event_ids = [str(event["_id"]) for event in events]
    if not event_ids:
        return empty_reactions()

    totals, has_rows = _in_memory_reaction_totals_for_event_ids(request, event_ids)
    if has_rows:
        try:
            _cache_reactions(redis_client, cache_key, totals, request.app.state.settings.like_ttl)
        except redis.RedisError:
            pass

    return totals


def reactions_for_event_titles(request: Request, event_titles: list[str]) -> dict[str, dict[str, int]]:
    unique_titles = {title for title in event_titles}
    return {title: reactions_for_event_title(request, title) for title in unique_titles}


def invalidate_event_title_reactions_cache(request: Request, event_title: str) -> None:
    try:
        request.app.state.redis.delete(reactions_cache_key(event_title))
    except redis.RedisError:
        pass


def cassandra_statements(request: Request) -> dict[str, PreparedStatement]:
    statements = getattr(request.app.state, "cassandra_prepared", None)
    if statements is None:
        statements = {}
        request.app.state.cassandra_prepared = statements

    session: Session = request.app.state.cassandra
    consistency = request.app.state.cassandra_consistency

    if "upsert_event_reaction" not in statements:
        upsert = session.prepare(
            """
            INSERT INTO event_reactions (event_id, created_by, like_value, created_at)
            VALUES (?, ?, ?, ?)
            """
        )
        upsert.consistency_level = consistency
        statements["upsert_event_reaction"] = upsert

    if "select_event_reactions" not in statements:
        select_reactions = session.prepare(
            """
            SELECT like_value
            FROM event_reactions
            WHERE event_id = ?
            """
        )
        select_reactions.consistency_level = consistency
        statements["select_event_reactions"] = select_reactions

    return statements


def upsert_event_reaction(request: Request, event_id: str, user_id: str, like_value: int) -> None:
    statement = cassandra_statements(request)["upsert_event_reaction"]
    now = datetime.now(timezone.utc)
    request.app.state.cassandra.execute(statement, [event_id, user_id, like_value, now])
