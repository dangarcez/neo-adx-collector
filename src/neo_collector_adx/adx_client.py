from __future__ import annotations

import logging
from typing import Any

from .exceptions import ConfigurationError
from .models import EnvironmentConfig

LOGGER = logging.getLogger(__name__)
KUSTO_SCOPE = "https://kusto.kusto.windows.net/.default"


class ADXQueryClient:
    def __init__(self, env: EnvironmentConfig):
        if not env.adx_cluster_url:
            raise ConfigurationError("ADX_CLUSTER_URL is required.")
        if not env.adx_database:
            raise ConfigurationError("ADX_DATABASE is required.")

        self.env = env
        self.database = env.adx_database
        self._client = self._build_client()

    def query(self, query: str) -> list[dict[str, Any]]:
        LOGGER.info("adx_query_started", extra={"database": self.database})
        response = self._client.execute(self.database, query)
        rows = _rows_from_response(response)
        LOGGER.info("adx_query_completed", extra={"row_count": len(rows)})
        return rows

    def close(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            close()

    def _build_client(self) -> Any:
        try:
            from azure.kusto.data import KustoClient, KustoConnectionStringBuilder
        except ImportError as exc:
            raise ConfigurationError(
                "Missing dependency 'azure-kusto-data'. Install project dependencies before running."
            ) from exc

        connection_builder = self._build_connection_string(KustoConnectionStringBuilder)
        return KustoClient(connection_builder)

    def _build_connection_string(self, builder_type: Any) -> Any:
        cluster_url = self.env.adx_cluster_url
        auth_mode = self.env.adx_auth_mode.lower()

        if auth_mode == "application_key":
            return self._build_application_key_connection(builder_type, cluster_url)
        if auth_mode == "managed_identity":
            return self._build_managed_identity_connection(builder_type, cluster_url)
        if auth_mode == "az_cli":
            return self._build_az_cli_connection(builder_type, cluster_url)
        if auth_mode != "default":
            raise ConfigurationError(
                "ADX_AUTH_MODE must be one of default, managed_identity, application_key or az_cli."
            )

        default_connection = self._build_default_connection(builder_type, cluster_url)
        if default_connection is not None:
            return default_connection

        if self.env.adx_client_id and self.env.adx_client_secret:
            return self._build_application_key_connection(builder_type, cluster_url)
        if hasattr(builder_type, "with_aad_managed_service_identity_authentication"):
            return self._build_managed_identity_connection(builder_type, cluster_url)
        return self._build_az_cli_connection(builder_type, cluster_url)

    def _build_default_connection(self, builder_type: Any, cluster_url: str) -> Any | None:
        try:
            from azure.identity import DefaultAzureCredential
        except ImportError:
            LOGGER.warning(
                "default_azure_credential_unavailable",
                extra={"reason": "azure-identity dependency missing"},
            )
            return None

        credential = DefaultAzureCredential(
            managed_identity_client_id=self.env.adx_managed_identity_client_id,
            exclude_interactive_browser_credential=True,
        )

        candidate_methods = (
            ("with_azure_token_credential", (cluster_url, credential)),
            ("with_token_credential", (cluster_url, credential)),
            ("with_token_provider", (cluster_url, lambda: credential.get_token(KUSTO_SCOPE).token)),
        )
        for method_name, args in candidate_methods:
            method = getattr(builder_type, method_name, None)
            if callable(method):
                return method(*args)

        LOGGER.warning(
            "default_azure_credential_fallback",
            extra={"reason": "kusto sdk does not expose token credential helpers"},
        )
        return None

    def _build_application_key_connection(self, builder_type: Any, cluster_url: str) -> Any:
        if not self.env.adx_client_id or not self.env.adx_client_secret:
            raise ConfigurationError(
                "ADX_CLIENT_ID and ADX_CLIENT_SECRET are required for ADX_AUTH_MODE=application_key."
            )
        method = getattr(builder_type, "with_aad_application_key_authentication", None)
        if not callable(method):
            raise ConfigurationError("Installed azure-kusto-data does not support application key authentication.")
        authority_id = self.env.adx_authority_id or "common"
        return method(
            cluster_url,
            self.env.adx_client_id,
            self.env.adx_client_secret,
            authority_id,
        )

    def _build_managed_identity_connection(self, builder_type: Any, cluster_url: str) -> Any:
        method = getattr(builder_type, "with_aad_managed_service_identity_authentication", None)
        if not callable(method):
            raise ConfigurationError("Installed azure-kusto-data does not support managed identity authentication.")
        client_id = self.env.adx_managed_identity_client_id
        if client_id:
            return method(cluster_url, client_id=client_id)
        return method(cluster_url)

    def _build_az_cli_connection(self, builder_type: Any, cluster_url: str) -> Any:
        method = getattr(builder_type, "with_az_cli_authentication", None)
        if not callable(method):
            raise ConfigurationError("Installed azure-kusto-data does not support Azure CLI authentication.")
        return method(cluster_url)


def _rows_from_response(response: Any) -> list[dict[str, Any]]:
    tables = getattr(response, "primary_results", None) or getattr(response, "tables", None)
    if not tables:
        return []

    table = tables[0]
    columns = [getattr(column, "column_name", getattr(column, "name", str(index))) for index, column in enumerate(getattr(table, "columns", []))]
    rows: list[dict[str, Any]] = []
    for row in table:
        item: dict[str, Any] = {}
        for index, column_name in enumerate(columns):
            try:
                item[column_name] = row[column_name]
            except Exception:
                item[column_name] = row[index]
        rows.append(item)
    return rows
