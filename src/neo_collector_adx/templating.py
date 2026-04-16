from __future__ import annotations

import json
import uuid
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Any

from .models import (
    ConditionalProperty,
    Condition,
    NodeMatch,
    NodeMutation,
    RelationshipMutation,
    RelationshipTemplate,
    RowContext,
    NodeTemplate,
)


class MutationBuilder:
    def __init__(self, namespace: uuid.UUID):
        self.namespace = namespace

    def build_node(self, template: NodeTemplate, row: RowContext) -> NodeMutation | None:
        if not self._conditions_pass(template.conditions, row):
            return None

        business_properties = self._resolve_properties(
            static_properties=template.static_properties,
            column_properties=template.column_properties,
            conditional_properties=template.conditional_properties,
            row=row,
        )
        name = business_properties.get("name")
        if name is None or str(name).strip() == "":
            return None

        stable_key = self._build_node_stable_key(template.types, template.template_hashes, name)
        properties = dict(business_properties)
        now = row.collected_at.astimezone(timezone.utc).isoformat()
        properties.update(
            {
                "node_uid": str(uuid.uuid5(self.namespace, stable_key)),
                "origin": "auto",
                "template_hashes": template.template_hashes,
                "created_at": now,
                "updated_at": now,
            }
        )
        labels = ["Entity", *template.types]
        return NodeMutation(
            labels=labels,
            template_hashes=template.template_hashes,
            update_policy=template.update_policy,
            business_properties=business_properties,
            properties=properties,
            stable_key=stable_key,
        )

    def build_relationship(
        self,
        template: RelationshipTemplate,
        row: RowContext,
    ) -> RelationshipMutation | None:
        if not self._conditions_pass(template.conditions, row):
            return None

        business_properties = self._resolve_properties(
            static_properties=template.static_properties,
            column_properties=template.column_properties,
            conditional_properties=template.conditional_properties,
            row=row,
        )
        source_match = self._resolve_selector_match(template.source.type, template.source.match_attributes.static, template.source.match_attributes.columns, row)
        target_match = self._resolve_selector_match(template.target.type, template.target.match_attributes.static, template.target.match_attributes.columns, row)
        if source_match is None or target_match is None:
            return None

        source_key = json.dumps(source_match.attributes, sort_keys=True, default=str)
        target_key = json.dumps(target_match.attributes, sort_keys=True, default=str)
        stable_key = f"relationship|{template.template_hash}|{template.type}|{source_match.type}|{source_key}|{target_match.type}|{target_key}"
        properties = dict(business_properties)
        now = row.collected_at.astimezone(timezone.utc).isoformat()
        properties.update(
            {
                "rel_uid": str(uuid.uuid5(self.namespace, stable_key)),
                "origin": "auto",
                "template_hashes": [template.template_hash],
                "created_at": now,
                "updated_at": now,
            }
        )
        return RelationshipMutation(
            type=template.type,
            template_hash=template.template_hash,
            update_policy=template.update_policy,
            business_properties=business_properties,
            properties=properties,
            source_match=source_match,
            target_match=target_match,
            stable_key=stable_key,
        )

    def _resolve_selector_match(
        self,
        node_type: str,
        static_attributes: dict[str, Any],
        column_attributes: dict[str, str],
        row: RowContext,
    ) -> NodeMatch | None:
        attributes = {key: self._normalize_value(value) for key, value in static_attributes.items()}
        for key, column in column_attributes.items():
            if column not in row.row:
                return None
            attributes[key] = self._normalize_value(row.row[column])
        return NodeMatch(type=node_type, attributes=attributes)

    def _resolve_properties(
        self,
        *,
        static_properties: dict[str, Any],
        column_properties: dict[str, str],
        conditional_properties: list[ConditionalProperty],
        row: RowContext,
    ) -> dict[str, Any]:
        properties = {
            key: self._normalize_value(value)
            for key, value in static_properties.items()
        }
        for key, column in column_properties.items():
            if column in row.row:
                properties[key] = self._normalize_value(row.row[column])

        for item in conditional_properties:
            if not self._conditions_pass(item.conditions, row):
                continue
            if item.type == "static":
                properties[item.name] = self._normalize_value(item.value)
                continue
            if item.from_column in row.row:
                properties[item.name] = self._normalize_value(row.row[item.from_column])
        return properties

    def _conditions_pass(self, conditions: list[Condition], row: RowContext) -> bool:
        return all(self._condition_passes(condition, row) for condition in conditions)

    def _condition_passes(self, condition: Condition, row: RowContext) -> bool:
        if condition.column not in row.row:
            return False
        value = row.row[condition.column]
        if condition.type == "string":
            left = "" if value is None else str(value)
            right = "" if condition.value is None else str(condition.value)
            return _compare_values(left, right, condition.operator)

        left_number = _to_number(value)
        right_number = _to_number(condition.value)
        if left_number is None or right_number is None:
            return False
        return _compare_values(left_number, right_number, condition.operator)

    def _normalize_value(self, value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, Decimal):
            integral = value.to_integral_value()
            return int(value) if integral == value else float(value)
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc).isoformat()
        if isinstance(value, (date, time)):
            return value.isoformat()
        if isinstance(value, uuid.UUID):
            return str(value)
        if isinstance(value, list):
            normalized_items = [self._normalize_value(item) for item in value]
            if all(isinstance(item, (str, int, float, bool)) or item is None for item in normalized_items):
                return normalized_items
            return json.dumps(normalized_items, sort_keys=True, ensure_ascii=True, default=str)
        if isinstance(value, dict):
            normalized_dict = {
                str(key): self._normalize_value(item)
                for key, item in value.items()
            }
            return json.dumps(normalized_dict, sort_keys=True, ensure_ascii=True, default=str)
        return str(value)

    @staticmethod
    def _build_node_stable_key(types: list[str], template_hashes: list[str], name: Any) -> str:
        types_key = "|".join(sorted(types))
        hashes_key = "|".join(sorted(template_hashes))
        return f"node|{types_key}|{hashes_key}|{name}"


def _compare_values(left: Any, right: Any, operator: str) -> bool:
    if operator == "equals":
        return left == right
    if operator == "not_equals":
        return left != right
    if operator == "greater_than":
        return left > right
    if operator == "less_than":
        return left < right
    raise ValueError(f"Unsupported operator: {operator}")


def _to_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None
