"""Compile reviewed records into a deterministic static public API."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any

from crew_customs.models import load_yaml_dir, write_json
from crew_customs.validate import validate_repository


SCHEMA_VERSION = "1.0.0"
DATASET_VERSION = "1.0.0"
DISCLAIMER = (
    "Verify current requirements with the relevant authority and airline "
    "procedures before travel."
)
PUBLIC_CUSTOMS_FIELDS = (
    "foodAndAgriculture",
    "medicines",
    "cashDeclaration",
    "alcohol",
    "tobacco",
    "prohibitedItems",
    "restrictedItems",
    "declarationRequirements",
)
PUBLIC_BAGGAGE_FIELDS = {
    "handBaggage": (
        "sharpObjects",
        "liquids",
        "batteriesAndElectronics",
        "prohibitedItems",
    ),
    "checkedCargoBag": (
        "knivesAndSharpObjects",
        "batteriesAndElectronics",
        "prohibitedItems",
    ),
}
RULE_LIST_FIELDS = {
    "foodAndAgriculture",
    "medicines",
    "prohibitedItems",
    "restrictedItems",
    "declarationRequirements",
    "sharpObjects",
    "liquids",
    "batteriesAndElectronics",
    "knivesAndSharpObjects",
}
POLICY_RULE_OBJECT_FIELDS = {"cashDeclaration", "alcohol", "tobacco"}
PUBLIC_RULE_FIELDS = ("text", "sourceIds")
PUBLIC_SOURCE_FIELDS = (
    "id",
    "authorityName",
    "pageTitle",
    "url",
    "jurisdiction",
    "accessedAt",
    "supportsFields",
    "sourceType",
)
SENSITIVE_PUBLIC_TERMS = (
    "key_flight",
    "flag_url",
    "flight",
    "hotel",
    "sharepoint",
    "staff",
    "roster",
    "internal",
    "intranet",
)


def compile_api(root: Path, output: Path, built_at: datetime) -> dict[str, int]:
    """Write the complete public API, replacing *output* only on success."""
    errors = validate_repository(root, built_at.date())
    if errors:
        raise ValueError("Cannot compile invalid repository:\n" + "\n".join(errors))
    airports = sorted(load_yaml_dir(root / "data/airports"), key=lambda item: item["iataCode"])
    countries = sorted(load_yaml_dir(root / "data/countries"), key=lambda item: item["iso2"])
    source_records = load_yaml_dir(root / "data/sources")
    countries_by_iso2 = {country["iso2"]: country for country in countries}
    sources_by_id = {source["id"]: source for source in source_records}
    counts = {
        "airports": len(airports),
        "countries": len(countries),
        "sources": len(source_records),
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
    try:
        compiled_airports = [
            compile_airport(airport, countries_by_iso2[airport["countryIso2"]], sources_by_id)
            for airport in airports
        ]
        compiled_countries = [
            compile_country(country, sources_by_id) for country in countries
        ]
        _write_api_files(
            temporary_output, compiled_airports, compiled_countries, counts, built_at
        )
        _replace_output(temporary_output, output)
    finally:
        if temporary_output.exists():
            shutil.rmtree(temporary_output)
    return counts


def compile_airport(
    airport: dict[str, Any], country: dict[str, Any], sources: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Produce one self-contained airport document from reviewed records."""
    effective_customs = merge_rules(country["customs"], airport.get("customsOverrides", {}))
    effective_baggage_security = merge_rules(
        country["baggageSecurity"], airport.get("baggageSecurityOverrides", {})
    )
    customs = _public_customs(effective_customs)
    baggage_security = _public_baggage_security(effective_baggage_security)
    crew_notes = _public_rule_list(airport.get("crewNotes", country.get("crewNotes", [])))
    review_status = combined_status(airport, country)
    dates = _effective_dates(airport, country, review_status)
    document = {
        "schemaVersion": SCHEMA_VERSION,
        "iataCode": airport["iataCode"],
        "airportName": airport["airportName"],
        "city": airport["city"],
        "country": {"name": country["countryName"], "iso2": country["iso2"]},
        "customs": customs,
        "baggageSecurity": baggage_security,
        "crewNotes": crew_notes,
        "sources": public_sources_for_rules(customs, baggage_security, crew_notes, sources),
        "lastVerified": dates["lastVerified"],
        "nextReviewDue": dates["nextReviewDue"],
        "reviewStatus": review_status,
        "disclaimer": DISCLAIMER,
    }
    _assert_privacy_safe(document)
    return document


def compile_country(
    country: dict[str, Any], sources: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Produce the normalized country document used for audit and reuse."""
    customs = _public_customs(country["customs"])
    baggage_security = _public_baggage_security(country["baggageSecurity"])
    crew_notes = _public_rule_list(country.get("crewNotes", []))
    document = {
        "schemaVersion": SCHEMA_VERSION,
        "iso2": country["iso2"],
        "countryName": country["countryName"],
        "customs": customs,
        "baggageSecurity": baggage_security,
        "crewNotes": crew_notes,
        "sources": public_sources_for_rules(customs, baggage_security, crew_notes, sources),
        "lastVerified": _published_date(country, "lastVerified"),
        "nextReviewDue": _published_date(country, "nextReviewDue"),
        "reviewStatus": country["reviewStatus"],
        "disclaimer": DISCLAIMER,
    }
    _assert_privacy_safe(document)
    return document


def _effective_dates(
    airport: dict[str, Any], country: dict[str, Any], review_status: str
) -> dict[str, str | None]:
    if review_status != "verified":
        return {"lastVerified": None, "nextReviewDue": None}
    return {
        "lastVerified": min(airport["lastVerified"], country["lastVerified"]),
        "nextReviewDue": min(
            airport["nextReviewDue"],
            country["nextReviewDue"],
        ),
    }


def merge_rules(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """Deep merge mapping sections while preserving reviewed list ordering."""
    merged = deepcopy(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge_rules(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _public_customs(customs: dict[str, Any]) -> dict[str, Any]:
    """Keep only the named, reviewed customs categories in public documents."""
    return {
        field: (
            _public_rule_list(customs[field])
            if field in RULE_LIST_FIELDS
            else _public_policy_rule_object(customs[field])
            if field in POLICY_RULE_OBJECT_FIELDS
            else deepcopy(customs[field])
        )
        for field in PUBLIC_CUSTOMS_FIELDS
    }


def _public_policy_rule_object(policy: dict[str, Any]) -> dict[str, Any]:
    """Keep only explicitly permitted rules from an object-valued policy."""
    if "rules" not in policy:
        return {}
    return {"rules": _public_rule_list(policy["rules"])}


def _public_baggage_security(baggage_security: dict[str, Any]) -> dict[str, Any]:
    """Keep only the named, reviewed baggage categories in public documents."""
    return {
        section: {
            field: _public_rule_list(baggage_security[section][field])
            for field in fields
        }
        for section, fields in PUBLIC_BAGGAGE_FIELDS.items()
    }


def _public_rule_list(rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_public_rule(rule) for rule in rules]


def _public_rule(rule: dict[str, Any]) -> dict[str, Any]:
    return {field: deepcopy(rule[field]) for field in PUBLIC_RULE_FIELDS}


def combined_status(airport: dict[str, Any], country: dict[str, Any]) -> str:
    """Return the least-ready status represented by an airport document."""
    statuses = {airport["reviewStatus"], country["reviewStatus"]}
    if "needs_review" in statuses:
        return "needs_review"
    if "research_pending" in statuses:
        return "research_pending"
    return "verified"


def _published_date(record: dict[str, Any], field: str) -> str | None:
    if record["reviewStatus"] != "verified":
        return None
    return record[field]


def public_sources_for_rules(
    customs: dict[str, Any],
    baggage_security: dict[str, Any],
    crew_notes: list[dict[str, Any]],
    sources: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return allowlisted source metadata cited by the effective public rules."""
    source_ids = sorted(_source_ids((customs, baggage_security, crew_notes)))
    return [
        _public_source(sources[source_id])
        for source_id in source_ids
    ]


def _public_source(source: dict[str, Any]) -> dict[str, Any]:
    return {field: deepcopy(source[field]) for field in PUBLIC_SOURCE_FIELDS}


def _source_ids(values: tuple[Any, ...]) -> set[str]:
    found: set[str] = set()
    for value in values:
        if isinstance(value, dict):
            if isinstance(value.get("sourceIds"), list):
                found.update(source_id for source_id in value["sourceIds"] if isinstance(source_id, str))
            found.update(_source_ids(tuple(value.values())))
        elif isinstance(value, list):
            found.update(_source_ids(tuple(value)))
    return found


def _write_api_files(
    output: Path,
    airports: list[dict[str, Any]],
    countries: list[dict[str, Any]],
    counts: dict[str, int],
    built_at: datetime,
) -> None:
    for airport in airports:
        write_json(output / "airports" / f"{airport['iataCode']}.json", airport)
    for country in countries:
        write_json(output / "countries" / f"{country['iso2']}.json", country)

    index = {
        "schemaVersion": SCHEMA_VERSION,
        "datasetVersion": DATASET_VERSION,
        "airports": [
            {
                "iataCode": airport["iataCode"],
                "airportName": airport["airportName"],
                "city": airport["city"],
                "country": airport["country"],
                "reviewStatus": airport["reviewStatus"],
                "endpoint": f"/api/v1/airports/{airport['iataCode']}.json",
            }
            for airport in airports
        ],
    }
    status = {
        "schemaVersion": SCHEMA_VERSION,
        "datasetVersion": DATASET_VERSION,
        "builtAt": _format_built_at(built_at),
        "counts": counts,
        "oldestVerificationDate": min(
            (
                record["lastVerified"]
                for record in [*airports, *countries]
                if record["reviewStatus"] == "verified"
            ),
            default=None,
        ),
    }
    _assert_privacy_safe(index)
    _assert_privacy_safe(status)
    write_json(output / "index.json", index)
    write_json(output / "status.json", status)


def _format_built_at(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("built_at must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _assert_privacy_safe(value: Any) -> None:
    _assert_no_sensitive_data(value)


def _assert_no_sensitive_data(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            _assert_safe_text(key)
            _assert_no_sensitive_data(item)
    elif isinstance(value, list):
        for item in value:
            _assert_no_sensitive_data(item)
    elif isinstance(value, str):
        _assert_safe_text(value)


def _assert_safe_text(value: str) -> None:
    normalized = value.casefold()
    for term in SENSITIVE_PUBLIC_TERMS:
        if term in normalized:
            raise ValueError(f"Sensitive operational data in public output: {term}")


def _replace_output(temporary_output: Path, output: Path) -> None:
    """Replace a completed directory with rollback protection for the old output."""
    if not output.exists():
        os.replace(temporary_output, output)
        return

    backup = Path(tempfile.mkdtemp(prefix=f".{output.name}-backup-", dir=output.parent))
    backup.rmdir()
    os.replace(output, backup)
    try:
        os.replace(temporary_output, output)
    except BaseException:
        os.replace(backup, output)
        raise
    else:
        shutil.rmtree(backup)
