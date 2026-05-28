import json
from datetime import datetime, timezone
from typing import Any

import redis
from fastapi import Request

from app.http_utils import parse_object_id, parse_rfc3339, serialize_event
from app.neo4j_graph import recommended_event_ids

RECOMMENDATIONS_CACHE_FIELD = "events"


def recommendations_cache_key(user_id: str) -> str:
    return f"user:{user_id}:recomms"


def read_recommendations_cache(redis_client: redis.Redis, user_id: str) -> list[dict[str, Any]] | None:
    raw_events = redis_client.hget(recommendations_cache_key(user_id), RECOMMENDATIONS_CACHE_FIELD)
    if raw_events is None:
        return None

    try:
        events = json.loads(raw_events)
    except json.JSONDecodeError:
        return None

    if not isinstance(events, list):
        return None

    if any(not isinstance(event, dict) for event in events):
        return None

    return events


def write_recommendations_cache(redis_client: redis.Redis, user_id: str, events: list[dict[str, Any]], ttl: int) -> None:
    key = recommendations_cache_key(user_id)
    payload = json.dumps(events, ensure_ascii=False, separators=(",", ":"))
    with redis_client.pipeline() as pipeline:
        pipeline.delete(key)
        pipeline.hset(key, RECOMMENDATIONS_CACHE_FIELD, payload)
        pipeline.expire(key, ttl)
        pipeline.execute()


def build_user_recommendations(request: Request, user_id: str) -> list[dict[str, Any]]:
    ranked_ids = recommended_event_ids(request.app.state.neo4j_driver, user_id)
    if not ranked_ids:
        return []

    likes_by_id = {event_id: likes for event_id, likes in ranked_ids}
    position_by_id = {event_id: position for position, (event_id, _) in enumerate(ranked_ids)}
    object_ids = [object_id for event_id, _ in ranked_ids if (object_id := parse_object_id(event_id)) is not None]
    if not object_ids:
        return []

    documents = list(request.app.state.mongodb["events"].find({"_id": {"$in": object_ids}}))
    grouped = _group_documents_by_title(documents, likes_by_id, position_by_id)
    sorted_groups = sorted(
        grouped.values(),
        key=lambda group: (
            -group["likes"],
            _started_at_sort_key(group["document"]),
            group["title"],
        ),
    )

    return [serialize_event(group["document"]) for group in sorted_groups]


def _group_documents_by_title(
    documents: list[dict[str, Any]],
    likes_by_id: dict[str, int],
    position_by_id: dict[str, int],
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}

    for document in documents:
        event_id = str(document.get("_id"))
        if event_id not in likes_by_id:
            continue

        title = document.get("title")
        if not isinstance(title, str) or title == "":
            title = event_id

        group = grouped.get(title)
        if group is None:
            grouped[title] = {
                "title": title,
                "document": document,
                "likes": likes_by_id[event_id],
                "position": position_by_id[event_id],
                "document_position": position_by_id[event_id],
            }
            continue

        current_document_position = group["document_position"]
        group["likes"] += likes_by_id[event_id]
        group["position"] = min(group["position"], position_by_id[event_id])
        if _document_precedes(document, group["document"], position_by_id[event_id], current_document_position):
            group["document"] = document
            group["document_position"] = position_by_id[event_id]

    return grouped


def _document_precedes(
    candidate: dict[str, Any],
    current: dict[str, Any],
    candidate_position: int,
    current_position: int,
) -> bool:
    candidate_key = (_started_at_sort_key(candidate), candidate_position)
    current_key = (_started_at_sort_key(current), current_position)
    return candidate_key < current_key


def _started_at_sort_key(document: dict[str, Any]) -> tuple[int, datetime]:
    started_at = document.get("started_at")
    if not isinstance(started_at, str):
        return 1, datetime.max.replace(tzinfo=timezone.utc)

    parsed = parse_rfc3339(started_at)
    if parsed is None:
        return 1, datetime.max.replace(tzinfo=timezone.utc)

    return 0, parsed.astimezone(timezone.utc)
