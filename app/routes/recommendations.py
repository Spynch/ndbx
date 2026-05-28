import redis
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from neo4j.exceptions import Neo4jError, ServiceUnavailable, SessionExpired
from pymongo.errors import PyMongoError

from app.recommendations import (
    build_user_recommendations,
    read_recommendations_cache,
    write_recommendations_cache,
)
from app.routes.common import require_authenticated_user
from app.session import set_session_cookie

router = APIRouter()


@router.get("/recommendations")
def get_recommendations(request: Request) -> Response:
    auth_result = require_authenticated_user(request)
    if isinstance(auth_result, Response):
        return auth_result

    sid, user_id = auth_result

    try:
        events = read_recommendations_cache(request.app.state.redis, user_id)
        if events is None:
            events = build_user_recommendations(request, user_id)
            write_recommendations_cache(
                request.app.state.redis,
                user_id,
                events,
                request.app.state.settings.recommendations_ttl,
            )
    except redis.RedisError as exc:
        raise HTTPException(status_code=503, detail="Redis is unavailable") from exc
    except (Neo4jError, ServiceUnavailable, SessionExpired) as exc:
        raise HTTPException(status_code=503, detail="Neo4j is unavailable") from exc
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail="MongoDB is unavailable") from exc

    response = JSONResponse(status_code=200, content={"events": events})
    set_session_cookie(response, sid, request.app.state.settings.session_ttl)
    return response
