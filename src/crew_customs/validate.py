"""Schema and cross-record validation for reviewed source data."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from crew_customs.models import load_yaml_dir


APPROVED_SOURCE_TYPES = {
    "customs",
    "government",
    "civil_aviation",
    "airport_authority",
    "embassy",
}

AIRPORT_RULE_PATHS = (
    ("crewNotes",),
    ("customsOverrides", "foodAndAgriculture"),
    ("customsOverrides", "medicines"),
    ("customsOverrides", "cashDeclaration", "rules"),
    ("customsOverrides", "alcohol", "rules"),
    ("customsOverrides", "tobacco", "rules"),
    ("customsOverrides", "prohibitedItems"),
    ("customsOverrides", "restrictedItems"),
    ("customsOverrides", "declarationRequirements"),
    ("baggageSecurityOverrides", "handBaggage", "sharpObjects"),
    ("baggageSecurityOverrides", "handBaggage", "liquids"),
    ("baggageSecurityOverrides", "handBaggage", "batteriesAndElectronics"),
    ("baggageSecurityOverrides", "handBaggage", "prohibitedItems"),
    ("baggageSecurityOverrides", "checkedCargoBag", "knivesAndSharpObjects"),
    ("baggageSecurityOverrides", "checkedCargoBag", "batteriesAndElectronics"),
    ("baggageSecurityOverrides", "checkedCargoBag", "prohibitedItems"),
)

COUNTRY_RULE_PATHS = (
    ("crewNotes",),
    ("customs", "foodAndAgriculture"),
    ("customs", "medicines"),
    ("customs", "cashDeclaration", "rules"),
    ("customs", "alcohol", "rules"),
    ("customs", "tobacco", "rules"),
    ("customs", "prohibitedItems"),
    ("customs", "restrictedItems"),
    ("customs", "declarationRequirements"),
    ("baggageSecurity", "handBaggage", "sharpObjects"),
    ("baggageSecurity", "handBaggage", "liquids"),
    ("baggageSecurity", "handBaggage", "batteriesAndElectronics"),
    ("baggageSecurity", "handBaggage", "prohibitedItems"),
    ("baggageSecurity", "checkedCargoBag", "knivesAndSharpObjects"),
    ("baggageSecurity", "checkedCargoBag", "batteriesAndElectronics"),
    ("baggageSecurity", "checkedCargoBag", "prohibitedItems"),
)


def validate_repository(root: Path, today: date) -> list[str]:
    """Return stable, human-readable errors for the reviewed-data repository."""
    del today  # Kept in the public interface for callers that validate at build time.

    schemas = _load_schemas(root / "schemas")
    airports = load_yaml_dir(root / "data/airports")
    countries = load_yaml_dir(root / "data/countries")
    sources = load_yaml_dir(root / "data/sources")
    errors = _schema_errors("airport", airports, schemas["airport"])
    errors += _schema_errors("country", countries, schemas["country"])
    errors += _schema_errors("source", sources, schemas["source"])

    errors += _duplicate_errors(airports, "iataCode", "airport IATA code")
    errors += _duplicate_errors(countries, "iso2", "country ISO2 code")
    errors += _duplicate_errors(sources, "id", "source ID")

    country_ids = _string_ids(countries, "iso2")
    source_by_id = {
        source_id: source
        for source in sources
        if isinstance(source_id := source.get("id"), str)
    }
    for airport in airports:
        airport_id = _label(airport, "iataCode")
        country_iso2 = airport.get("countryIso2")
        if isinstance(country_iso2, str) and country_iso2 not in country_ids:
            errors.append(f"{airport_id}: countryIso2 does not resolve: {country_iso2}")

    for source in sources:
        source_id = _label(source, "id")
        source_type = source.get("sourceType")
        if isinstance(source_type, str) and source_type not in APPROVED_SOURCE_TYPES:
            errors.append(f"{source_id}: unapproved sourceType: {source_type}")
        _validate_date(source_id, "accessedAt", source.get("accessedAt"), errors)

    for records, label_key, rule_paths in (
        (airports, "iataCode", AIRPORT_RULE_PATHS),
        (countries, "iso2", COUNTRY_RULE_PATHS),
    ):
        for record in records:
            record_id = _label(record, label_key)
            _validate_record_dates(record_id, record, errors)
            verified = record.get("reviewStatus") == "verified"
            for rule in _rules_at_paths(record, rule_paths):
                source_ids = rule.get("sourceIds")
                if verified and (not isinstance(source_ids, list) or not source_ids):
                    errors.append(f"{record_id}: verified rule has no sourceIds")
                    continue
                if not isinstance(source_ids, list):
                    continue
                for source_id in source_ids:
                    if not isinstance(source_id, str) or source_id not in source_by_id:
                        errors.append(f"{record_id}: unknown sourceId: {source_id}")
                        continue
                    source_type = source_by_id[source_id].get("sourceType")
                    if source_type not in APPROVED_SOURCE_TYPES:
                        errors.append(
                            f"{record_id}: sourceId uses unapproved sourceType: {source_id}"
                        )

    return sorted(set(errors))


def _load_schemas(path: Path) -> dict[str, dict[str, Any]]:
    return {
        name: json.loads((path / f"{name}.schema.json").read_text(encoding="utf-8"))
        for name in ("airport", "country", "source")
    }


def _schema_errors(
    record_type: str, records: list[dict[str, Any]], schema: dict[str, Any]
) -> list[str]:
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors: list[str] = []
    for record in records:
        label_key = {"airport": "iataCode", "country": "iso2", "source": "id"}[record_type]
        label = _label(record, label_key)
        for error in validator.iter_errors(record):
            location = ".".join(str(part) for part in error.absolute_path) or "record"
            errors.append(f"{label}: {location}: {error.message}")
    return errors


def _duplicate_errors(
    records: list[dict[str, Any]], key: str, label: str
) -> list[str]:
    seen: set[str] = set()
    errors: list[str] = []
    for record in records:
        value = record.get(key)
        if isinstance(value, str):
            if value in seen:
                errors.append(f"duplicate {label}: {value}")
            seen.add(value)
    return errors


def _string_ids(records: list[dict[str, Any]], key: str) -> set[str]:
    return {record[key] for record in records if isinstance(record.get(key), str)}


def _rules_at_paths(
    record: dict[str, Any], paths: tuple[tuple[str, ...], ...]
) -> Iterator[dict[str, Any]]:
    for path in paths:
        value: Any = record
        for segment in path:
            if not isinstance(value, dict) or segment not in value:
                break
            value = value[segment]
        else:
            if isinstance(value, list):
                yield from (item for item in value if isinstance(item, dict))


def _validate_record_dates(
    record_id: str, record: dict[str, Any], errors: list[str]
) -> None:
    last_verified = _validate_date(record_id, "lastVerified", record.get("lastVerified"), errors)
    next_review_due = _validate_date(
        record_id, "nextReviewDue", record.get("nextReviewDue"), errors
    )
    if (
        last_verified is not None
        and next_review_due is not None
        and next_review_due < last_verified
    ):
        errors.append(f"{record_id}: nextReviewDue precedes lastVerified")


def _validate_date(
    record_id: str, field: str, value: Any, errors: list[str]
) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        errors.append(f"{record_id}: invalid {field} date: {value}")
        return None


def _label(record: dict[str, Any], key: str) -> str:
    value = record.get(key)
    return value if isinstance(value, str) else f"unknown {key}"
