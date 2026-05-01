import os
import re
from dataclasses import dataclass

IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def _read_required_env(name: str) -> str:
    value = os.getenv(name)
    if value is None:
        raise ValueError(f"{name} is required")
    return value


def _read_mongodb_database_env() -> str:
    value = os.getenv("MONGODB_DATABSE")
    if value is not None:
        return value

    fallback = os.getenv("MONGODB_DATABASE")
    if fallback is not None:
        return fallback

    raise ValueError("MONGODB_DATABSE is required")


def _read_hosts_env(name: str) -> tuple[str, ...]:
    value = _read_required_env(name)
    hosts = [host.strip() for host in value.split(",") if host.strip() != ""]
    if not hosts:
        raise ValueError(f"{name} must contain at least one host")
    return tuple(hosts)


def _read_nonempty_env(name: str) -> str:
    value = _read_required_env(name).strip()
    if value == "":
        raise ValueError(f"{name} must not be empty")
    return value


def _read_cassandra_keyspace_env() -> str:
    value = _read_nonempty_env("CASSANDRA_KEYSPACE")
    cleaned = value.strip('"').strip("'")
    if not IDENTIFIER_PATTERN.fullmatch(cleaned):
        raise ValueError(f"{value} is invalid CASSANDRA_KEYSPACE")
    return cleaned


def _read_cassandra_consistency_env() -> str:
    value = _read_nonempty_env("CASSANDRA_CONSISTENCY")
    return value.strip('"').strip("'").upper()


def _read_cassandra_replication_class_env() -> str:
    value = _read_nonempty_env("CASSANDRA_REPLICATION_CLASS")
    cleaned = value.strip('"').strip("'")
    if not IDENTIFIER_PATTERN.fullmatch(cleaned):
        raise ValueError(f"{value} is invalid CASSANDRA_REPLICATION_CLASS")
    return cleaned


def _read_int_env(name: str, min_value: int = 0) -> int:
    value = _read_required_env(name)
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got: {value}") from exc

    if parsed < min_value:
        raise ValueError(f"{name} must be >= {min_value}, got: {parsed}")
    return parsed


def _read_float_env(name: str, min_value: float = 0.0) -> float:
    value = _read_required_env(name)
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got: {value}") from exc

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
    like_ttl: int
    mongodb_database: str
    mongodb_user: str
    mongodb_password: str
    mongodb_host: str
    mongodb_port: int
    cassandra_hosts: tuple[str, ...]
    cassandra_port: int
    cassandra_username: str
    cassandra_password: str
    cassandra_keyspace: str
    cassandra_consistency: str
    cassandra_replication_class: str
    cassandra_replication_factor: int
    startup_retry_attempts: int
    startup_retry_delay_seconds: float

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
            like_ttl=_read_int_env("APP_LIKE_TTL", min_value=1),
            mongodb_database=_read_mongodb_database_env(),
            mongodb_user=_read_required_env("MONGODB_USER"),
            mongodb_password=_read_required_env("MONGODB_PASSWORD"),
            mongodb_host=_read_required_env("MONGODB_HOST"),
            mongodb_port=_read_int_env("MONGODB_PORT", min_value=1),
            cassandra_hosts=_read_hosts_env("CASSANDRA_HOSTS"),
            cassandra_port=_read_int_env("CASSANDRA_PORT", min_value=1),
            cassandra_username=_read_required_env("CASSANDRA_USERNAME"),
            cassandra_password=_read_required_env("CASSANDRA_PASSWORD"),
            cassandra_keyspace=_read_cassandra_keyspace_env(),
            cassandra_consistency=_read_cassandra_consistency_env(),
            cassandra_replication_class=_read_cassandra_replication_class_env(),
            cassandra_replication_factor=_read_int_env("CASSANDRA_REPLICATION_FACTOR", min_value=1),
            startup_retry_attempts=_read_int_env("APP_STARTUP_RETRY_ATTEMPTS", min_value=1),
            startup_retry_delay_seconds=_read_float_env("APP_STARTUP_RETRY_DELAY_SECONDS", min_value=0.0),
        )
