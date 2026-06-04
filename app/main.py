import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from cassandra import DriverException
from cassandra.cluster import NoHostAvailable
from fastapi import FastAPI
from neo4j.exceptions import Neo4jError, ServiceUnavailable, SessionExpired
from pymongo.errors import PyMongoError

from app.config import Settings
from app.routes import router
from app.storage import (
    build_cassandra_cluster,
    build_cassandra_session,
    build_mongodb_client,
    build_neo4j_driver,
    build_redis_client,
    resolve_cassandra_consistency,
)

settings = Settings.from_env()


def close_cassandra(app_instance: FastAPI) -> None:
    if app_instance.state.cassandra_cluster is not None:
        app_instance.state.cassandra_cluster.shutdown()
        app_instance.state.cassandra_cluster = None
        app_instance.state.cassandra = None


def connect_datastores(app_instance: FastAPI) -> None:
    app_instance.state.mongodb.command("ping")
    app_instance.state.cassandra_cluster = build_cassandra_cluster(settings)
    app_instance.state.cassandra = build_cassandra_session(app_instance.state.cassandra_cluster)
    app_instance.state.cassandra.set_keyspace(settings.cassandra_keyspace)
    app_instance.state.neo4j_driver.verify_connectivity()


@asynccontextmanager
async def lifespan(app_instance: FastAPI) -> AsyncIterator[None]:
    last_error: Exception | None = None

    for attempt in range(settings.startup_retry_attempts):
        try:
            connect_datastores(app_instance)
            break
        except (
            PyMongoError,
            NoHostAvailable,
            DriverException,
            Neo4jError,
            ServiceUnavailable,
            SessionExpired,
        ) as exc:
            last_error = exc
            close_cassandra(app_instance)
            if attempt == settings.startup_retry_attempts - 1:
                raise RuntimeError("MongoDB, Cassandra or Neo4j is unavailable") from last_error
            time.sleep(settings.startup_retry_delay_seconds)

    try:
        yield
    finally:
        app_instance.state.redis.close()
        app_instance.state.mongodb_client.close()
        app_instance.state.neo4j_driver.close()
        close_cassandra(app_instance)


app = FastAPI(lifespan=lifespan)
app.state.settings = settings
app.state.redis = build_redis_client(settings)
app.state.mongodb_client = build_mongodb_client(settings)
app.state.mongodb = app.state.mongodb_client[settings.mongodb_database]
app.state.neo4j_driver = build_neo4j_driver(settings)
app.state.cassandra_cluster = None
app.state.cassandra = None
app.state.cassandra_consistency = resolve_cassandra_consistency(settings)
app.include_router(router)


def main() -> None:
    uvicorn.run(app, host=settings.app_host, port=settings.app_port)


if __name__ == "__main__":
    main()
