from neo4j import Driver


def create_user_node(driver: Driver, user_id: str) -> None:
    with driver.session() as session:
        session.run(
            """
            MERGE (:User {id: $user_id})
            """,
            user_id=user_id,
        ).consume()


def create_event_node(driver: Driver, event_id: str, title: str) -> None:
    with driver.session() as session:
        session.run(
            """
            MERGE (event:Event {id: $event_id})
            SET event.title = $title
            """,
            event_id=event_id,
            title=title,
        ).consume()


def create_liked_relationship(driver: Driver, user_id: str, event_id: str, event_title: str) -> bool:
    with driver.session() as session:
        result = session.run(
            """
            MERGE (user:User {id: $user_id})
            MERGE (event:Event {id: $event_id})
            SET event.title = $event_title
            MERGE (user)-[:LIKED]->(event)
            """,
            user_id=user_id,
            event_id=event_id,
            event_title=event_title,
        )
        summary = result.consume()
        return summary.counters.relationships_created > 0


def delete_liked_relationship(driver: Driver, user_id: str, event_id: str) -> None:
    with driver.session() as session:
        session.run(
            """
            MATCH (:User {id: $user_id})-[liked:LIKED]->(:Event {id: $event_id})
            DELETE liked
            """,
            user_id=user_id,
            event_id=event_id,
        ).consume()


def recommended_event_ids(driver: Driver, user_id: str) -> list[tuple[str, int]]:
    with driver.session() as session:
        result = session.run(
            """
            MATCH (target:User {id: $user_id})-[:LIKED]->(:Event)<-[:LIKED]-(other:User)-[:LIKED]->(candidate:Event)
            WHERE other.id <> $user_id
              AND NOT EXISTS {
                MATCH (target)-[:LIKED]->(candidate)
              }
            WITH DISTINCT candidate
            MATCH (candidate)<-[:LIKED]-(liker:User)
            RETURN candidate.id AS event_id, count(DISTINCT liker) AS likes
            ORDER BY likes DESC, event_id ASC
            """,
            user_id=user_id,
        )

        recommendations: list[tuple[str, int]] = []
        for record in result:
            event_id = record.get("event_id")
            likes = record.get("likes")
            if isinstance(event_id, str) and isinstance(likes, int):
                recommendations.append((event_id, likes))

        return recommendations
