from urllib.parse import quote_plus

import redis
from cassandra import ConsistencyLevel
from cassandra.auth import PlainTextAuthProvider
from cassandra.cluster import Cluster, Session
from fastapi import FastAPI
from pymongo import ASCENDING, MongoClient

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


def ensure_indexes(app_instance: FastAPI) -> None:
    database = app_instance.state.mongodb
    users = database["users"]
    events = database["events"]

    users.create_index([("username", ASCENDING)], unique=True)
    users.create_index([("full_name", ASCENDING)])
    events.create_index([("title", ASCENDING), ("created_by", ASCENDING)])
    events.create_index([("created_by", ASCENDING), ("title", ASCENDING)])
    events.create_index([("created_by", ASCENDING)])
    events.create_index([("title", ASCENDING)])
    events.create_index([("category", ASCENDING)])
    events.create_index([("price", ASCENDING)])
    events.create_index([("location.city", ASCENDING)])
    events.create_index([("started_at", ASCENDING)])


def ensure_cassandra_schema(app_instance: FastAPI) -> None:
    settings: Settings = app_instance.state.settings
    keyspace = settings.cassandra_keyspace
    replication_class = settings.cassandra_replication_class
    replication_factor = settings.cassandra_replication_factor
    session: Session = app_instance.state.cassandra

    session.execute(
        f"""
        CREATE KEYSPACE IF NOT EXISTS {keyspace}
        WITH replication = {{'class': '{replication_class}', 'replication_factor': {replication_factor}}}
        """
    )
    session.set_keyspace(keyspace)
    session.execute(
        """
        CREATE TABLE IF NOT EXISTS event_reactions (
            event_id text,
            created_by text,
            like_value tinyint,
            created_at timestamp,
            PRIMARY KEY (event_id, created_by)
        )
        """
    )
    session.execute(
        """
        CREATE INDEX IF NOT EXISTS event_reactions_like_value_idx
        ON event_reactions (like_value)
        """
    )
    session.execute(
        """
        CREATE INDEX IF NOT EXISTS event_reactions_created_by_idx
        ON event_reactions (created_by)
        """
    )
    session.execute(
        """
        CREATE TABLE IF NOT EXISTS event_reviews (
            event_id text,
            created_by text,
            id uuid,
            rating tinyint,
            comment text,
            created_at timestamp,
            updated_at timestamp,
            PRIMARY KEY (event_id, created_by)
        )
        """
    )
