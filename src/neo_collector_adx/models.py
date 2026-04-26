from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal


UpdatePolicy = Literal["create", "merge", "merge_at_change"]
ConditionOperator = Literal["equals", "not_equals", "greater_than", "less_than"]
PropertySourceType = Literal["static", "column"]
ConditionType = Literal["string", "number"]
TransformProcessorType = Literal["TO_UPPER", "TO_LOWER"]


@dataclass(slots=True)
class Condition:
    type: ConditionType
    column: str
    operator: ConditionOperator
    value: Any


@dataclass(slots=True)
class ConditionalProperty:
    type: PropertySourceType
    name: str
    conditions: list[Condition]
    value: Any | None = None
    from_column: str | None = None


@dataclass(slots=True)
class PropertyTransformProcessor:
    type: TransformProcessorType


@dataclass(slots=True)
class PropertyTransform:
    property: str
    process: list[PropertyTransformProcessor]


@dataclass(slots=True)
class MatchAttributes:
    static: dict[str, Any] = field(default_factory=dict)
    columns: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class NodeSelector:
    type: str
    match_attributes: MatchAttributes


@dataclass(slots=True)
class NodeTemplate:
    types: list[str]
    template_hashes: list[str]
    update_policy: UpdatePolicy = "create"
    expiration_time_min: int | None = None
    static_properties: dict[str, Any] = field(default_factory=dict)
    column_properties: dict[str, str] = field(default_factory=dict)
    conditional_properties: list[ConditionalProperty] = field(default_factory=list)
    property_transforms: list[PropertyTransform] = field(default_factory=list)
    conditions: list[Condition] = field(default_factory=list)


@dataclass(slots=True)
class RelationshipTemplate:
    type: str
    template_hash: str
    update_policy: UpdatePolicy = "create"
    expiration_time_min: int | None = None
    static_properties: dict[str, Any] = field(default_factory=dict)
    column_properties: dict[str, str] = field(default_factory=dict)
    conditional_properties: list[ConditionalProperty] = field(default_factory=list)
    property_transforms: list[PropertyTransform] = field(default_factory=list)
    conditions: list[Condition] = field(default_factory=list)
    source: NodeSelector | None = None
    target: NodeSelector | None = None


@dataclass(slots=True)
class JobConfig:
    name: str
    query: str
    interval_seconds: int
    nodes: list[NodeTemplate] = field(default_factory=list)
    relationships: list[RelationshipTemplate] = field(default_factory=list)


@dataclass(slots=True)
class RuntimeConfig:
    default_interval_seconds: int = 60
    sleep_seconds: float = 0.0
    dry_run: bool = False


@dataclass(slots=True)
class AppConfig:
    runtime: RuntimeConfig
    jobs: list[JobConfig]


@dataclass(slots=True)
class EnvironmentConfig:
    config_path: str
    log_level: str = "INFO"
    log_format: str = "text"
    uuid_namespace: str = "6f0ec5a9-4e76-4cd4-a75e-bfc8f3dbcd55"
    adx_cluster_url: str | None = None
    adx_database: str | None = None
    adx_authority_id: str | None = None
    adx_auth_mode: str = "default"
    adx_managed_identity_client_id: str | None = None
    adx_client_id: str | None = None
    adx_client_secret: str | None = None
    adx_query_timeout_seconds: int = 30
    neo4j_uri: str | None = None
    neo4j_database: str = "neo4j"
    neo4j_username: str = "neo4j"
    neo4j_password: str | None = None
    neo4j_timeout_seconds: int = 10
    neo4j_verify_connectivity: bool = True
    neo4j_apply_schema: bool = True


@dataclass(slots=True)
class RowContext:
    row: dict[str, Any]
    job_name: str
    collected_at: datetime


@dataclass(slots=True)
class NodeMutation:
    labels: list[str]
    template_hashes: list[str]
    update_policy: UpdatePolicy
    expiration_time_min: int | None
    business_properties: dict[str, Any]
    properties: dict[str, Any]
    stable_key: str


@dataclass(slots=True)
class NodeMatch:
    type: str
    attributes: dict[str, Any]


@dataclass(slots=True)
class RelationshipMutation:
    type: str
    template_hash: str
    update_policy: UpdatePolicy
    expiration_time_min: int | None
    business_properties: dict[str, Any]
    properties: dict[str, Any]
    source_match: NodeMatch
    target_match: NodeMatch
    stable_key: str


@dataclass(slots=True)
class GraphNode:
    element_id: str
    labels: list[str]
    properties: dict[str, Any]

    @property
    def node_uid(self) -> str | None:
        value = self.properties.get("node_uid")
        return str(value) if value is not None else None


@dataclass(slots=True)
class GraphRelationship:
    element_id: str
    rel_type: str
    properties: dict[str, Any]


@dataclass(slots=True)
class MutationResult:
    action: Literal["created", "updated", "skipped"]
    kind: Literal["node", "relationship"]
    identifier: str
    reason: str | None = None


@dataclass(slots=True)
class JobRunStats:
    job_name: str
    rows_processed: int = 0
    nodes_created: int = 0
    nodes_updated: int = 0
    nodes_skipped: int = 0
    relationships_created: int = 0
    relationships_updated: int = 0
    relationships_skipped: int = 0

    def record(self, result: MutationResult) -> None:
        if result.kind == "node":
            if result.action == "created":
                self.nodes_created += 1
            elif result.action == "updated":
                self.nodes_updated += 1
            else:
                self.nodes_skipped += 1
            return

        if result.action == "created":
            self.relationships_created += 1
        elif result.action == "updated":
            self.relationships_updated += 1
        else:
            self.relationships_skipped += 1
