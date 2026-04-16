from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone

from .adx_client import ADXQueryClient
from .config import load_app_config
from .exceptions import ConfigurationError
from .models import AppConfig, EnvironmentConfig, JobConfig, JobRunStats, RowContext
from .neo4j_client import DryRunGraphRepository, Neo4jGraphRepository
from .templating import MutationBuilder

LOGGER = logging.getLogger(__name__)


class CollectorApplication:
    def __init__(self, env: EnvironmentConfig, config: AppConfig):
        self.env = env
        self.config = config
        try:
            namespace = uuid.UUID(env.uuid_namespace)
        except ValueError as exc:
            raise ConfigurationError("APP_UUID_NAMESPACE must be a valid UUID.") from exc

        self.builder = MutationBuilder(namespace)
        self.adx_client: ADXQueryClient | None = None
        self.graph_repository = None

    @classmethod
    def from_environment(cls, env: EnvironmentConfig, config_path: str | None = None) -> CollectorApplication:
        return cls(env=env, config=load_app_config(config_path or env.config_path))

    def bootstrap(self) -> None:
        self.adx_client = ADXQueryClient(self.env)
        if self.config.runtime.dry_run:
            self.graph_repository = DryRunGraphRepository()
        else:
            relationship_types = [
                relationship.type
                for job in self.config.jobs
                for relationship in job.relationships
            ]
            self.graph_repository = Neo4jGraphRepository(
                uri=self.env.neo4j_uri,
                database=self.env.neo4j_database,
                username=self.env.neo4j_username,
                password=self.env.neo4j_password,
                timeout_seconds=self.env.neo4j_timeout_seconds,
                verify_connectivity=self.env.neo4j_verify_connectivity,
                apply_schema=self.env.neo4j_apply_schema,
                relationship_types=relationship_types,
            )
        self.graph_repository.connect()

    def close(self) -> None:
        try:
            if self.adx_client is not None:
                self.adx_client.close()
        finally:
            if self.graph_repository is not None:
                self.graph_repository.close()

    def run_job(self, job: JobConfig) -> None:
        if self.adx_client is None or self.graph_repository is None:
            raise RuntimeError("Application must be bootstrapped before running jobs.")
        started_at = time.monotonic()
        stats = JobRunStats(job_name=job.name)
        LOGGER.info("job_started", extra={"job_name": job.name})
        try:
            rows = self.adx_client.query(job.query)
            for raw_row in rows:
                stats.rows_processed += 1
                self._process_row(job, raw_row, stats)
        except Exception:
            LOGGER.exception("job_failed", extra={"job_name": job.name})
            return

        LOGGER.info(
            "job_summary",
            extra={
                "job_name": job.name,
                "rows_processed": stats.rows_processed,
                "nodes_created": stats.nodes_created,
                "nodes_updated": stats.nodes_updated,
                "nodes_skipped": stats.nodes_skipped,
                "relationships_created": stats.relationships_created,
                "relationships_updated": stats.relationships_updated,
                "relationships_skipped": stats.relationships_skipped,
                "elapsed_seconds": round(time.monotonic() - started_at, 3),
            },
        )

    def _process_row(self, job: JobConfig, row: dict[str, object], stats: JobRunStats) -> None:
        context = RowContext(
            row=row,
            job_name=job.name,
            collected_at=datetime.now(timezone.utc),
        )
        try:
            for template in job.nodes:
                mutation = self.builder.build_node(template, context)
                if mutation is None:
                    continue
                stats.record(self.graph_repository.upsert_node(mutation))

            for template in job.relationships:
                mutation = self.builder.build_relationship(template, context)
                if mutation is None:
                    continue
                for result in self.graph_repository.upsert_relationship(mutation):
                    stats.record(result)
        except Exception:
            LOGGER.exception("row_processing_failed", extra={"job_name": job.name, "row": row})
        finally:
            if self.config.runtime.sleep_seconds > 0:
                time.sleep(self.config.runtime.sleep_seconds)
