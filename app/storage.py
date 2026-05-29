from urllib.parse import quote_plus

import redis
from cassandra import ConsistencyLevel
from cassandra.auth import PlainTextAuthProvider
from cassandra.cluster import Cluster, Session
from pymongo import MongoClient

from app.config import Settings

def build_redis_client(settings: Settings) -> redis.Redis:
    password = settings.redis_password or None
    return redis.Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        password=password,
        db=settings.redis_db,
        decode_responses=True,
    )


def build_mongodb_client(settings: Settings) -> MongoClient:
    auth = ""
    query = ""

    if settings.mongodb_user:
        user = quote_plus(settings.mongodb_user)
        password = quote_plus(settings.mongodb_password)
        auth = f"{user}:{password}@"
        query = "?authSource=admin"

    uri = f"mongodb://{auth}{settings.mongodb_host}:{settings.mongodb_port}/{query}"
    return MongoClient(uri, serverSelectionTimeoutMS=5000)


def build_cassandra_cluster(settings: Settings) -> Cluster:
    cluster_options: dict[str, object] = {
        "contact_points": list(settings.cassandra_hosts),
        "port": settings.cassandra_port,
    }

    if settings.cassandra_username != "":
        cluster_options["auth_provider"] = PlainTextAuthProvider(
            username=settings.cassandra_username,
            password=settings.cassandra_password,
        )

    return Cluster(**cluster_options)


def build_cassandra_session(cluster: Cluster) -> Session:
    return cluster.connect()


def resolve_cassandra_consistency(settings: Settings) -> int:
    consistency = getattr(ConsistencyLevel, settings.cassandra_consistency, None)
    if not isinstance(consistency, int):
        raise ValueError(f'Unsupported CASSANDRA_CONSISTENCY: "{settings.cassandra_consistency}"')
    return consistency
