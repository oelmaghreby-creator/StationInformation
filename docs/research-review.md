# Research and review log

This file records the human review gate for country and airport research. A
record marked `needs_review` has been researched against official sources but
must not be promoted to `verified` until an independent second person completes
the checklist and records their name and review date here.

## Reviewer checklist

For every record promoted to `verified`, the reviewer must confirm all items:

- [ ] The named authority is the competent official authority for the rule.
- [ ] Every cited URL opened successfully on the recorded access date.
- [ ] The page jurisdiction and any sub-jurisdiction or route scope are explicit.
- [ ] Every numeric threshold preserves its currency, unit, age, duration, and
      other eligibility qualifiers.
- [ ] Operating-crew scope is explicit in the source, or the record says that a
      verified crew-specific allowance is unavailable; passenger allowances are
      never silently reused as crew allowances.
- [ ] Customs rules are separate from aviation-security rules, and hand baggage
      is separate from checked crew baggage (the cargo bag).
- [ ] The rule's effective date is recorded when the authority publishes one.
- [ ] An independent second-person reviewer name and review date are recorded.
- [ ] `lastVerified` and `nextReviewDue` are set for reviewed records; records
      still at `research_pending` keep both values null.

## Country batch: AU, CA, GB, IN, SA, SG, TH, US

Research performed: 2026-08-25. Next review due for `needs_review` records:
2026-11-25. Independent second-person review: **pending**.

| Record | Status | Crew scope and effective-date result | Second-person review |
| --- | --- | --- | --- |
| AU | `needs_review` | ABF explicitly states the AUD450 general-goods crew limit and that the 2.25-litre alcohol concession applies to crew aged 18 or over. No effective date is stated on the page. | Pending |
| CA | `research_pending` | The registered authority-contact source supplies no crew allowance. Passenger figures were not copied. | Not eligible until research is resolved |
| GB | `needs_review` | HMRC explicitly applies passenger personal-allowance rules to international aircrew. Numeric rules are limited to Great Britain; no page-level effective date is stated. | Pending |
| IN | `needs_review` | Baggage Rules, 2026 explicitly allow non-final-pay-off crew gift articles up to ₹2,500; final-pay-off crew fall under the Rules. No commencement date is asserted because the registered official pages do not visibly state one. | Pending |
| SA | `research_pending` | The registered ZATCA page is traveller-facing and supplies no operating-crew allowance. Passenger figures were not copied. | Not eligible until research is resolved |
| SG | `needs_review` | Singapore Customs publishes bona fide crew liquor options and excludes crew from GST import relief for newly acquired goods. No separate effective date is stated; page last updated 2026-03-25. | Pending |
| TH | `research_pending` | The registered Thai Customs page is passenger-facing and supplies no operating-crew allowance. Passenger figures were not copied. | Not eligible until research is resolved |
| US | `research_pending` | The registered government authority-contact page supplies no crew allowance. Passenger figures were not copied. | Not eligible until research is resolved |

### Official-source verification ledger

Each URL below returned HTTP 200 on 2026-08-25. Fingerprints are SHA-256 over
visible text after script/style removal, HTML entity decoding, and whitespace
collapse. `supportsFields` is recorded in the corresponding source YAML.

| Source ID | Official URL | Supported fields |
| --- | --- | --- |
| `au-abf-duty-free-crew` | https://www.abf.gov.au/entering-and-leaving-australia/duty-free | `crewNotes`, `customs.alcohol.rules`, `customs.declarationRequirements` |
| `ca-cbsa-authority-contact` | https://www.cbsa-asfc.gc.ca/contact/bis-sif-eng.html | `crewNotes` (authority-contact fallback; no allowance) |
| `gb-border-force-travelling` | https://www.gov.uk/government/publications/travelling-to-the-uk/travelling-to-the-uk | `crewNotes`, `customs.cashDeclaration.rules`, `customs.alcohol.rules`, `customs.tobacco.rules` |
| `gb-hmrc-crew-rules` | https://www.gov.uk/government/publications/hmrc-brexit-transition-communications-resources/travellers-communication-pack-plain-text | `crewNotes`, `customs.cashDeclaration.rules`, `customs.alcohol.rules`, `customs.tobacco.rules`, `customs.declarationRequirements` |
| `in-mumbai-customs-crew-2026` | https://mumbaicustomszone3.gov.in/aarrival-passenger-guidelines | `crewNotes` |
| `in-pib-baggage-2026` | https://www.pib.gov.in/PressReleasePage.aspx?PRID=2222384 | `crewNotes` |
| `sa-zatca-traveller-luggage` | https://zatca.gov.sa/en/RulesRegulations/Taxes/Pages/customs-individual/Travel-pages/luggage.aspx | `crewNotes` (research-gap note only; passenger allowance not used) |
| `sg-customs-crew-concessions` | https://www.customs.gov.sg/at-customs/arriving-in-singapore/duty-free-concession-gst-relief/ | `crewNotes`, `customs.alcohol.rules`, `customs.tobacco.rules`, `customs.declarationRequirements` |
| `th-customs-arriving-passengers` | https://www.customs.go.th/list_strc_simple_neted.php?ini_content=individual_160503_03_160905_01&lang=en&left_menu=menu_individual_submenu_01_160421_01 | `crewNotes` (research-gap note only; passenger allowance not used) |
| `us-cbp-authority-contact` | https://www.usa.gov/agencies/u-s-customs-and-border-protection | `crewNotes` (authority-contact fallback; no allowance) |

## Airport override: SIN

Research performed: 2026-08-25. Next review due: 2026-11-25. Independent
second-person review: **pending**. Status: `needs_review`.

This is an airport aviation-security override for departures from Singapore
Changi Airport, not a Singapore customs rule and not an arrival rule. The Changi
Airport Group page states that, from 15 April 2026 at 00:01 Singapore time, a
person departing from Changi may carry at most two power banks, only in hand
baggage. Units up to 100Wh are allowed; units over 100Wh through 160Wh require
carrier approval; units over 160Wh are prohibited. Checked-baggage carriage is
prohibited. The FAQ explicitly says there are no group exceptions, including
for crew, for these departures.

| Source ID | Official URL | Supported fields |
| --- | --- | --- |
| `sin-changi-power-banks-2026` | https://www.changiairport.com/en/fly/security-and-baggage-restrictions.html | `crewNotes`, `baggageSecurityOverrides.handBaggage.batteriesAndElectronics`, `baggageSecurityOverrides.checkedCargoBag.batteriesAndElectronics` |

### Open review leads

- US CBP crew guidance returned HTTP 403 to the bounded verifier. Search indexing
  indicates a current crew-exemption page, so an independent reviewer must open
  and reconcile it before US can move beyond `research_pending`.
- CBSA form BSF552 appears to describe crew effects and exemptions. It was not
  accepted into the reviewed dataset because the approved CA disposition is
  `research_pending`; an independent reviewer must reconcile it before changing
  the CA note or status.
