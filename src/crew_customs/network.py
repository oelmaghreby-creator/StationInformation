"""Privacy-safe normalization of the supplied airport network."""

import csv
import re
from pathlib import Path


def normalize_network(
    csv_path: Path, exclude: set[str], country_mapping: dict[str, str] | None = None
) -> list[dict[str, str]]:
    """Return deterministic, deduplicated airport identities from a network CSV.

    Only the public identity fields are retained. Operational columns from the
    source (such as flight, hotel, or flag URL details) are intentionally not
    copied into the returned records.
    """
    excluded = {code.strip().upper() for code in exclude}
    mappings = country_mapping or {}
    if any(not isinstance(country, str) or not country.strip() for country in mappings):
        raise ValueError("Country mapping keys must be non-empty")
    unique: dict[str, dict[str, str]] = {}
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            iata = row["Route"].strip().upper()
            if iata in excluded:
                continue
            if not re.fullmatch(r"[A-Z]{3}", iata):
                raise ValueError(f"Invalid IATA code: {iata!r}")
            country_name = row["Country"].strip()
            if not country_name:
                raise ValueError("Invalid country name")
            country_iso2 = row["Country_Code"].strip().upper()
            mapped_iso2 = mappings.get(country_name)
            if mapped_iso2 is None:
                raise ValueError(f"Unresolved country mapping for {country_name}")
            mapped_iso2 = mapped_iso2.strip().upper()
            if not re.fullmatch(r"[A-Z]{2}", mapped_iso2):
                raise ValueError(f"Invalid mapped country code for {country_name}: {mapped_iso2!r}")
            if country_iso2 and country_iso2 != mapped_iso2:
                raise ValueError(f"Conflicting country mapping for {country_name}: {country_iso2}")
            country_iso2 = mapped_iso2
            record = {
                "iataCode": iata,
                "city": row["City"].strip(),
                "countryName": country_name,
                "countryIso2": country_iso2,
                "region": row["Region"].strip(),
            }
            if iata in unique and unique[iata] != record:
                raise ValueError(f"Conflicting airport rows: {iata}")
            unique[iata] = record
    return [unique[key] for key in sorted(unique)]
