import time

import uvicorn
from cassandra import DriverException
from cassandra.cluster import NoHostAvailable
from fastapi import FastAPI
from pymongo.errors import PyMongoError

from app.config import Settings
from app.routes import router
from app.storage import (
    build_cassandra_cluster,
    build_cassandra_session,
    build_mongodb_client,
    build_redis_client,
    ensure_indexes,
    ensure_cassandra_schema,
    resolve_cassandra_consistency,
)

settings = Settings.from_env()
app = FastAPI()
app.state.settings = settings
app.state.redis = build_redis_client(settings)
app.state.mongodb_client = build_mongodb_client(settings)
app.state.mongodb = app.state.mongodb_client[settings.mongodb_database]
app.state.cassandra_cluster = None
app.state.cassandra = None
app.state.cassandra_consistency = resolve_cassandra_consistency(settings)
app.include_router(router)



    for _ in range(settings.startup_retry_attempts):
        try:
            if app.state.cassandra is None:
                app.state.cassandra_cluster = build_cassandra_cluster(settings)
                app.state.cassandra = build_cassandra_session(app.state.cassandra_cluster)

            ensure_indexes(app)
            ensure_cassandra_schema(app)
            return
        except (PyMongoError, NoHostAvailable, DriverException) as exc:
            last_error = exc
            if app.state.cassandra_cluster is not None:
                app.state.cassandra_cluster.shutdown()
                app.state.cassandra_cluster = None
                app.state.cassandra = None
            time.sleep(settings.startup_retry_delay_seconds)

    raise RuntimeError("MongoDB or Cassandra is unavailable") from last_error


@app.on_event("shutdown")
def shutdown() -> None:
    app.state.redis.close()
    app.state.mongodb_client.close()
    if app.state.cassandra_cluster is not None:
        app.state.cassandra_cluster.shutdown()


def main() -> None:
    uvicorn.run(app, host=settings.app_host, port=settings.app_port)


if __name__ == "__main__":
    main()
