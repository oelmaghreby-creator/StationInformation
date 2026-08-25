# Crew Destination Customs API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and publish a versioned, crew-only destination customs and baggage-security JSON API from the supplied airport network, with validation, source traceability, and human-reviewed weekly source monitoring.

**Architecture:** Human-reviewed YAML records are the source of truth. Focused Python modules normalize the network, validate records, compile self-contained airport JSON files, and monitor official source pages; GitHub Actions tests and deploys compiled files to GitHub Pages. Monitoring opens deduplicated GitHub issues and never edits published rules.

**Tech Stack:** Python 3.12 standard library, PyYAML 6.0.2, jsonschema 4.23.0, pytest 8.3.5, GitHub Actions, GitHub Pages static JSON.

**Spec:** `docs/superpowers/specs/2026-08-25-crew-customs-api-design.md`

## Global Constraints

- Audience is operating crew only; language is English.
- Public output contains no roster, staff, flight, hotel, SharePoint, or other operational data.
- Initial source is `Final_Flight_Numbers_DIL.csv`; exclude AUH from the destination set for v1.
- Separate hand baggage from checked crew baggage (`checkedCargoBag`).
- Never substitute passenger allowances when no verified crew-specific allowance exists.
- Every verified substantive rule must reference an approved official source.
- Monitoring may open/update issues but must never automatically modify verified rules.
- Public API paths remain under `/api/v1/`; breaking changes require a new version.
- Workflows use least-privilege permissions and Python 3.12.

## File map

```text
pyproject.toml                         Dependency and test configuration
README.md                              API use, local commands, disclaimer
inputs/Final_Flight_Numbers_DIL.csv    Supplied network input (not deployed)
schemas/country.schema.json            Country source-record contract
schemas/airport.schema.json            Airport source-record contract
schemas/source.schema.json             Official-source contract
data/airports/*.yaml                   Normalized airport identities/overrides
data/countries/*.yaml                  Reviewed jurisdiction rules
data/sources/*.yaml                    Source registry and fingerprints
src/crew_customs/models.py             Typed loading helpers
src/crew_customs/network.py            CSV normalization and privacy allowlist
src/crew_customs/validate.py           Cross-file and source validation
src/crew_customs/compiler.py           Deterministic public JSON compiler
src/crew_customs/monitor.py            Fetch, fingerprint, and issue payload logic
src/crew_customs/cli.py                Command-line entry points
tests/fixtures/                         Small deterministic fixtures
tests/test_network.py                  Network normalization tests
tests/test_validation.py               Schema/cross-record tests
tests/test_compiler.py                 Public API and privacy tests
tests/test_monitor.py                  Change and deduplication tests
public/api/v1/                          Generated GitHub Pages files
.github/workflows/ci-pages.yml          Validate, compile, deploy
.github/workflows/monitor-sources.yml    Weekly source monitoring
```

---

### Task 1: Project foundation and typed loaders

**Files:**
- Create: `pyproject.toml`
- Create: `src/crew_customs/__init__.py`
- Create: `src/crew_customs/models.py`
- Create: `tests/test_models.py`

**Interfaces:**
- Produces: `load_yaml(path: Path) -> dict[str, Any]`, `load_yaml_dir(path: Path) -> list[dict[str, Any]]`, `write_json(path: Path, value: Any) -> None`.

- [ ] **Step 1: Write failing loader and deterministic-output tests**

```python
from pathlib import Path
from crew_customs.models import load_yaml, write_json

def test_load_yaml_and_write_sorted_json(tmp_path: Path):
    source = tmp_path / "record.yaml"
    source.write_text("iataCode: JFK\ncity: New York\n", encoding="utf-8")
    assert load_yaml(source)["iataCode"] == "JFK"
    target = tmp_path / "record.json"
    write_json(target, {"z": 1, "a": 2})
    assert target.read_text(encoding="utf-8") == '{\n  "a": 2,\n  "z": 1\n}\n'
```

- [ ] **Step 2: Run the test and confirm the missing-module failure**

Run: `python -m pytest tests/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'crew_customs'`.

- [ ] **Step 3: Add pinned project configuration**

```toml
[build-system]
requires = ["setuptools>=75"]
build-backend = "setuptools.build_meta"

[project]
name = "crew-customs-api"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["PyYAML==6.0.2", "jsonschema==4.23.0"]

[project.optional-dependencies]
test = ["pytest==8.3.5"]

[project.scripts]
crew-customs = "crew_customs.cli:main"

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
```

- [ ] **Step 4: Implement focused YAML and JSON helpers**

```python
from pathlib import Path
from typing import Any
import json
import yaml

def load_yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected mapping in {path}")
    return value

def load_yaml_dir(path: Path) -> list[dict[str, Any]]:
    return [load_yaml(item) for item in sorted(path.glob("*.yaml"))]

def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
```

- [ ] **Step 5: Run tests and commit**

Run: `python -m pytest tests/test_models.py -v`
Expected: PASS.

```bash
git add pyproject.toml src/crew_customs tests/test_models.py
git commit -m "build: initialize crew customs project"
```

### Task 2: Normalize and privacy-filter the supplied network

**Files:**
- Copy: `inputs/Final_Flight_Numbers_DIL.csv`
- Create: `src/crew_customs/network.py`
- Create: `tests/test_network.py`
- Create: `tests/fixtures/network.csv`

**Interfaces:**
- Consumes: CSV columns `Route`, `City`, `Country`, `Country_Code`, `Region`.
- Produces: `normalize_network(csv_path: Path, exclude: set[str]) -> list[dict[str, str]]`.

- [ ] **Step 1: Create a fixture with duplication, AUH, and forbidden operational fields**

```csv
Route,City,Country,Country_Code,Region,Flight No.,Related_Hotel,Flag_Url
JFK,New York,United States,US,Americas,EY001,Hotel A,https://private.example
JFK,New York,United States,US,Americas,EY003,Hotel A,https://private.example
AUH,Abu Dhabi,United Arab Emirates,AE,Asia,EY002,Hotel B,https://private.example
```

- [ ] **Step 2: Write the failing normalization test**

```python
from pathlib import Path
from crew_customs.network import normalize_network

def test_network_is_unique_excludes_auh_and_allowlists_fields():
    rows = normalize_network(Path("tests/fixtures/network.csv"), {"AUH"})
    assert rows == [{
        "iataCode": "JFK", "city": "New York", "countryName": "United States",
        "countryIso2": "US", "region": "Americas"
    }]
    assert "Related_Hotel" not in rows[0]
```

- [ ] **Step 3: Run the test and confirm failure**

Run: `python -m pytest tests/test_network.py -v`
Expected: FAIL because `crew_customs.network` does not exist.

- [ ] **Step 4: Implement strict IATA normalization and field allowlisting**

```python
import csv, re
from pathlib import Path

def normalize_network(csv_path: Path, exclude: set[str]) -> list[dict[str, str]]:
    unique = {}
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            iata = row["Route"].strip().upper()
            if iata in exclude:
                continue
            if not re.fullmatch(r"[A-Z]{3}", iata):
                raise ValueError(f"Invalid IATA code: {iata!r}")
            record = {"iataCode": iata, "city": row["City"].strip(),
                      "countryName": row["Country"].strip(),
                      "countryIso2": row["Country_Code"].strip().upper(),
                      "region": row["Region"].strip()}
            if iata in unique and unique[iata] != record:
                raise ValueError(f"Conflicting airport rows: {iata}")
            unique[iata] = record
    return [unique[key] for key in sorted(unique)]
```

- [ ] **Step 5: Run tests, copy the input, and commit**

Run: `python -m pytest tests/test_network.py -v`
Expected: PASS.

```bash
mkdir -p inputs
cp ../upload/Final_Flight_Numbers_DIL.csv inputs/Final_Flight_Numbers_DIL.csv
git add inputs src/crew_customs/network.py tests
git commit -m "feat: normalize destination network safely"
```

### Task 3: Define schemas and cross-record validation

**Files:**
- Create: `schemas/airport.schema.json`
- Create: `schemas/country.schema.json`
- Create: `schemas/source.schema.json`
- Create: `src/crew_customs/validate.py`
- Create: `tests/test_validation.py`

**Interfaces:**
- Produces: `validate_repository(root: Path, today: date) -> list[str]`; an empty list means valid.

- [ ] **Step 1: Write failing tests for source traceability, ISO/IATA format, and dates**

```python
from datetime import date
from crew_customs.validate import validate_repository

def test_verified_rule_requires_known_source(sample_repo):
    sample_repo.country["customs"]["prohibitedItems"] = [{"text": "Item X", "sourceIds": []}]
    errors = validate_repository(sample_repo.root, date(2026, 8, 25))
    assert "US: verified rule has no sourceIds" in errors

def test_review_due_cannot_precede_verified_date(sample_repo):
    sample_repo.country.update(lastVerified="2026-08-25", nextReviewDue="2026-08-24")
    assert any("nextReviewDue" in e for e in validate_repository(sample_repo.root, date(2026, 8, 25)))
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `python -m pytest tests/test_validation.py -v`
Expected: FAIL because validation is not implemented.

- [ ] **Step 3: Add JSON Schemas with exact required keys**

Define `airport.schema.json` to require `iataCode`, `airportName`, `city`, `countryIso2`, `reviewStatus`, and `lastVerified`; constrain IATA with `^[A-Z]{3}$` and ISO2 with `^[A-Z]{2}$`. Define `country.schema.json` to require the customs and baggage-security object keys from the approved specimen. Define every rule item as `{text: string, sourceIds: non-empty unique string array}` when `reviewStatus` is `verified`. Define sources with `id`, `authorityName`, `pageTitle`, `url`, `jurisdiction`, `accessedAt`, `fingerprint`, and `supportsFields`.

- [ ] **Step 4: Implement schema, reference, date, mapping, and approved-source validation**

```python
APPROVED_SOURCE_TYPES = {"customs", "government", "civil_aviation", "airport_authority", "embassy"}

def validate_repository(root: Path, today: date) -> list[str]:
    # Load schemas and YAML directories; collect jsonschema errors.
    # Reject duplicate IATA, ISO2, or source IDs.
    # Confirm airport countryIso2 resolves to a country file.
    # Confirm every sourceId resolves and uses an approved sourceType.
    # Parse lastVerified/nextReviewDue and enforce chronological order.
    # Return stable, sorted human-readable errors.
```

- [ ] **Step 5: Run validation tests and commit**

Run: `python -m pytest tests/test_validation.py -v`
Expected: PASS for valid fixtures and the exact expected errors for invalid fixtures.

```bash
git add schemas src/crew_customs/validate.py tests
git commit -m "feat: validate customs records and sources"
```

### Task 4: Compile the versioned public API

**Files:**
- Create: `src/crew_customs/compiler.py`
- Create: `tests/test_compiler.py`
- Generate: `public/api/v1/index.json`
- Generate: `public/api/v1/status.json`
- Generate: `public/api/v1/airports/*.json`
- Generate: `public/api/v1/countries/*.json`

**Interfaces:**
- Consumes: validated airport, country, and source records.
- Produces: `compile_api(root: Path, output: Path, built_at: datetime) -> dict[str, int]`.

- [ ] **Step 1: Write failing tests for inheritance, deterministic output, and privacy**

```python
def test_compiler_builds_self_contained_airport(sample_repo, tmp_path):
    compile_api(sample_repo.root, tmp_path, datetime(2026, 8, 25, tzinfo=timezone.utc))
    jfk = json.loads((tmp_path / "airports/JFK.json").read_text())
    assert jfk["country"]["iso2"] == "US"
    assert "handBaggage" in jfk["baggageSecurity"]
    serialized = json.dumps(jfk)
    for forbidden in ["Flight No.", "Related_Hotel", "Flag_Url", "SharePoint"]:
        assert forbidden not in serialized
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `python -m pytest tests/test_compiler.py -v`
Expected: FAIL because the compiler does not exist.

- [ ] **Step 3: Implement country inheritance and airport overrides**

```python
def compile_airport(airport: dict, country: dict, sources: dict[str, dict]) -> dict:
    return {
        "schemaVersion": "1.0.0", "iataCode": airport["iataCode"],
        "airportName": airport["airportName"], "city": airport["city"],
        "country": {"name": country["countryName"], "iso2": country["iso2"]},
        "customs": merge_rules(country["customs"], airport.get("customsOverrides", {})),
        "baggageSecurity": merge_rules(country["baggageSecurity"], airport.get("baggageSecurityOverrides", {})),
        "crewNotes": airport.get("crewNotes", country.get("crewNotes", [])),
        "sources": public_sources_for_record(airport, country, sources),
        "lastVerified": min(airport["lastVerified"], country["lastVerified"]),
        "nextReviewDue": min(airport["nextReviewDue"], country["nextReviewDue"]),
        "reviewStatus": combined_status(airport, country),
        "disclaimer": DISCLAIMER,
    }
```

- [ ] **Step 4: Generate index/status and enforce deterministic builds**

Sort airports by IATA, sources by ID, and rule arrays by their curated input order. Include `datasetVersion`, `builtAt`, counts, and oldest verification date in `status.json`. Compile to a temporary directory and replace the output only after all files succeed.

- [ ] **Step 5: Run all compiler/privacy tests and commit**

Run: `python -m pytest tests/test_compiler.py -v`
Expected: PASS.

```bash
git add src/crew_customs/compiler.py tests/test_compiler.py public/api/v1
git commit -m "feat: compile versioned static API"
```

### Task 5: Add CLI and complete network initialization

**Files:**
- Create: `src/crew_customs/cli.py`
- Create: `tests/test_cli.py`
- Generate: `data/airports/*.yaml`
- Create: `data/countries/*.yaml`

**Interfaces:**
- Produces commands: `crew-customs init-network`, `crew-customs validate`, `crew-customs build`, `crew-customs monitor`.

- [ ] **Step 1: Write CLI tests for exit codes**

```python
def test_validate_returns_nonzero_for_invalid_repo(invalid_repo):
    result = runner(["validate", "--root", str(invalid_repo)])
    assert result == 1

def test_build_refuses_invalid_data(invalid_repo):
    result = runner(["build", "--root", str(invalid_repo)])
    assert result == 1
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `python -m pytest tests/test_cli.py -v`
Expected: FAIL because the CLI does not exist.

- [ ] **Step 3: Implement argparse command routing**

Use `argparse` subcommands. `init-network` calls `normalize_network`, applies a committed `data/country_mapping.yaml`, and creates airport records with `reviewStatus: research_pending`. `validate` prints each error to stderr and returns 1 if any exist. `build` validates before compilation. `monitor` returns 1 only for an execution failure, not when changes are detected.

- [ ] **Step 4: Initialize all 96 destination airport records**

Run: `crew-customs init-network --csv inputs/Final_Flight_Numbers_DIL.csv --exclude AUH --root .`
Expected: 96 unique `data/airports/{IATA}.yaml` files; no flight, hotel, or SharePoint fields.

- [ ] **Step 5: Verify counts and commit**

Run: `crew-customs validate --root .`
Expected at this phase: only explicit `research_pending` records are accepted; malformed records still fail.

```bash
git add src/crew_customs/cli.py data tests/test_cli.py
git commit -m "feat: initialize airport research records"
```

### Task 6: Research, source, and review country batches

**Files:**
- Modify: `data/countries/*.yaml`
- Modify: `data/sources/*.yaml`
- Modify: `data/airports/*.yaml` only for airport-specific official rules
- Create: `docs/research-review.md`

**Interfaces:**
- Produces: reviewed YAML satisfying the schemas and source policy.

- [ ] **Step 1: Create the reviewer checklist**

The checklist requires: official authority identity; URL opened successfully; jurisdiction confirmed; every numeric threshold preserves currency/unit/age qualifiers; operating-crew scope explicitly supported or marked unavailable; hand/checked baggage location separated; effective date captured when available; second-person review recorded; and `lastVerified`/`nextReviewDue` set.

- [ ] **Step 2: Research countries in small reviewable batches**

Process 5–8 countries per commit. For each country, record only claims supported by approved official sources. If no crew-specific allowance is published, use a sourced note such as `No verified crew-specific allowance was identified in the listed official sources; contact the station or authority before carrying restricted goods.` Do not insert passenger allowances as crew allowances.

- [ ] **Step 3: Add airport-specific exceptions only when necessary**

Airport overrides require an official airport or competent-authority source and must cite the specific override field. National rules remain in the country record.

- [ ] **Step 4: Validate every batch before review**

Run: `crew-customs validate --root .`
Expected: PASS for reviewed records; remaining destinations may stay `research_pending` but are not labeled verified.

- [ ] **Step 5: Commit each approved batch**

```bash
git add data docs/research-review.md
git commit -m "data: verify customs rules for <country-batch>"
```

### Task 7: Implement safe weekly source monitoring

**Files:**
- Create: `src/crew_customs/monitor.py`
- Create: `tests/test_monitor.py`

**Interfaces:**
- Produces: `check_source(source: dict, fetch: Callable) -> CheckResult`; `build_issue(result: CheckResult, affected: list[str]) -> IssueDraft`; stable issue key `source-change:{source_id}:{new_fingerprint}`.

- [ ] **Step 1: Write tests for semantic normalization and issue deduplication**

```python
def test_markup_only_change_does_not_alert():
    old = "<main><p>Allowance: 2 items</p></main>"
    new = "<main class='red'><p>Allowance: 2 items</p></main>"
    assert fingerprint(old) == fingerprint(new)

def test_changed_text_has_stable_issue_key():
    result = changed_result("us-cbp", "old", "new")
    assert build_issue(result, ["JFK"])["key"].startswith("source-change:us-cbp:")
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `python -m pytest tests/test_monitor.py -v`
Expected: FAIL because monitoring is not implemented.

- [ ] **Step 3: Implement bounded fetching and fingerprinting**

Use `urllib.request` with a descriptive user agent, HTTPS-only redirects, 20-second timeout, maximum 5 redirects, and maximum 5 MiB response size. Normalize visible text by stripping scripts/styles/tags, decoding entities, and collapsing whitespace. Hash normalized UTF-8 text with SHA-256.

- [ ] **Step 4: Implement issue drafts and consecutive-failure behavior**

Issue drafts contain authority, URL, affected countries/airports, prior/current hashes, bounded text diff, potentially supported fields, and the exact reviewer checklist. Store monitor state as a workflow artifact/cache file. Require three consecutive fetch failures before drafting an outage issue. Search open issues by stable source label/key before creating one; update the existing issue instead of duplicating it.

- [ ] **Step 5: Run monitoring tests and commit**

Run: `python -m pytest tests/test_monitor.py -v`
Expected: PASS.

```bash
git add src/crew_customs/monitor.py tests/test_monitor.py
git commit -m "feat: detect official source changes safely"
```

### Task 8: Configure CI, GitHub Pages, and weekly review issues

**Files:**
- Create: `.github/workflows/ci-pages.yml`
- Create: `.github/workflows/monitor-sources.yml`
- Create: `README.md`

**Interfaces:**
- Consumes: CLI commands and generated `public/` directory.
- Produces: deployed Pages artifact and review issues labeled `source-change`.

- [ ] **Step 1: Add CI and Pages workflow**

Trigger on pull requests and pushes to `main`. Default permissions are `contents: read`. Install `.[test]`, run `pytest`, run `crew-customs validate`, run `crew-customs build`, and fail if `git diff --exit-code public` detects uncommitted generated changes. On `main`, upload `public/` with `actions/upload-pages-artifact` and deploy using a separate job with only `pages: write` and `id-token: write`.

- [ ] **Step 2: Add weekly monitoring workflow**

Use `schedule: cron: '17 3 * * 1'` and `workflow_dispatch`. Grant only `contents: read` and `issues: write`. Run the monitor, then create/update issues using `gh issue list/create/comment`; never commit or open an automatic data pull request.

- [ ] **Step 3: Document API consumption and fallback behavior**

README example:

```javascript
const response = await fetch(`${API_BASE}/api/v1/airports/${iata.toUpperCase()}.json`);
if (!response.ok) return { available: false, message: "Station customs information is not yet available." };
const station = await response.json();
```

Document caching via `status.json`, `research_pending`, visible last-verified date, and the mandatory disclaimer.

- [ ] **Step 4: Run the complete local release gate**

Run:

```bash
python -m pytest -v
crew-customs validate --root .
crew-customs build --root . --output public/api/v1
git diff --check
```

Expected: all tests PASS; validation exits 0; the build completes; no whitespace errors.

- [ ] **Step 5: Commit deployment configuration**

```bash
git add .github README.md public
git commit -m "ci: deploy customs API and monitor sources"
```

### Task 9: GitHub publication and production smoke test

**Files:**
- Modify: repository settings only (GitHub Pages source: GitHub Actions)

**Interfaces:**
- Produces: public base URL `https://<owner>.github.io/<repo>/api/v1/`.

- [ ] **Step 1: Create or select the target GitHub repository**

Use a public repository with default branch `main`. Push the complete local history without rewriting unrelated remote history.

- [ ] **Step 2: Enable GitHub Pages through GitHub Actions**

Set Pages build/deployment source to GitHub Actions and confirm the environment protection settings allow the deploy job.

- [ ] **Step 3: Run production endpoint smoke tests**

```bash
curl --fail --silent https://<owner>.github.io/<repo>/api/v1/status.json
curl --fail --silent https://<owner>.github.io/<repo>/api/v1/index.json
curl --fail --silent https://<owner>.github.io/<repo>/api/v1/airports/JFK.json
```

Expected: HTTP 200, valid JSON, matching schema/dataset versions, and no forbidden operational fields.

- [ ] **Step 4: Test missing-destination behavior**

Run: `curl --silent --output /dev/null --write-out '%{http_code}' https://<owner>.github.io/<repo>/api/v1/airports/ZZZ.json`
Expected: `404`; the SaaS handles this using the documented unavailable message.

- [ ] **Step 5: Record the production URL and commit documentation**

```bash
git add README.md
git commit -m "docs: record production API endpoint"
git push origin main
```

## Plan self-review result

- Spec coverage: repository structure, normalization, privacy, schemas, compilation, sourcing, review, monitoring, validation, GitHub Pages, and client fallback are each assigned to a task.
- Scope boundary: airline-controlled onboard procedures and unapproved dangerous-goods content remain excluded.
- Type consistency: the loader, normalizer, validator, compiler, monitor, and CLI interfaces are defined once and consumed by later tasks under the same names.
- Placeholder scan: angle-bracket values occur only where the GitHub owner/repository or commit batch name cannot exist until publication; they are explicit execution inputs rather than unfinished implementation requirements.

