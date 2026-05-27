CREATE KEYSPACE IF NOT EXISTS __CASSANDRA_KEYSPACE__
WITH replication = {
  'class': '__CASSANDRA_REPLICATION_CLASS__',
  'replication_factor': __CASSANDRA_REPLICATION_FACTOR__
};

USE __CASSANDRA_KEYSPACE__;

CREATE TABLE IF NOT EXISTS event_reactions (
  event_id text,
  created_by text,
  like_value tinyint,
  created_at timestamp,
  PRIMARY KEY (event_id, created_by)
);

CREATE INDEX IF NOT EXISTS event_reactions_like_value_idx
ON event_reactions (like_value);

CREATE INDEX IF NOT EXISTS event_reactions_created_by_idx
ON event_reactions (created_by);
