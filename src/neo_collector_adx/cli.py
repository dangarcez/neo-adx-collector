from __future__ import annotations

import argparse
import logging

from .app import CollectorApplication
from .config import load_environment
from .dotenv import load_dotenv_file
from .exceptions import ConfigurationError
from .logging_utils import configure_logging
from .scheduler import JobScheduler


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="neo-collector-adx",
        description="Collect rows from ADX and project them into Neo4j nodes and relationships.",
    )
    parser.add_argument(
        "--env",
        default=".env",
        help="Path to the .env file. Defaults to .env in the current directory.",
    )
    parser.add_argument(
        "--config",
        help="Path to the YAML configuration file. Overrides APP_CONFIG_PATH.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run each configured job once and exit.",
    )
    parser.add_argument(
        "--validate-config",
        action="store_true",
        help="Validate the environment and YAML configuration, then exit.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    application = None

    try:
        load_dotenv_file(args.env, required=args.env != ".env")
        env = load_environment()
        if args.config:
            env.config_path = args.config

        configure_logging(env.log_level, env.log_format)
        application = CollectorApplication.from_environment(env, env.config_path)
        if args.validate_config:
            logging.getLogger(__name__).info(
                "configuration_valid",
                extra={"config_path": env.config_path},
            )
            return 0

        application.bootstrap()
        scheduler = JobScheduler(application.config.jobs, application.run_job)
        if args.once:
            scheduler.run_once()
        else:
            scheduler.run_forever()
        return 0
    except ConfigurationError as exc:
        logging.getLogger(__name__).error("configuration_error: %s", exc)
        return 2
    except FileNotFoundError as exc:
        logging.getLogger(__name__).error("file_not_found: %s", exc)
        return 2
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("interrupted")
        return 130
    finally:
        if application is not None:
            application.close()
