from __future__ import annotations

import os
import tempfile
import textwrap
import unittest

from neo_collector_adx.config import load_app_config
from neo_collector_adx.exceptions import ConfigurationError


class ConfigLoadingTests(unittest.TestCase):
    def test_loads_valid_configuration(self) -> None:
        yaml_text = """
        runtime:
          default_interval_seconds: 120
          sleep_seconds: 0.5
          dry_run: true
        jobs:
          - name: signins
            query: |
              SigninLogs
              | project UserPrincipalName, IPAddress, FailedAttempts
            nodes:
              - type: User
                template_hashes:
                  - user-v1
                update_policy: mergeAtChange
                dynamic_properties:
                  name: UserPrincipalName
            relationships:
              - type: AUTHENTICATED_FROM
                template_hashes:
                  - rel-v1
                source:
                  type: User
                  match_column_attributes:
                    name: UserPrincipalName
                target:
                  type: IPAddress
                  match_attributes:
                    columns:
                      name: IPAddress
        """

        with _temp_yaml(yaml_text) as path:
            config = load_app_config(path)

        self.assertTrue(config.runtime.dry_run)
        self.assertEqual(120, config.runtime.default_interval_seconds)
        self.assertEqual(1, len(config.jobs))
        self.assertEqual("merge_at_change", config.jobs[0].nodes[0].update_policy)
        self.assertEqual("rel-v1", config.jobs[0].relationships[0].template_hash)

    def test_rejects_node_without_name_property(self) -> None:
        yaml_text = """
        jobs:
          - name: signins
            query: SigninLogs
            nodes:
              - type: User
                template_hashes:
                  - user-v1
                static_properties:
                  category: identity
        """

        with _temp_yaml(yaml_text) as path:
            with self.assertRaises(ConfigurationError):
                load_app_config(path)

    def test_rejects_relationship_without_match_attributes(self) -> None:
        yaml_text = """
        jobs:
          - name: signins
            query: SigninLogs
            relationships:
              - type: AUTHENTICATED_FROM
                template_hash: rel-v1
                source:
                  type: User
                  match_attributes: {}
                target:
                  type: IPAddress
                  match_attributes:
                    columns:
                      name: IPAddress
        """

        with _temp_yaml(yaml_text) as path:
            with self.assertRaises(ConfigurationError):
                load_app_config(path)


class _temp_yaml:
    def __init__(self, text: str):
        self.text = textwrap.dedent(text).strip()
        self.file = None

    def __enter__(self) -> str:
        self.file = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
        self.file.write(self.text)
        self.file.flush()
        return self.file.name

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.file is not None:
            file_name = self.file.name
            self.file.close()
            if file_name:
                os.unlink(file_name)
