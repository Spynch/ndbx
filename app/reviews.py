import hashlib
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

import redis
from cassandra.cluster import Session
from cassandra.query import PreparedStatement
from fastapi import Request

REVIEWS_INCLUDE_TOKEN = "reviews"
REVIEWS_CACHE_PREFIX = "event:"
REVIEWS_CACHE_SUFFIX = ":reviews"
MAX_COMMENT_LENGTH = 300


def should_include_reviews(request: Request) -> bool:
    include_values = request.query_params.getlist("include")
    for include_value in include_values:
        for token in include_value.split(","):
            if token.strip().lower() == REVIEWS_INCLUDE_TOKEN:
                return True
    return False


def is_valid_review_comment(value: Any) -> bool:
    return isinstance(value, str) and len(value) <= MAX_COMMENT_LENGTH


def is_valid_review_rating(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 5


def empty_reviews() -> dict[str, float | int]:
    return {"count": 0, "rating": 0.0}


def reviews_cache_key(event_title: str) -> str:
    title_hash = hashlib.md5(event_title.encode("utf-8")).hexdigest()
    return f"{REVIEWS_CACHE_PREFIX}{title_hash}{REVIEWS_CACHE_SUFFIX}"


def parse_review_id(raw_value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(raw_value)
    except ValueError:
        return None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _timestamp_to_rfc3339(value: Any) -> str:
    if not isinstance(value, datetime):
        return ""

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)

    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def serialize_review_row(row: Any) -> dict[str, Any]:
    return {
        "id": str(getattr(row, "id")),
        "event_id": getattr(row, "event_id", ""),
        "comment": getattr(row, "comment", ""),
        "created_at": _timestamp_to_rfc3339(getattr(row, "created_at", None)),
        "created_by": getattr(row, "created_by", ""),
        "rating": int(getattr(row, "rating", 0)),
        "updated_at": _timestamp_to_rfc3339(getattr(row, "updated_at", None)),
    }


def _parse_reviews_payload(payload: Any) -> dict[str, float | int] | None:
    if not isinstance(payload, dict):
        return None

    count = payload.get("count")
    rating = payload.get("rating")
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        return None
    if not isinstance(rating, (int, float)) or isinstance(rating, bool) or rating < 0:
        return None

    return {"count": count, "rating": float(rating)}


def _read_reviews_from_cache(redis_client: redis.Redis, cache_key: str) -> dict[str, float | int] | None:
    raw_payload = redis_client.hgetall(cache_key)
    if not raw_payload:
        return None

    try:
        count = int(raw_payload.get("count", "0"))
        rating = float(raw_payload.get("rating", "0"))
    except (TypeError, ValueError):
        return None

    return _parse_reviews_payload({"count": count, "rating": rating})


def _cache_reviews(
    redis_client: redis.Redis,
    cache_key: str,
    reviews: dict[str, float | int],
    ttl: int,
) -> None:
    with redis_client.pipeline() as pipeline:
        pipeline.delete(cache_key)
        pipeline.hset(cache_key, mapping=reviews)
        pipeline.expire(cache_key, ttl)
        pipeline.execute()


def _round_rating(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def _in_memory_review_totals_for_event_ids(request: Request, event_ids: list[str]) -> tuple[int, int]:
    count = 0
    rating_sum = 0

    statements = cassandra_statements(request)
    select_statement = statements["select_event_review_ratings"]
    session: Session = request.app.state.cassandra

    for event_id in event_ids:
        for row in session.execute(select_statement, [event_id]):
            rating = getattr(row, "rating", None)
            if isinstance(rating, int):
                count += 1
                rating_sum += rating

    return count, rating_sum


def reviews_for_event_title(request: Request, event_title: str) -> dict[str, float | int]:
    cache_key = reviews_cache_key(event_title)
    redis_client: redis.Redis = request.app.state.redis

    try:
        cached = _read_reviews_from_cache(redis_client, cache_key)
    except redis.RedisError:
        cached = None

    if cached is not None:
        return cached

    events = list(request.app.state.mongodb["events"].find({"title": event_title}, {"_id": 1}))
    event_ids = [str(event["_id"]) for event in events]
    if not event_ids:
        return empty_reviews()

    count, rating_sum = _in_memory_review_totals_for_event_ids(request, event_ids)
    if count == 0:
        return empty_reviews()

    average = _round_rating(Decimal(rating_sum) / Decimal(count))
    reviews = {"count": count, "rating": average}

    try:
        _cache_reviews(redis_client, cache_key, reviews, request.app.state.settings.event_reviews_ttl)
    except redis.RedisError:
        pass

    return reviews


def reviews_for_event_titles(request: Request, event_titles: list[str]) -> dict[str, dict[str, float | int]]:
    unique_titles = {title for title in event_titles}
    return {title: reviews_for_event_title(request, title) for title in unique_titles}


def invalidate_event_title_reviews_cache(request: Request, event_title: str) -> None:
    try:
        request.app.state.redis.delete(reviews_cache_key(event_title))
    except redis.RedisError:
        pass


def refresh_event_title_reviews_cache(request: Request, event_title: str) -> dict[str, float | int]:
    invalidate_event_title_reviews_cache(request, event_title)
    return reviews_for_event_title(request, event_title)


def cassandra_statements(request: Request) -> dict[str, PreparedStatement]:
    statements = getattr(request.app.state, "cassandra_prepared", None)
    if statements is None:
        statements = {}
        request.app.state.cassandra_prepared = statements

    session: Session = request.app.state.cassandra
    consistency = request.app.state.cassandra_consistency

    if "insert_event_review_if_absent" not in statements:
        insert_review = session.prepare(
            """
            INSERT INTO event_reviews (event_id, created_by, id, rating, comment, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            IF NOT EXISTS
            """
        )
        insert_review.consistency_level = consistency
        statements["insert_event_review_if_absent"] = insert_review

    if "select_event_reviews" not in statements:
        select_reviews = session.prepare(
            """
            SELECT id, event_id, rating, comment, created_at, created_by, updated_at
            FROM event_reviews
            WHERE event_id = ?
            """
        )
        select_reviews.consistency_level = consistency
        statements["select_event_reviews"] = select_reviews

    if "select_event_review_by_owner" not in statements:
        select_review = session.prepare(
            """
            SELECT id, event_id, rating, comment, created_at, created_by, updated_at
            FROM event_reviews
            WHERE event_id = ? AND created_by = ?
            """
        )
        select_review.consistency_level = consistency
        statements["select_event_review_by_owner"] = select_review

    if "select_event_review_ratings" not in statements:
        select_ratings = session.prepare(
            """
            SELECT rating
            FROM event_reviews
            WHERE event_id = ?
            """
        )
        select_ratings.consistency_level = consistency
        statements["select_event_review_ratings"] = select_ratings

    if "update_event_review_rating_comment" not in statements:
        update_rating_comment = session.prepare(
            """
            UPDATE event_reviews
            SET rating = ?, comment = ?, updated_at = ?
            WHERE event_id = ? AND created_by = ?
            """
        )
        update_rating_comment.consistency_level = consistency
        statements["update_event_review_rating_comment"] = update_rating_comment

    if "update_event_review_rating" not in statements:
        update_rating = session.prepare(
            """
            UPDATE event_reviews
            SET rating = ?, updated_at = ?
            WHERE event_id = ? AND created_by = ?
            """
        )
        update_rating.consistency_level = consistency
        statements["update_event_review_rating"] = update_rating

    if "update_event_review_comment" not in statements:
        update_comment = session.prepare(
            """
            UPDATE event_reviews
            SET comment = ?, updated_at = ?
            WHERE event_id = ? AND created_by = ?
            """
        )
        update_comment.consistency_level = consistency
        statements["update_event_review_comment"] = update_comment

    if "touch_event_review" not in statements:
        touch_review = session.prepare(
            """
            UPDATE event_reviews
            SET updated_at = ?
            WHERE event_id = ? AND created_by = ?
            """
        )
        touch_review.consistency_level = consistency
        statements["touch_event_review"] = touch_review

    return statements


def create_event_review(request: Request, event_id: str, user_id: str, comment: str, rating: int) -> uuid.UUID | None:
    review_id = uuid.uuid4()
    now = _utc_now()
    statement = cassandra_statements(request)["insert_event_review_if_absent"]
    result = request.app.state.cassandra.execute(
        statement,
        [event_id, user_id, review_id, rating, comment, now, now],
    )
    row = result.one()
    if row is None or not bool(getattr(row, "applied", False)):
        return None
    return review_id


def list_event_reviews(request: Request, event_id: str, limit: int | None, offset: int | None) -> list[dict[str, Any]]:
    if limit == 0:
        return []

    statement = cassandra_statements(request)["select_event_reviews"]
    rows = request.app.state.cassandra.execute(statement, [event_id])
    skip = offset or 0
    reviews: list[dict[str, Any]] = []

    for row in rows:
        if skip > 0:
            skip -= 1
            continue

        if limit is not None and len(reviews) >= limit:
            break

        reviews.append(serialize_review_row(row))

    return reviews


def update_event_review(
    request: Request,
    event_id: str,
    user_id: str,
    review_id: uuid.UUID,
    comment: str | None,
    rating: int | None,
) -> bool:
    statements = cassandra_statements(request)
    row = request.app.state.cassandra.execute(
        statements["select_event_review_by_owner"],
        [event_id, user_id],
    ).one()
    if row is None or getattr(row, "id", None) != review_id:
        return False

    now = _utc_now()
    if rating is not None and comment is not None:
        request.app.state.cassandra.execute(
            statements["update_event_review_rating_comment"],
            [rating, comment, now, event_id, user_id],
        )
    elif rating is not None:
        request.app.state.cassandra.execute(
            statements["update_event_review_rating"],
            [rating, now, event_id, user_id],
        )
    elif comment is not None:
        request.app.state.cassandra.execute(
            statements["update_event_review_comment"],
            [comment, now, event_id, user_id],
        )
    else:
        request.app.state.cassandra.execute(
            statements["touch_event_review"],
            [now, event_id, user_id],
        )

    return True
