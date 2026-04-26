from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from .exceptions import ConfigurationError
from .models import (
    AppConfig,
    ConditionalProperty,
    Condition,
    EnvironmentConfig,
    JobConfig,
    MatchAttributes,
    NodeSelector,
    NodeTemplate,
    PropertyTransform,
    PropertyTransformProcessor,
    RelationshipTemplate,
    RuntimeConfig,
)

UPDATE_POLICY_ALIASES = {
    "create": "create",
    "merge": "merge",
    "merge_at_change": "merge_at_change",
    "mergeAtChange": "merge_at_change",
    "merge-at-change": "merge_at_change",
}
NUMBER_OPERATORS = ("equals", "not_equals", "greater_than", "less_than")
STRING_OPERATORS = ("equals", "not_equals")


def load_environment() -> EnvironmentConfig:
    return EnvironmentConfig(
        config_path=os.getenv("APP_CONFIG_PATH", "config.demo.yaml"),
        log_level=os.getenv("APP_LOG_LEVEL", "INFO"),
        log_format=os.getenv("APP_LOG_FORMAT", "text"),
        uuid_namespace=os.getenv(
            "APP_UUID_NAMESPACE",
            "6f0ec5a9-4e76-4cd4-a75e-bfc8f3dbcd55",
        ),
        adx_cluster_url=_optional_env("ADX_CLUSTER_URL"),
        adx_database=_optional_env("ADX_DATABASE"),
        adx_authority_id=_optional_env("ADX_AUTHORITY_ID"),
        adx_auth_mode=os.getenv("ADX_AUTH_MODE", "default"),
        adx_managed_identity_client_id=_optional_env("ADX_MANAGED_IDENTITY_CLIENT_ID"),
        adx_client_id=_optional_env("ADX_CLIENT_ID"),
        adx_client_secret=_optional_env("ADX_CLIENT_SECRET"),
        adx_query_timeout_seconds=_parse_int_env("ADX_QUERY_TIMEOUT_SECONDS", 30),
        neo4j_uri=_optional_env("NEO4J_URI"),
        neo4j_database=os.getenv("NEO4J_DATABASE", "neo4j"),
        neo4j_username=os.getenv("NEO4J_USERNAME", "neo4j"),
        neo4j_password=_optional_env("NEO4J_PASSWORD"),
        neo4j_timeout_seconds=_parse_int_env("NEO4J_TIMEOUT_SECONDS", 10),
        neo4j_verify_connectivity=_parse_bool_env("NEO4J_VERIFY_CONNECTIVITY", True),
        neo4j_apply_schema=_parse_bool_env("NEO4J_APPLY_SCHEMA", True),
    )


def load_app_config(path: str) -> AppConfig:
    config_path = Path(path)
    if not config_path.exists():
        raise ConfigurationError(f"YAML configuration not found: {config_path}")

    raw_config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw_config, dict):
        raise ConfigurationError("Configuration root must be a mapping.")

    runtime = _parse_runtime(raw_config.get("runtime") or {})
    jobs_data = raw_config.get("jobs")
    if not isinstance(jobs_data, list) or not jobs_data:
        raise ConfigurationError("Configuration must define at least one job in 'jobs'.")

    jobs = [_parse_job(item, runtime) for item in jobs_data]
    return AppConfig(runtime=runtime, jobs=jobs)


def _parse_runtime(data: dict[str, Any]) -> RuntimeConfig:
    if not isinstance(data, dict):
        raise ConfigurationError("'runtime' must be an object.")

    default_interval_seconds = _as_positive_int(
        data.get("default_interval_seconds", 60),
        "runtime.default_interval_seconds",
    )
    sleep_seconds = _as_non_negative_float(
        data.get("sleep_seconds", 0),
        "runtime.sleep_seconds",
    )
    dry_run = _as_bool(data.get("dry_run", False), "runtime.dry_run")
    return RuntimeConfig(
        default_interval_seconds=default_interval_seconds,
        sleep_seconds=sleep_seconds,
        dry_run=dry_run,
    )


def _parse_job(data: Any, runtime: RuntimeConfig) -> JobConfig:
    if not isinstance(data, dict):
        raise ConfigurationError("Each job must be an object.")

    name = _require_non_empty_string(data.get("name"), "jobs[].name")
    query = _require_non_empty_string(data.get("query"), f"jobs[{name}].query")
    interval_value = data.get("interval_seconds", runtime.default_interval_seconds)
    interval_seconds = _as_positive_int(interval_value, f"jobs[{name}].interval_seconds")

    nodes_data = data.get("nodes") or []
    relationships_data = data.get("relationships") or []
    if not isinstance(nodes_data, list):
        raise ConfigurationError(f"jobs[{name}].nodes must be a list.")
    if not isinstance(relationships_data, list):
        raise ConfigurationError(f"jobs[{name}].relationships must be a list.")

    nodes = [_parse_node_template(item, name, index) for index, item in enumerate(nodes_data)]
    relationships = [
        _parse_relationship_template(item, name, index)
        for index, item in enumerate(relationships_data)
    ]

    return JobConfig(
        name=name,
        query=query,
        interval_seconds=interval_seconds,
        nodes=nodes,
        relationships=relationships,
    )


def _parse_node_template(data: Any, job_name: str, index: int) -> NodeTemplate:
    ctx = f"jobs[{job_name}].nodes[{index}]"
    if not isinstance(data, dict):
        raise ConfigurationError(f"{ctx} must be an object.")

    types = _parse_types(data, ctx)
    template_hashes = _parse_string_list(
        data.get("template_hashes"),
        f"{ctx}.template_hashes",
        min_items=1,
    )
    update_policy = _normalize_update_policy(data.get("update_policy"), ctx)
    expiration_time_min = _parse_optional_positive_int(
        data.get("expiration_time_min"),
        f"{ctx}.expiration_time_min",
    )
    static_properties = _parse_mapping(data.get("static_properties"), f"{ctx}.static_properties")
    column_properties = _parse_column_mapping(
        data.get("column_properties", data.get("dynamic_properties")),
        f"{ctx}.column_properties",
    )
    conditional_properties = _parse_conditional_properties(
        data.get("conditional_properties") or [],
        f"{ctx}.conditional_properties",
    )
    property_transforms = _parse_property_transforms(
        data.get("property_transforms") or [],
        f"{ctx}.property_transforms",
    )
    conditions = _parse_conditions(data.get("conditions") or [], f"{ctx}.conditions")

    if "name" not in static_properties and "name" not in column_properties:
        raise ConfigurationError(f"{ctx} must define 'name' in static_properties or column_properties.")

    return NodeTemplate(
        types=types,
        template_hashes=template_hashes,
        update_policy=update_policy,
        expiration_time_min=expiration_time_min,
        static_properties=static_properties,
        column_properties=column_properties,
        conditional_properties=conditional_properties,
        property_transforms=property_transforms,
        conditions=conditions,
    )


def _parse_relationship_template(data: Any, job_name: str, index: int) -> RelationshipTemplate:
    ctx = f"jobs[{job_name}].relationships[{index}]"
    if not isinstance(data, dict):
        raise ConfigurationError(f"{ctx} must be an object.")

    rel_type = _require_non_empty_string(data.get("type"), f"{ctx}.type")
    template_hash = _parse_relationship_hash(data, ctx)
    update_policy = _normalize_update_policy(data.get("update_policy"), ctx)
    expiration_time_min = _parse_optional_positive_int(
        data.get("expiration_time_min"),
        f"{ctx}.expiration_time_min",
    )
    static_properties = _parse_mapping(data.get("static_properties"), f"{ctx}.static_properties")
    column_properties = _parse_column_mapping(
        data.get("column_properties", data.get("dynamic_properties")),
        f"{ctx}.column_properties",
    )
    conditional_properties = _parse_conditional_properties(
        data.get("conditional_properties") or [],
        f"{ctx}.conditional_properties",
    )
    property_transforms = _parse_property_transforms(
        data.get("property_transforms") or [],
        f"{ctx}.property_transforms",
    )
    conditions = _parse_conditions(data.get("conditions") or [], f"{ctx}.conditions")
    source = _parse_selector(data.get("source"), f"{ctx}.source")
    target = _parse_selector(data.get("target"), f"{ctx}.target")

    return RelationshipTemplate(
        type=rel_type,
        template_hash=template_hash,
        update_policy=update_policy,
        expiration_time_min=expiration_time_min,
        static_properties=static_properties,
        column_properties=column_properties,
        conditional_properties=conditional_properties,
        property_transforms=property_transforms,
        conditions=conditions,
        source=source,
        target=target,
    )


def _parse_selector(data: Any, ctx: str) -> NodeSelector:
    if not isinstance(data, dict):
        raise ConfigurationError(f"{ctx} must be an object.")

    selector_type = _require_non_empty_string(data.get("type"), f"{ctx}.type")
    match_data = data.get("match_attributes")
    if match_data is None:
        match_data = {
            "static": data.get("match_static_attributes"),
            "columns": data.get("match_column_attributes"),
        }
    if not isinstance(match_data, dict):
        raise ConfigurationError(f"{ctx}.match_attributes must be an object.")

    static_attributes = _parse_mapping(match_data.get("static"), f"{ctx}.match_attributes.static")
    column_attributes = _parse_column_mapping(
        match_data.get("columns"),
        f"{ctx}.match_attributes.columns",
    )
    if not static_attributes and not column_attributes:
        raise ConfigurationError(f"{ctx} must define at least one match attribute.")

    return NodeSelector(
        type=selector_type,
        match_attributes=MatchAttributes(
            static=static_attributes,
            columns=column_attributes,
        ),
    )


def _parse_conditional_properties(data: Any, ctx: str) -> list[ConditionalProperty]:
    if not isinstance(data, list):
        raise ConfigurationError(f"{ctx} must be a list.")

    output: list[ConditionalProperty] = []
    for index, item in enumerate(data):
        item_ctx = f"{ctx}[{index}]"
        if not isinstance(item, dict):
            raise ConfigurationError(f"{item_ctx} must be an object.")

        prop_type = _require_non_empty_string(item.get("type"), f"{item_ctx}.type")
        if prop_type not in {"static", "column"}:
            raise ConfigurationError(f"{item_ctx}.type must be 'static' or 'column'.")

        name = _require_non_empty_string(item.get("name"), f"{item_ctx}.name")
        conditions = _parse_conditions(item.get("conditions") or [], f"{item_ctx}.conditions")
        if not conditions:
            raise ConfigurationError(f"{item_ctx}.conditions must define at least one condition.")

        if prop_type == "static":
            if "value" not in item:
                raise ConfigurationError(f"{item_ctx}.value is required for static conditional properties.")
            output.append(
                ConditionalProperty(
                    type="static",
                    name=name,
                    value=item.get("value"),
                    conditions=conditions,
                )
            )
            continue

        from_column = _require_non_empty_string(
            item.get("from_column", item.get("column")),
            f"{item_ctx}.from_column",
        )
        output.append(
            ConditionalProperty(
                type="column",
                name=name,
                from_column=from_column,
                conditions=conditions,
            )
        )

    return output


def _parse_property_transforms(data: Any, ctx: str) -> list[PropertyTransform]:
    if not isinstance(data, list):
        raise ConfigurationError(f"{ctx} must be a list.")

    transforms: list[PropertyTransform] = []
    for index, item in enumerate(data):
        item_ctx = f"{ctx}[{index}]"
        if not isinstance(item, dict):
            raise ConfigurationError(f"{item_ctx} must be an object.")

        property_name = _require_non_empty_string(item.get("property"), f"{item_ctx}.property")
        process_data = item.get("process")
        if not isinstance(process_data, list) or not process_data:
            raise ConfigurationError(f"{item_ctx}.process must be a non-empty list.")

        processors: list[PropertyTransformProcessor] = []
        for process_index, process_item in enumerate(process_data):
            process_ctx = f"{item_ctx}.process[{process_index}]"
            if not isinstance(process_item, dict):
                raise ConfigurationError(f"{process_ctx} must be an object.")

            process_type = _require_non_empty_string(process_item.get("type"), f"{process_ctx}.type").upper()
            if process_type not in {"TO_UPPER", "TO_LOWER"}:
                raise ConfigurationError(f"{process_ctx}.type must be TO_UPPER or TO_LOWER.")

            processors.append(PropertyTransformProcessor(type=process_type))

        transforms.append(PropertyTransform(property=property_name, process=processors))

    return transforms


def _parse_conditions(data: Any, ctx: str) -> list[Condition]:
    if not isinstance(data, list):
        raise ConfigurationError(f"{ctx} must be a list.")

    conditions: list[Condition] = []
    for index, item in enumerate(data):
        item_ctx = f"{ctx}[{index}]"
        if not isinstance(item, dict):
            raise ConfigurationError(f"{item_ctx} must be an object.")

        condition_type = _require_non_empty_string(item.get("type"), f"{item_ctx}.type")
        if condition_type not in {"string", "number"}:
            raise ConfigurationError(f"{item_ctx}.type must be 'string' or 'number'.")

        column = _require_non_empty_string(item.get("column"), f"{item_ctx}.column")
        operators = STRING_OPERATORS if condition_type == "string" else NUMBER_OPERATORS
        chosen = [operator for operator in operators if operator in item]
        if len(chosen) != 1:
            raise ConfigurationError(f"{item_ctx} must define exactly one comparison operator.")

        operator = chosen[0]
        conditions.append(
            Condition(
                type="string" if condition_type == "string" else "number",
                column=column,
                operator=operator,  # type: ignore[arg-type]
                value=item[operator],
            )
        )

    return conditions


def _parse_types(data: dict[str, Any], ctx: str) -> list[str]:
    raw_types = data.get("types")
    if raw_types is None and data.get("type") is not None:
        raw_types = [data.get("type")]
    return _parse_string_list(raw_types, f"{ctx}.types", min_items=1)


def _parse_relationship_hash(data: dict[str, Any], ctx: str) -> str:
    if data.get("template_hash") is not None:
        return _require_non_empty_string(data.get("template_hash"), f"{ctx}.template_hash")

    hashes = _parse_string_list(
        data.get("template_hashes"),
        f"{ctx}.template_hashes",
        min_items=1,
    )
    if len(hashes) != 1:
        raise ConfigurationError(f"{ctx}.template_hashes can only contain one item for relationships.")
    return hashes[0]


def _normalize_update_policy(value: Any, ctx: str) -> str:
    normalized = UPDATE_POLICY_ALIASES.get(str(value or "create"))
    if normalized is None:
        raise ConfigurationError(f"{ctx}.update_policy must be create, merge or merge_at_change.")
    return normalized


def _parse_string_list(value: Any, ctx: str, *, min_items: int = 0) -> list[str]:
    if not isinstance(value, list):
        raise ConfigurationError(f"{ctx} must be a list.")

    items = [_require_non_empty_string(item, ctx) for item in value]
    if len(items) < min_items:
        raise ConfigurationError(f"{ctx} must contain at least {min_items} item(s).")
    return items


def _parse_mapping(value: Any, ctx: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigurationError(f"{ctx} must be an object.")

    output: dict[str, Any] = {}
    for key, item_value in value.items():
        key_str = _require_non_empty_string(key, ctx)
        output[key_str] = item_value
    return output


def _parse_column_mapping(value: Any, ctx: str) -> dict[str, str]:
    mapping = _parse_mapping(value, ctx)
    output: dict[str, str] = {}
    for key, item_value in mapping.items():
        output[key] = _require_non_empty_string(item_value, f"{ctx}.{key}")
    return output


def _require_non_empty_string(value: Any, ctx: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{ctx} must be a non-empty string.")
    return value.strip()


def _as_positive_int(value: Any, ctx: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"{ctx} must be an integer.") from exc
    if parsed <= 0:
        raise ConfigurationError(f"{ctx} must be greater than zero.")
    return parsed


def _parse_optional_positive_int(value: Any, ctx: str) -> int | None:
    if value is None:
        return None
    return _as_positive_int(value, ctx)


def _as_non_negative_float(value: Any, ctx: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"{ctx} must be a number.") from exc
    if parsed < 0:
        raise ConfigurationError(f"{ctx} must be greater than or equal to zero.")
    return parsed


def _as_bool(value: Any, ctx: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
    raise ConfigurationError(f"{ctx} must be a boolean.")


def _optional_env(name: str) -> str | None:
    value = os.getenv(name)
    if value is None or not value.strip():
        return None
    return value.strip()


def _parse_int_env(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None or not raw_value.strip():
        return default
    try:
        return int(raw_value)
    except ValueError as exc:
        raise ConfigurationError(f"Environment variable {name} must be an integer.") from exc


def _parse_bool_env(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None or not raw_value.strip():
        return default
    return _as_bool(raw_value, name)
