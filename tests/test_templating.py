from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timezone

from neo_collector_adx.models import (
    ConditionalProperty,
    Condition,
    GraphRelationship,
    MatchAttributes,
    NodeSelector,
    NodeTemplate,
    PropertyTransform,
    PropertyTransformProcessor,
    RelationshipTemplate,
    RowContext,
)
from neo_collector_adx.neo4j_client import (
    DryRunGraphRepository,
    Neo4jGraphRepository,
    _expires_at_value,
    _resolve_relationship_expires_at,
)
from neo_collector_adx.templating import MutationBuilder


class MutationBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.builder = MutationBuilder(uuid.UUID("6f0ec5a9-4e76-4cd4-a75e-bfc8f3dbcd55"))
        self.row = RowContext(
            row={
                "UserPrincipalName": "alice@example.com",
                "IPAddress": "10.0.0.10",
                "FailedAttempts": 6,
                "Country": "BR",
                "ResourceName": "cpu_vru",
            },
            job_name="failed_signins",
            collected_at=datetime(2026, 4, 16, 12, 0, tzinfo=timezone.utc),
        )

    def test_builds_node_with_conditional_property(self) -> None:
        template = NodeTemplate(
            types=["User"],
            template_hashes=["user-v1"],
            update_policy="merge",
            expiration_time_min=15,
            static_properties={"source_system": "adx"},
            column_properties={"name": "UserPrincipalName"},
            conditional_properties=[
                ConditionalProperty(
                    type="static",
                    name="risk",
                    value="high",
                    conditions=[
                        Condition(
                            type="number",
                            column="FailedAttempts",
                            operator="greater_than",
                            value=5,
                        )
                    ],
                )
            ],
            property_transforms=[
                PropertyTransform(
                    property="name",
                    process=[PropertyTransformProcessor(type="TO_UPPER")],
                )
            ],
            conditions=[],
        )

        mutation = self.builder.build_node(template, self.row)

        self.assertIsNotNone(mutation)
        self.assertEqual("ALICE@EXAMPLE.COM", mutation.business_properties["name"])
        self.assertEqual("high", mutation.business_properties["risk"])
        self.assertEqual(15, mutation.expiration_time_min)
        self.assertEqual("auto", mutation.properties["origin"])
        self.assertIn("Entity", mutation.labels)

    def test_property_transform_ignores_non_string_values(self) -> None:
        template = NodeTemplate(
            types=["User"],
            template_hashes=["user-v1"],
            update_policy="merge",
            static_properties={"risk_score": 10},
            column_properties={"name": "UserPrincipalName"},
            conditional_properties=[],
            property_transforms=[
                PropertyTransform(
                    property="risk_score",
                    process=[PropertyTransformProcessor(type="TO_UPPER")],
                )
            ],
            conditions=[],
        )

        mutation = self.builder.build_node(template, self.row)

        self.assertIsNotNone(mutation)
        self.assertEqual(10, mutation.business_properties["risk_score"])

    def test_regex_property_transform_uses_capture_groups(self) -> None:
        template = NodeTemplate(
            types=["Resource"],
            template_hashes=["resource-v1"],
            update_policy="merge",
            static_properties={},
            column_properties={"name": "ResourceName"},
            conditional_properties=[],
            property_transforms=[
                PropertyTransform(
                    property="name",
                    process=[
                        PropertyTransformProcessor(
                            type="REGEX",
                            pattern=r"(\w+)_(\w+)",
                            output="$1_and_$2",
                        )
                    ],
                )
            ],
            conditions=[],
        )

        mutation = self.builder.build_node(template, self.row)

        self.assertIsNotNone(mutation)
        self.assertEqual("cpu_and_vru", mutation.business_properties["name"])

    def test_regex_property_transform_preserves_value_without_match(self) -> None:
        template = NodeTemplate(
            types=["Resource"],
            template_hashes=["resource-v1"],
            update_policy="merge",
            static_properties={},
            column_properties={"name": "ResourceName"},
            conditional_properties=[],
            property_transforms=[
                PropertyTransform(
                    property="name",
                    process=[
                        PropertyTransformProcessor(
                            type="REGEX",
                            pattern=r"^(\d+)$",
                            output="$1",
                        )
                    ],
                )
            ],
            conditions=[],
        )

        mutation = self.builder.build_node(template, self.row)

        self.assertIsNotNone(mutation)
        self.assertEqual("cpu_vru", mutation.business_properties["name"])

    def test_skips_node_when_name_resolves_to_missing_column(self) -> None:
        template = NodeTemplate(
            types=["User"],
            template_hashes=["user-v1"],
            update_policy="create",
            static_properties={},
            column_properties={"name": "MissingColumn"},
            conditional_properties=[],
            conditions=[],
        )

        mutation = self.builder.build_node(template, self.row)

        self.assertIsNone(mutation)

    def test_builds_relationship_with_column_matchers(self) -> None:
        template = RelationshipTemplate(
            type="AUTHENTICATED_FROM",
            template_hash="user-ip-v1",
            update_policy="merge",
            static_properties={"source_system": "adx"},
            column_properties={"country": "Country"},
            conditional_properties=[],
            conditions=[
                Condition(
                    type="number",
                    column="FailedAttempts",
                    operator="greater_than",
                    value=1,
                )
            ],
            source=NodeSelector(
                type="User",
                match_attributes=MatchAttributes(columns={"name": "UserPrincipalName"}),
            ),
            target=NodeSelector(
                type="IPAddress",
                match_attributes=MatchAttributes(columns={"name": "IPAddress"}),
            ),
        )

        mutation = self.builder.build_relationship(template, self.row)

        self.assertIsNotNone(mutation)
        self.assertEqual("AUTHENTICATED_FROM", mutation.type)
        self.assertEqual("alice@example.com", mutation.source_match.attributes["name"])
        self.assertEqual("10.0.0.10", mutation.target_match.attributes["name"])
        self.assertEqual("BR", mutation.business_properties["country"])
        self.assertEqual("user-ip-v1", mutation.properties["template_hash"])
        self.assertNotIn("template_hashes", mutation.properties)

    def test_relationship_update_comparison_uses_template_hash_property(self) -> None:
        template = RelationshipTemplate(
            type="AUTHENTICATED_FROM",
            template_hash="user-ip-v1",
            update_policy="merge_at_change",
            static_properties={},
            column_properties={"country": "Country"},
            conditional_properties=[],
            conditions=[],
            source=NodeSelector(
                type="User",
                match_attributes=MatchAttributes(columns={"name": "UserPrincipalName"}),
            ),
            target=NodeSelector(
                type="IPAddress",
                match_attributes=MatchAttributes(columns={"name": "IPAddress"}),
            ),
        )
        mutation = self.builder.build_relationship(template, self.row)
        repository = object.__new__(Neo4jGraphRepository)

        current = GraphRelationship(
            element_id="rel-1",
            rel_type="AUTHENTICATED_FROM",
            properties={"template_hash": "user-ip-v1", "country": "BR"},
        )

        self.assertIsNotNone(mutation)
        self.assertFalse(repository._relationship_needs_update(current, mutation))

    def test_skips_relationship_when_selector_column_is_missing(self) -> None:
        template = RelationshipTemplate(
            type="AUTHENTICATED_FROM",
            template_hash="user-ip-v1",
            update_policy="merge",
            static_properties={},
            column_properties={},
            conditional_properties=[],
            conditions=[],
            source=NodeSelector(
                type="User",
                match_attributes=MatchAttributes(columns={"name": "UnknownColumn"}),
            ),
            target=NodeSelector(
                type="IPAddress",
                match_attributes=MatchAttributes(columns={"name": "IPAddress"}),
            ),
        )

        mutation = self.builder.build_relationship(template, self.row)

        self.assertIsNone(mutation)

    def test_dry_run_repository_does_not_use_reserved_logrecord_keys(self) -> None:
        template = NodeTemplate(
            types=["User"],
            template_hashes=["user-v1"],
            update_policy="create",
            static_properties={},
            column_properties={"name": "UserPrincipalName"},
            conditional_properties=[],
            conditions=[],
        )

        mutation = self.builder.build_node(template, self.row)
        repository = DryRunGraphRepository()

        result = repository.upsert_node(mutation)

        self.assertEqual("skipped", result.action)
        self.assertEqual("node", result.kind)

    def test_expires_at_helper_generates_future_timestamp(self) -> None:
        expires_at = _expires_at_value(
            30,
            now=datetime(2026, 4, 21, 12, 0, tzinfo=timezone.utc),
        )

        self.assertEqual("2026-04-21T12:30:00+00:00", expires_at)

    def test_relationship_expiration_is_preserved_for_merge_at_change(self) -> None:
        template = RelationshipTemplate(
            type="AUTHENTICATED_FROM",
            template_hash="user-ip-v1",
            update_policy="merge_at_change",
            expiration_time_min=15,
            static_properties={},
            column_properties={"country": "Country"},
            conditional_properties=[],
            property_transforms=[],
            conditions=[],
            source=NodeSelector(
                type="User",
                match_attributes=MatchAttributes(columns={"name": "UserPrincipalName"}),
            ),
            target=NodeSelector(
                type="IPAddress",
                match_attributes=MatchAttributes(columns={"name": "IPAddress"}),
            ),
        )
        mutation = self.builder.build_relationship(template, self.row)

        class ExistingRelationship:
            properties = {"expires_at": "2026-04-21T13:00:00+00:00"}

        self.assertEqual(
            "2026-04-21T13:00:00+00:00",
            _resolve_relationship_expires_at(ExistingRelationship(), mutation),
        )
