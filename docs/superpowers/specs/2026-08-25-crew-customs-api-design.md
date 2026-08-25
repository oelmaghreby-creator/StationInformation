# Crew Destination Customs API — Design Specification

## 1. Purpose

Build a public, read-only API for a roster SaaS application. A crew member opens a flight card, selects the destination, and sees verified station guidance for personal hand baggage and checked crew baggage (operationally called the “cargo bag”).

The API is informational. It does not replace airline manuals, dangerous-goods procedures, security instructions, or instructions from local authorities.

## 2. Confirmed scope

- Audience: operating crew only.
- Expected scale: 5,000–20,000 users.
- Language: English for the first release.
- Initial network: the airport codes supplied in `Final_Flight_Numbers_DIL.csv`.
- Network growth: new IATA codes can be added later without changing the API contract.
- Hosting: public static JSON API on GitHub Pages.
- Updates: curated rules with weekly official-source monitoring and human review.
- No passenger-facing content.
- No roster, employee, flight, or hotel data is published.

## 3. Input assessment

The supplied CSV contains 371 flight records and 97 unique three-letter route codes, including AUH. The first release therefore covers 96 destination codes excluding AUH, unless AUH is explicitly retained as a destination later.

The CSV fields used for initialization are:

- `Route` — IATA airport code.
- `City` — display city.
- `Country` — display country.
- `Country_Code` — candidate ISO 3166-1 alpha-2 code.
- `Region` — optional display grouping.

Some country codes are blank or inconsistent. The build must normalize and validate country mappings independently. The following fields must not be copied into the public API: `Flight No.`, `Key_Flight`, `Related_Hotel`, SharePoint URLs, or other operational fields.

## 4. Information shown on a destination card

### Entry and customs

- Food and agricultural restrictions.
- Medicines and prescription/document requirements.
- Cash and monetary-instrument declaration thresholds.
- Alcohol and tobacco crew allowances, when an official crew-specific rule exists.
- Prohibited and restricted imports.
- Declaration requirements and arrival procedures.
- Crew-specific notes supported by an official source.

### Baggage and security

Rules are separated by bag location because an item may be prohibited in hand baggage but allowed conditionally in checked baggage.

- Hand baggage: sharp objects, liquids, batteries/electronics, and prohibited items.
- Checked crew baggage (“cargo bag”): knives/sharp objects, batteries/electronics, and prohibited items.
- Destination-specific aviation-security exceptions when supported by an official airport, civil-aviation, police, border, or customs source.

Global airline dangerous-goods requirements are outside the authoritative scope of this API unless the project later receives an approved airline source. The API may point the crew member to the airline manual where appropriate but must not invent or reproduce unapproved operational rules.

## 5. Data model

Country rules are stored once and referenced by every applicable airport. Airport files contain identity data and airport-specific overrides only. The published airport response may be compiled into a self-contained document for simple SaaS consumption.

```json
{
  "schemaVersion": "1.0.0",
  "iataCode": "JFK",
  "airportName": "John F. Kennedy International Airport",
  "city": "New York",
  "country": {
    "name": "United States",
    "iso2": "US"
  },
  "customs": {
    "foodAndAgriculture": [],
    "medicines": [],
    "cashDeclaration": {},
    "alcohol": {},
    "tobacco": {},
    "prohibitedItems": [],
    "restrictedItems": [],
    "declarationRequirements": []
  },
  "baggageSecurity": {
    "handBaggage": {
      "sharpObjects": [],
      "liquids": [],
      "batteriesAndElectronics": [],
      "prohibitedItems": []
    },
    "checkedCargoBag": {
      "knivesAndSharpObjects": [],
      "batteriesAndElectronics": [],
      "prohibitedItems": []
    }
  },
  "crewNotes": [],
  "sources": [],
  "lastVerified": "2026-08-25",
  "nextReviewDue": "2026-11-25",
  "reviewStatus": "verified",
  "disclaimer": "Verify current requirements with the relevant authority and airline procedures before travel."
}
```

Every substantive rule entry must be traceable to at least one source identifier. Source records contain authority name, page title, URL, jurisdiction, access date, content fingerprint, and the fields supported by that source.

## 6. Public API

The versioned paths are:

```text
/api/v1/index.json
/api/v1/airports/{IATA}.json
/api/v1/countries/{ISO2}.json
/api/v1/status.json
```

- `index.json` lists supported airports, city/country metadata, review status, and airport endpoint.
- `airports/{IATA}.json` is the primary document consumed by the flight card.
- `countries/{ISO2}.json` exposes the normalized country rules for audit and reuse.
- `status.json` reports schema version, dataset version, build time, counts, and oldest verification date.
- Unknown airport codes return a normal GitHub Pages 404; the SaaS should show “Station customs information is not yet available.”
- Breaking schema changes require a new API version such as `/api/v2/`.

Static files should be cached by the SaaS. A dataset version in `status.json` allows the client to detect an update without re-downloading every airport document.

## 7. Source policy

Preferred sources, in order:

1. National customs, border, or government authorities.
2. National civil-aviation or aviation-security authorities.
3. Official airport authority pages.
4. Official embassy or consular guidance when it directly cites the responsible authority.

Travel blogs, commercial allowance summaries, social media, search-result snippets, and AI-generated summaries are not authoritative sources. When no crew-specific allowance is published, the API must say that no verified crew-specific rule was found; it must not silently substitute a passenger allowance.

Conflicting official sources are flagged `needs_review` and are not published as verified guidance until resolved.

## 8. Automated monitoring and review

A scheduled GitHub Action runs weekly:

1. Load the source registry.
2. Fetch official pages with controlled timeouts, retries, and a descriptive user agent.
3. Normalize non-semantic markup where practical.
4. Compute a new fingerprint and compare it with the stored fingerprint.
5. If a meaningful change is detected, create or update one GitHub issue per jurisdiction/source.
6. Include affected countries and airports, old/new fingerprints, an extracted change preview, potentially affected fields, fetch status, and a reviewer checklist.
7. Do not modify verified rule data automatically.

Repeated runs must not create duplicate open issues for the same unresolved source change. Fetch failures create a monitoring issue only after a defined consecutive-failure threshold so transient outages do not create noise.

After a reviewed data pull request is merged, validation and deployment run automatically.

## 9. Validation and publication gates

The build fails when:

- An IATA code is missing, duplicated, or not exactly three uppercase letters.
- A country code is invalid or an airport-country mapping is unresolved.
- Required schema fields are absent or have the wrong type.
- A verified substantive rule lacks a source reference.
- A source URL is malformed or uses an unapproved source category.
- Dates are invalid or `nextReviewDue` precedes `lastVerified`.
- Published files contain forbidden source columns or sensitive operational data.
- An airport refers to a missing country record.
- Generated files differ from committed source data without being rebuilt.

Tests include schema tests, mapping tests, fixture-based compiler tests, monitoring deduplication tests, and a smoke test for representative API files. GitHub Pages deploys only after all checks pass.

## 10. Repository boundaries

```text
data/
  airports/
  countries/
  sources/
schemas/
scripts/
tests/
public/api/v1/
.github/workflows/
docs/
```

- `data/` contains human-reviewed source records.
- `scripts/` normalizes the supplied CSV, validates data, compiles API files, and monitors sources.
- `public/` contains generated deployable JSON only.
- Workflow files run tests, compile the API, deploy Pages, and monitor sources.

## 11. Security and privacy

- The API contains public regulatory information only.
- No secrets are embedded in JSON or workflow logs.
- GitHub workflow permissions use least privilege; the monitor receives issue-write permission only.
- External page content is treated as untrusted data and never executed.
- Fetch size and redirect limits reduce abuse and accidental downloads.
- The CSV is used only to derive the airport network; flight, hotel, SharePoint, and other operational data are excluded.

## 12. Acceptance criteria

- Every selected destination has a valid airport endpoint or an explicit `research_pending` status.
- The flight-card client can retrieve a destination using one IATA code without joining multiple files.
- Hand baggage and checked crew baggage rules are visibly separated.
- Every verified rule is source-traceable and dated.
- Weekly monitoring opens deduplicated review issues without changing published rules.
- Validation blocks malformed, unsupported, stale, or accidentally sensitive output.
- GitHub Pages serves the versioned JSON endpoints publicly after a successful deployment.

## 13. Delivery sequence

1. Scaffold repository, schemas, compiler, tests, and GitHub workflows.
2. Normalize the supplied airport network.
3. Establish the source registry and research template.
4. Research and review destinations in country batches.
5. Generate and validate API output.
6. Configure GitHub Pages and perform client-call smoke tests.

