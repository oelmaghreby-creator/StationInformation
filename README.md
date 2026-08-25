# Crew Destination Customs API

A versioned, static JSON API for operating crew destination customs and
baggage-security information. It publishes only reviewed destination data; it
does not contain rosters, flights, hotels, staff details, or other operational
data.

## Use the API

The intended public API base is
`https://oelmaghreby-creator.github.io/StationInformation/api/v1/`. Request an
airport document by its uppercase IATA code:

```javascript
const API_BASE = "https://oelmaghreby-creator.github.io/StationInformation/api/v1";
const response = await fetch(`${API_BASE}/airports/${iata.toUpperCase()}.json`);
if (!response.ok) return { available: false, message: "Station customs information is not yet available." };
const station = await response.json();
```

Treat a failed response as unavailable data, not as permission to reuse a
different airport or passenger allowance. The airport index is available at
`https://oelmaghreby-creator.github.io/StationInformation/api/v1/index.json`;
country documents are available under `/countries/{ISO2}.json` relative to the
API base.

Each airport response keeps hand baggage distinct from `checkedCargoBag`,
includes its contributing official sources, and contains this mandatory
disclaimer:

> Verify current requirements with the relevant authority and airline
> procedures before travel.

## Freshness, caching, and fallbacks

Fetch `/api/v1/status.json` before serving cached station data. Cache its
`datasetVersion` and `builtAt` together with the station response; invalidate
or revalidate your station cache when either value changes. Show the response's
`lastVerified` date prominently. A `null` date means the station is not
verified and should be presented as unavailable or pending review.

`research_pending` means no verified crew-specific rule is published for that
station or jurisdiction yet. It is not a passenger-rule fallback and must not
be represented as a verified allowance. `needs_review` is similarly not a
substitute for current authority confirmation. Always keep the disclaimer
visible with any cached or live result.

## Local development

Use Python 3.12 and install the project with its test dependencies:

```bash
python -m pip install '.[test]'
python -m pytest -v
crew-customs validate --root .
crew-customs build --root . --output public/api/v1
crew-customs release-check --root .
```

The raw network CSV is private operational input. Keep it outside this
checkout (the repository ignores `inputs/`) and pass its secure local path
explicitly when initializing identities:

```bash
crew-customs init-network --csv /secure/path/Final_Flight_Numbers_DIL.csv --root .
```

`init-network` retains only airport/city/country identity fields and writes
research-pending records; it does not copy flight, hotel, SharePoint, or other
operational columns. Once records have been initialized, `validate`, `build`,
and `release-check` work without the CSV. `release-check` reads
`release-manifest.json`, rejects any `inputs/` directory in the release
checkout, and scans the Pages snapshot for forbidden operational content.

## Public publication: snapshot only

Do **not** push, merge, mirror, or otherwise preserve this repository's normal
Git history in `oelmaghreby-creator/StationInformation`. Although the current
tree is safe, its reachable ancestry contains private operational input.
`crew-customs release-check --root .` therefore fails intentionally with
`snapshot-only publication required` in this repository.

Create the reviewed, deterministic tracked-HEAD export instead:

```bash
crew-customs release-snapshot --root . --output ../StationInformation-head.tar
```

This command uses `git archive HEAD`; it never archives the working directory,
so ignored files such as `.superpowers/` cannot enter the release. Extract the
archive into a new staging directory, review it, initialize a new **parentless**
`main` commit, and push only that new commit to the empty public repository.
Do not add the original repository as a remote or use a normal history push.

With a declared `builtAt` value, the build is deterministic. CI validates every
source change, rebuilds `public/`, commits changed generated API files on
`main`, and then deploys GitHub Pages. Pull requests remain read-only. Source
monitoring runs daily and creates or comments on exact-key `source-change`
review issues. It never changes source YAML or verified rules automatically.

For a reproducible release build, use the committed UTC ISO 8601 timestamp
from the CI workflow with either `--built-at` or `SOURCE_DATE_EPOCH`:

```bash
SOURCE_DATE_EPOCH=2026-08-25T00:00:00Z \
  crew-customs build --root . --output public/api/v1
# Equivalent: crew-customs build --built-at 2026-08-25T00:00:00Z ...
```

Both values must be UTC ISO 8601 timestamps. Keep the workflow value and the
committed `status.json` `builtAt` value in sync whenever publishing a new API
release, so CI can detect every generated-file change.
