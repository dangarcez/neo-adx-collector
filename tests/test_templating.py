from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timezone

from neo_collector_adx.models import (
    ConditionalProperty,
    Condition,
    MatchAttributes,
    NodeSelector,
    NodeTemplate,
    RelationshipTemplate,
    RowContext,
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
            },
            job_name="failed_signins",
            collected_at=datetime(2026, 4, 16, 12, 0, tzinfo=timezone.utc),
        )

    def test_builds_node_with_conditional_property(self) -> None:
        template = NodeTemplate(
            types=["User"],
            template_hashes=["user-v1"],
            update_policy="merge",
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
            conditions=[],
        )

        mutation = self.builder.build_node(template, self.row)

        self.assertIsNotNone(mutation)
        self.assertEqual("alice@example.com", mutation.business_properties["name"])
        self.assertEqual("high", mutation.business_properties["risk"])
        self.assertEqual("auto", mutation.properties["origin"])
        self.assertIn("Entity", mutation.labels)

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
