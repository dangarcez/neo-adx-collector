from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .exceptions import ConfigurationError
from .models import (
    GraphNode,
    GraphRelationship,
    MutationResult,
    NodeMatch,
    NodeMutation,
    RelationshipMutation,
)

LOGGER = logging.getLogger(__name__)


class DryRunGraphRepository:
    def __init__(self) -> None:
        self._logger = logging.getLogger(f"{__name__}.dry_run")

    def connect(self) -> None:
        return None

    def close(self) -> None:
        return None

    def ensure_schema(self) -> None:
        return None

    def upsert_node(self, mutation: NodeMutation) -> MutationResult:
        self._logger.info(
            "dry_run_node",
            extra={"name": mutation.business_properties.get("name"), "labels": mutation.labels},
        )
        return MutationResult(
            action="skipped",
            kind="node",
            identifier=str(mutation.business_properties.get("name", mutation.stable_key)),
            reason="dry_run",
        )

    def upsert_relationship(self, mutation: RelationshipMutation) -> list[MutationResult]:
        self._logger.info(
            "dry_run_relationship",
            extra={"type": mutation.type, "stable_key": mutation.stable_key},
        )
        return [
            MutationResult(
                action="skipped",
                kind="relationship",
                identifier=mutation.stable_key,
                reason="dry_run",
            )
        ]


class Neo4jGraphRepository:
    def __init__(self, uri: str | None, database: str, username: str, password: str | None, timeout_seconds: int, verify_connectivity: bool, apply_schema: bool):
        if not uri:
            raise ConfigurationError("NEO4J_URI is required unless runtime.dry_run=true.")
        if not password:
            raise ConfigurationError("NEO4J_PASSWORD is required unless runtime.dry_run=true.")

        self.uri = uri
        self.database = database
        self.username = username
        self.password = password
        self.timeout_seconds = timeout_seconds
        self.verify_connectivity = verify_connectivity
        self.apply_schema = apply_schema
        self._driver = None

    def connect(self) -> None:
        try:
            from neo4j import GraphDatabase
        except ImportError as exc:
            raise ConfigurationError(
                "Missing dependency 'neo4j'. Install project dependencies before running."
            ) from exc

        self._driver = GraphDatabase.driver(
            self.uri,
            auth=(self.username, self.password),
            connection_timeout=self.timeout_seconds,
        )
        if self.verify_connectivity:
            self._driver.verify_connectivity()
        if self.apply_schema:
            self.ensure_schema()

    def close(self) -> None:
        if self._driver is not None:
            self._driver.close()
            self._driver = None

    def ensure_schema(self) -> None:
        queries = [
            "CREATE CONSTRAINT entity_node_uid_unique IF NOT EXISTS FOR (n:Entity) REQUIRE n.node_uid IS UNIQUE",
            "CREATE CONSTRAINT rel_uid_unique IF NOT EXISTS FOR ()-[r]-() REQUIRE r.rel_uid IS UNIQUE",
            "CREATE INDEX entity_name_index IF NOT EXISTS FOR (n:Entity) ON (n.name)",
        ]
        with self._session() as session:
            for query in queries:
                try:
                    session.run(query).consume()
                except Exception:
                    LOGGER.exception("schema_query_failed", extra={"query": query})

    def upsert_node(self, mutation: NodeMutation) -> MutationResult:
        existing = self._find_equivalent_node(mutation)
        name = str(mutation.business_properties.get("name"))
        if len(existing) > 1:
            return MutationResult(
                action="skipped",
                kind="node",
                identifier=name,
                reason="multiple equivalent nodes found",
            )

        if not existing:
            self._create_node(mutation)
            return MutationResult(action="created", kind="node", identifier=name)

        current = existing[0]
        if mutation.update_policy == "create":
            return MutationResult(action="skipped", kind="node", identifier=name, reason="already exists")

        if mutation.update_policy == "merge_at_change" and not self._node_needs_update(current, mutation):
            return MutationResult(action="skipped", kind="node", identifier=name, reason="no business changes")

        self._update_node(current, mutation)
        return MutationResult(action="updated", kind="node", identifier=name)

    def upsert_relationship(self, mutation: RelationshipMutation) -> list[MutationResult]:
        source_nodes = self._find_nodes(mutation.source_match)
        target_nodes = self._find_nodes(mutation.target_match)
        if not source_nodes or not target_nodes:
            return [
                MutationResult(
                    action="skipped",
                    kind="relationship",
                    identifier=mutation.stable_key,
                    reason="source or target not found",
                )
            ]

        results: list[MutationResult] = []
        for source in source_nodes:
            for target in target_nodes:
                results.append(self._upsert_relationship_between(source, target, mutation))
        return results

    def _upsert_relationship_between(
        self,
        source: GraphNode,
        target: GraphNode,
        mutation: RelationshipMutation,
    ) -> MutationResult:
        if not source.node_uid or not target.node_uid:
            return MutationResult(
                action="skipped",
                kind="relationship",
                identifier=mutation.stable_key,
                reason="source or target node has no node_uid",
            )

        existing = self._find_equivalent_relationship(source, target, mutation)
        identifier = f"{source.node_uid}->{target.node_uid}:{mutation.type}"
        if len(existing) > 1:
            return MutationResult(
                action="skipped",
                kind="relationship",
                identifier=identifier,
                reason="multiple equivalent relationships found",
            )

        if not existing:
            self._create_relationship(source, target, mutation)
            return MutationResult(action="created", kind="relationship", identifier=identifier)

        current = existing[0]
        if mutation.update_policy == "create":
            return MutationResult(action="skipped", kind="relationship", identifier=identifier, reason="already exists")

        if mutation.update_policy == "merge_at_change" and not self._relationship_needs_update(current, mutation):
            return MutationResult(action="skipped", kind="relationship", identifier=identifier, reason="no business changes")

        self._update_relationship(source, target, current, mutation)
        return MutationResult(action="updated", kind="relationship", identifier=identifier)

    def _find_equivalent_node(self, mutation: NodeMutation) -> list[GraphNode]:
        query = """
        MATCH (n:Entity {name: $name})
        WHERE
          any(hash IN $template_hashes WHERE hash IN coalesce(n.template_hashes, []))
          OR all(label IN $labels WHERE label IN labels(n))
        RETURN elementId(n) AS element_id, labels(n) AS labels, properties(n) AS properties
        LIMIT 2
        """
        params = {
            "name": mutation.business_properties.get("name"),
            "template_hashes": mutation.template_hashes,
            "labels": [label for label in mutation.labels if label != "Entity"],
        }
        return self._read_nodes(query, params)

    def _find_nodes(self, match: NodeMatch) -> list[GraphNode]:
        query, params = _build_node_match_query(match)
        return self._read_nodes(query, params)

    def _find_equivalent_relationship(
        self,
        source: GraphNode,
        target: GraphNode,
        mutation: RelationshipMutation,
    ) -> list[GraphRelationship]:
        query = """
        MATCH (s)-[r]->(t)
        WHERE elementId(s) = $source_id
          AND elementId(t) = $target_id
          AND (
            type(r) = $rel_type
            OR $template_hash IN coalesce(r.template_hashes, [])
          )
        RETURN elementId(r) AS element_id, type(r) AS rel_type, properties(r) AS properties
        LIMIT 2
        """
        with self._session() as session:
            result = session.run(
                query,
                {
                    "source_id": source.element_id,
                    "target_id": target.element_id,
                    "rel_type": mutation.type,
                    "template_hash": mutation.template_hash,
                },
            )
            return [
                GraphRelationship(
                    element_id=record["element_id"],
                    rel_type=record["rel_type"],
                    properties=dict(record["properties"]),
                )
                for record in result
            ]

    def _create_node(self, mutation: NodeMutation) -> None:
        query = f"""
        CREATE (n{_labels_fragment(mutation.labels)})
        SET n = $properties
        """
        with self._session() as session:
            session.run(query, {"properties": mutation.properties}).consume()

    def _update_node(self, existing: GraphNode, mutation: NodeMutation) -> None:
        current_hashes = _as_string_list(existing.properties.get("template_hashes"))
        merged_hashes = _merge_unique(current_hashes, mutation.template_hashes)
        current_origin = existing.properties.get("origin") or "auto"
        property_updates = dict(mutation.business_properties)
        property_updates["updated_at"] = mutation.properties["updated_at"]
        property_updates["template_hashes"] = merged_hashes
        query = f"""
        MATCH (n)
        WHERE elementId(n) = $element_id
        SET n += $property_updates
        SET n.origin = coalesce(n.origin, $origin)
        SET n{_labels_fragment(mutation.labels)}
        """
        with self._session() as session:
            session.run(
                query,
                {
                    "element_id": existing.element_id,
                    "property_updates": property_updates,
                    "origin": current_origin,
                },
            ).consume()

    def _create_relationship(self, source: GraphNode, target: GraphNode, mutation: RelationshipMutation) -> None:
        query = f"""
        MATCH (source), (target)
        WHERE elementId(source) = $source_id AND elementId(target) = $target_id
        CREATE (source)-[r:{_escape_identifier(mutation.type)}]->(target)
        SET r = $properties
        """
        with self._session() as session:
            session.run(
                query,
                {
                    "source_id": source.element_id,
                    "target_id": target.element_id,
                    "properties": mutation.properties,
                },
            ).consume()

    def _update_relationship(
        self,
        source: GraphNode,
        target: GraphNode,
        existing: GraphRelationship,
        mutation: RelationshipMutation,
    ) -> None:
        current_hashes = _as_string_list(existing.properties.get("template_hashes"))
        merged_hashes = _merge_unique(current_hashes, [mutation.template_hash])
        final_properties = dict(mutation.business_properties)
        final_properties["rel_uid"] = existing.properties.get("rel_uid") or mutation.properties["rel_uid"]
        final_properties["origin"] = existing.properties.get("origin") or "auto"
        final_properties["template_hashes"] = merged_hashes
        final_properties["created_at"] = existing.properties.get("created_at") or mutation.properties["created_at"]
        final_properties["updated_at"] = mutation.properties["updated_at"]

        if existing.rel_type != mutation.type:
            query = f"""
            MATCH (source)-[old]->(target)
            WHERE elementId(source) = $source_id
              AND elementId(target) = $target_id
              AND elementId(old) = $relationship_id
            DELETE old
            CREATE (source)-[new:{_escape_identifier(mutation.type)}]->(target)
            SET new = $properties
            """
            with self._session() as session:
                session.run(
                    query,
                    {
                        "source_id": source.element_id,
                        "target_id": target.element_id,
                        "relationship_id": existing.element_id,
                        "properties": final_properties,
                    },
                ).consume()
            return

        query = """
        MATCH ()-[r]->()
        WHERE elementId(r) = $relationship_id
        SET r += $properties
        """
        with self._session() as session:
            session.run(
                query,
                {
                    "relationship_id": existing.element_id,
                    "properties": final_properties,
                },
            ).consume()

    def _read_nodes(self, query: str, params: dict[str, Any]) -> list[GraphNode]:
        with self._session() as session:
            result = session.run(query, params)
            return [
                GraphNode(
                    element_id=record["element_id"],
                    labels=list(record["labels"]),
                    properties=dict(record["properties"]),
                )
                for record in result
            ]

    def _node_needs_update(self, existing: GraphNode, mutation: NodeMutation) -> bool:
        existing_hashes = set(_as_string_list(existing.properties.get("template_hashes")))
        expected_hashes = set(mutation.template_hashes)
        expected_labels = set(mutation.labels)
        current_labels = set(existing.labels)
        if not expected_hashes.issubset(existing_hashes):
            return True
        if not expected_labels.issubset(current_labels):
            return True
        return _has_business_changes(existing.properties, mutation.business_properties)

    def _relationship_needs_update(
        self,
        existing: GraphRelationship,
        mutation: RelationshipMutation,
    ) -> bool:
        existing_hashes = set(_as_string_list(existing.properties.get("template_hashes")))
        if mutation.template_hash not in existing_hashes:
            return True
        if existing.rel_type != mutation.type:
            return True
        return _has_business_changes(existing.properties, mutation.business_properties)

    def _session(self) -> Any:
        if self._driver is None:
            raise RuntimeError("Neo4j driver is not connected.")
        return self._driver.session(database=self.database)


@dataclass(slots=True)
class _CypherQuery:
    text: str
    params: dict[str, Any]


def _build_node_match_query(match: NodeMatch) -> tuple[str, dict[str, Any]]:
    conditions = []
    params: dict[str, Any] = {}
    for index, (key, value) in enumerate(match.attributes.items()):
        param_name = f"attr_{index}"
        conditions.append(f"n.{_escape_identifier(key)} = ${param_name}")
        params[param_name] = value

    where_clause = " AND ".join(conditions) if conditions else "true"
    query = f"""
    MATCH (n:Entity:{_escape_identifier(match.type)})
    WHERE {where_clause}
    RETURN elementId(n) AS element_id, labels(n) AS labels, properties(n) AS properties
    """
    return query, params


def _has_business_changes(current: dict[str, Any], desired: dict[str, Any]) -> bool:
    for key, value in desired.items():
        if current.get(key) != value:
            return True
    return False


def _as_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _merge_unique(left: list[str], right: list[str]) -> list[str]:
    merged = list(left)
    for item in right:
        if item not in merged:
            merged.append(item)
    return merged


def _labels_fragment(labels: list[str]) -> str:
    unique_labels: list[str] = []
    for label in labels:
        if label not in unique_labels:
            unique_labels.append(label)
    return "".join(f":{_escape_identifier(label)}" for label in unique_labels)


def _escape_identifier(identifier: str) -> str:
    return f"`{identifier.replace('`', '``')}`"
