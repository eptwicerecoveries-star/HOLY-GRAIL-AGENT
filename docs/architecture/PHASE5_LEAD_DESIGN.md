# Phase 5: Lead Creation

## What this phase is for

Phases 2 to 4 each answer one question about a row: what does it say, who is the owner, may
this be worked. Phase 5 is where those answers become the objects the business runs on --
`counties`, `surplus_cases`, `properties`, `owners`, `compliance_evaluations`, `leads` --
and where, for the first time, the system decides something rather than reporting it.

## The distinction the whole phase turns on

**Cases and owners are records of fact. Leads are decisions.**

A case is written for every row a county published, whatever the row says. A row with no
surplus, an unreadable owner, or a figure of zero all become cases. That is not
completeness for its own sake: a row that produced no record is a row nobody can account
for later, and "we never saw it" and "we saw it and it was empty" have to be
distinguishable when someone asks why a parcel is missing.

A lead says a person should be contacted. It is written only when four independent
conditions hold at once:

| # | Condition | Rejection code |
|---|---|---|
| 1 | A surplus figure exists **and** is above zero | `no_surplus` / `surplus_not_claimable` |
| 2 | An owner was published, of a kind the business pursues | `no_owner` / `owner_not_pursuable` |
| 3 | The row was auto-accepted by the parser | `needs_review` |
| 4 | The state's compliance rules positively permit contact | `compliance_blocked` |

Each is checked independently and each alone withholds the lead. The tests exercise all
six codes separately, because a gate that only ever fails for one reason is a gate that has
not been tested.

### Condition 1 is where the surplus rule survives contact with a database

`surplus_amount` is copied exactly as the interpreter resolved it, including the `None` that
means "this county published no surplus". Nothing in this phase fills that in from a winning
bid, a sale amount or an assessment. Doing so would undo the rule the parser is most careful
about, one layer further down where it would be much harder to notice.

The two failure codes are kept apart deliberately. `no_surplus` means the county said
nothing; `surplus_not_claimable` means the county said zero, which is a real figure -- a
fully refunded overbid genuinely reads $0.00 and the county holds nothing. Collapsing them
would hide how much of a county's list has already been paid out.

### Condition 4 blocks everything today

No state's statutes have been recorded (see `PHASE4_COMPLIANCE_DESIGN.md`), so every case is
held at the compliance gate and **the corpus produces zero leads**. That is the fail-closed
design working, not a defect in this phase, and the report says so in as many words rather
than printing a bare `0`.

`LeadBuilder.promote` is the other half of that bargain. When a state is brought online, it
re-checks stored cases against the rules as they now stand and creates the leads that now
pass -- without re-parsing a single PDF. Cases rejected for a reason a statute cannot change
(no surplus, a company owner) are filtered out in SQL rather than re-examined. Without
promotion, failing closed would mean losing the backlog; with it, failing closed costs
nothing but time.

## Case identity

`surplus_cases` is unique on `(county_id, dedupe_hash)`. The hash is built from what the
county actually published, in this order:

1. the first published identifier among parcel, case number, certificate number, unique id;
2. narrowed by the sale date where one is given -- the same parcel can go to sale in more
   than one year, and those are different cases over different money;
3. failing all of that, the verbatim row contents.

The fallback is weaker and is recorded as such (`is_positional`). It means a republication
with a corrected spelling reads as a new case rather than the same one. The alternative --
matching on owner name and amount -- would merge two neighbours who happen to be owed the
same figure. Splitting one case in two is a duplicate someone can spot; merging two people's
claims is a wrong payment, so the failure modes are not equally acceptable.

**One owner row per published owner cell, not per human.** Counties routinely publish
`SMITH JOHN & MARY` in a single field. Splitting it would invent a party boundary the county
never drew, and the halves would carry names nobody published. The classifier keeps the
additional parties it can see alongside the split name, so nothing is lost by leaving the
cell whole.

## Why `case_number` became nullable

It was `NOT NULL`. None of the corpus counties publish a case number -- they publish parcel
or account numbers -- so satisfying the constraint meant synthesising an identifier, which a
reader would reasonably take for the county's own. The column is now nullable and identity
lives in `dedupe_hash`, which exists for the purpose.

The downgrade refuses to run while any row holds a NULL, rather than filling one in.

## Counties are registered, not assumed

Every case needs a county and nothing created one before this phase. `CountyRegistry`
find-or-creates from state and slug **together** -- never from a name, because several states
have a Washington County and one shared row would merge unrelated cases into one county's
figures.

Registration does not overwrite. Once a county exists, later runs reuse it rather than
rewriting its details from whatever config happens to be on disk, so a stale local file
cannot silently redescribe a county already in use. `update_from_config` makes that change
deliberately and reports which fields moved.

Registration details (`fips_code`, `source_type`, `source_url`, `publishing_frequency`) were
added to the existing `config/counties/<state>/<slug>.yaml` rather than a second file. It is
the same county; two files describing it would be one more thing to keep in step. A typo in
an enum value is rejected rather than defaulted -- describing an API feed as a manual upload
would be a quiet lie about where the data came from.

## Every case gets a compliance verdict, including the refused ones

A blocked case with no stored reason is indistinguishable from a case nobody looked at, and
the difference matters: the first is the system working, the second is work not done. So
`ComplianceEvaluation` is written for every case, carrying the coded blocking reasons in its
notes.

Each evaluation records `state_rule_version`, a hash of the statutory values that produced
it, so a verdict reached under one reading of a statute is still legible after the file is
corrected. The hash covers only values that change a verdict -- not who verified the file or
when -- so re-checking a file and finding it unchanged does not make every past evaluation
look differently decided. A state with no file at all records `absent`, which is more useful
than a blank.

## Idempotency

Running the same document twice updates the same cases, writes no second owner, and leaves
the lead count unchanged. Counties republish the same list constantly; a republished list is
not new work, and a work queue that doubled on every republication would be unusable within
a month.

Where a county republishes with a **correction**, the corrected figure wins on the case. The
verbatim original is never touched -- it lives in `raw_surplus_rows`, which remains the
immutable record of what was published.

## What is deliberately not here

- **Contacts, phones and addresses.** Enrichment is its own phase and needs external
  providers. A lead today carries the case and the owner, nothing more.
- **Scoring.** `leads.score` stays null. Ranking leads before any exist would be ranking an
  empty set.
- **Multiple owners per lead.** A lead points at one principal owner, chosen as the most
  confidently classified pursuable owner on the case. Every owner row is still stored.
- **Ingestion jobs.** Cases can be built without one; wiring the job record belongs with the
  orchestrator phase.

## Files

```
surplus_ai/leads/
    exceptions.py           LeadError, CountyRegistrationError
    county_registry.py      find-or-create, validation, deliberate updates
    dedupe.py               case_identity(), the identifier cascade and its fallback
    case_builder.py         SurplusCase + Property, money columns kept apart
    owner_builder.py        one owner per published cell, classified
    compliance_recorder.py  an evaluation for every case, with the rule version
    lead_builder.py         the four-part gate, the funnel report, promote()
    pipeline.py             LeadPipeline, LeadInventory
surplus_ai/cli/commands/leads.py   surplusai leads build / promote / status
migration a96d6f5432c0            case_number nullable
```

53 tests in `tests/unit/leads/test_leads.py`, including the real Harford funnel: 49 rows →
49 cases → 14 rejected for a company owner → 35 held at compliance → 0 leads.
