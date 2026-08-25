"""Command-line interface for initializing and publishing reviewed data."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
from typing import Sequence

import yaml

from crew_customs.compiler import compile_api
from crew_customs.models import load_yaml
from crew_customs.network import normalize_network
from crew_customs.release import (
    create_release_snapshot,
    validate_history_privacy,
    validate_publish_snapshot,
)
from crew_customs.validate import validate_repository


def main(arguments: Sequence[str] | None = None) -> int:
    """Run a CLI subcommand and return its process status."""
    parser = _parser()
    namespace = parser.parse_args(arguments)
    if namespace.command == "init-network":
        return _init_network(namespace)
    if namespace.command == "validate":
        return _validate(namespace.root)
    if namespace.command == "build":
        return _build(namespace)
    if namespace.command == "monitor":
        return _monitor(namespace.root)
    if namespace.command == "release-check":
        return _release_check(namespace.root)
    if namespace.command == "release-snapshot":
        return _release_snapshot(namespace)
    parser.error("a command is required")
    return 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="crew-customs")
    commands = parser.add_subparsers(dest="command")

    initialize = commands.add_parser("init-network")
    initialize.add_argument("--csv", required=True, type=Path)
    initialize.add_argument("--exclude", action="append", default=[])
    initialize.add_argument("--root", type=Path, default=Path("."))

    validate = commands.add_parser("validate")
    validate.add_argument("--root", type=Path, default=Path("."))

    build = commands.add_parser("build")
    build.add_argument("--root", type=Path, default=Path("."))
    build.add_argument("--output", type=Path)
    build.add_argument(
        "--built-at",
        help="UTC ISO 8601 timestamp; defaults to SOURCE_DATE_EPOCH when set",
    )

    monitor = commands.add_parser("monitor")
    monitor.add_argument("--root", type=Path, default=Path("."))

    release_check = commands.add_parser(
        "release-check",
        help="reject private inputs or operational content from the Pages snapshot",
    )
    release_check.add_argument("--root", type=Path, default=Path("."))

    release_snapshot = commands.add_parser(
        "release-snapshot",
        help="export tracked HEAD for a new parentless public repository",
    )
    release_snapshot.add_argument("--root", type=Path, default=Path("."))
    release_snapshot.add_argument("--output", required=True, type=Path)
    return parser


def _init_network(namespace: argparse.Namespace) -> int:
    root = namespace.root
    try:
        mapping = _country_mapping(root)
        rows = normalize_network(namespace.csv, set(namespace.exclude), mapping)
        airports, countries = _initial_records(rows)
        _write_missing_records(root, airports, countries)
    except (OSError, ValueError, yaml.YAMLError) as error:
        print(error, file=sys.stderr)
        return 1
    return 0


def _country_mapping(root: Path) -> dict[str, str]:
    path = root / "data/country_mapping.yaml"
    if not path.exists():
        return {}
    mapping = load_yaml(path)
    if not all(isinstance(country, str) and country.strip() for country in mapping):
        raise ValueError("Country mapping keys must be non-empty")
    if not all(isinstance(code, str) for code in mapping.values()):
        raise ValueError("Country mapping must map country names to ISO2 codes")
    return mapping


def _initial_records(
    rows: list[dict[str, str]],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    airports = [
        {
            "iataCode": row["iataCode"],
            "airportName": f"{row['iataCode']} airport (research pending)",
            "city": row["city"],
            "countryIso2": row["countryIso2"],
            "reviewStatus": "research_pending",
            "lastVerified": None,
            "nextReviewDue": None,
        }
        for row in rows
    ]
    country_identities = {
        (row["countryIso2"], row["countryName"])
        for row in rows
    }
    countries = [
        {
            "iso2": iso2,
            "countryName": country_name,
            "customs": _empty_customs(),
            "baggageSecurity": _empty_baggage_security(),
            "crewNotes": [],
            "reviewStatus": "research_pending",
            "lastVerified": None,
            "nextReviewDue": None,
        }
        for iso2, country_name in sorted(country_identities)
    ]
    return airports, countries


def _empty_customs() -> dict[str, object]:
    return {
        "foodAndAgriculture": [],
        "medicines": [],
        "cashDeclaration": {},
        "alcohol": {},
        "tobacco": {},
        "prohibitedItems": [],
        "restrictedItems": [],
        "declarationRequirements": [],
    }


def _empty_baggage_security() -> dict[str, object]:
    return {
        "handBaggage": {
            "sharpObjects": [],
            "liquids": [],
            "batteriesAndElectronics": [],
            "prohibitedItems": [],
        },
        "checkedCargoBag": {
            "knivesAndSharpObjects": [],
            "batteriesAndElectronics": [],
            "prohibitedItems": [],
        },
    }


def _write_missing_records(
    root: Path, airports: list[dict[str, object]], countries: list[dict[str, object]]
) -> None:
    for country in countries:
        _write_if_missing(root / "data/countries" / f"{country['iso2']}.yaml", country)
    for airport in airports:
        _write_if_missing(root / "data/airports" / f"{airport['iataCode']}.yaml", airport)


def _write_if_missing(path: Path, record: dict[str, object]) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(record, sort_keys=False), encoding="utf-8")


def _validate(root: Path) -> int:
    try:
        errors = validate_repository(root, date.today())
    except (OSError, ValueError, yaml.YAMLError) as error:
        print(error, file=sys.stderr)
        return 1
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


def _build(namespace: argparse.Namespace) -> int:
    try:
        built_at = _build_timestamp(namespace.built_at)
        if _validate(namespace.root):
            return 1
        output = namespace.output
        if output is None:
            output = namespace.root / "public/api/v1"
        elif not output.is_absolute():
            output = namespace.root / output
        compile_api(namespace.root, output, built_at)
    except (OSError, ValueError, yaml.YAMLError) as error:
        print(error, file=sys.stderr)
        return 1
    return 0


def _build_timestamp(argument: str | None) -> datetime:
    """Return an explicit UTC build time or a current time for local builds.

    ``SOURCE_DATE_EPOCH`` intentionally carries the same UTC ISO 8601 value as
    ``--built-at``.  Keeping this contract explicit makes generated output
    reproducible in CI without removing its public ``builtAt`` metadata.
    """
    raw = argument if argument is not None else os.environ.get("SOURCE_DATE_EPOCH")
    if raw is None:
        return datetime.now(timezone.utc)
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("built-at must be a UTC ISO 8601 timestamp") from error
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("built-at must be a UTC ISO 8601 timestamp")
    return value.astimezone(timezone.utc)


def _monitor(root: Path) -> int:
    try:
        from crew_customs.monitor import run_monitor

        run_monitor(root)
    except (ImportError, OSError, RuntimeError, ValueError) as error:
        print(error, file=sys.stderr)
        return 1
    return 0


def _release_check(root: Path) -> int:
    try:
        errors = validate_publish_snapshot(root)
        if not errors:
            errors.extend(validate_history_privacy(root))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(error, file=sys.stderr)
        return 1
    for error in errors:
        print(f"Publish snapshot rejected: {error}", file=sys.stderr)
    return 1 if errors else 0


def _release_snapshot(namespace: argparse.Namespace) -> int:
    try:
        create_release_snapshot(namespace.root, namespace.output)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
