# Phase 4: The Compliance Engine

## What this phase is for

Phases 2 and 3 answer *what the county published* and *who the owner is*. Phase 4 answers a
different kind of question: **may this case lawfully be worked, and on what terms?**

That difference drives every decision below. The parser's job is to report faithfully what a
document says; if it is unsure, the row is queued and a person looks at it. The compliance
engine's job is to decide something with legal consequences, and its uncertainty cannot be
handed to a queue and forgotten — an unanswered compliance question that leaks through as
"proceed" is a call placed during a statutory blackout, or a fee agreed above a state's cap.

## The shipped state of this phase

**No state in this repository can currently clear a case.** `config/compliance/states/md.yaml`
and `in.yaml` exist, carry the state's name, and have every statutory value set to `null`
with `verified: false`.

This is deliberate and it is the most important thing in this document.

The brief asked for a compliance engine. It did not supply the statutes, and the statutes are
not something to infer. Maryland's waiting period is a fact about Maryland law; it is not
derivable from the corpus, from the other states, from general knowledge of how surplus
recovery usually works, or from a language model's recollection of a statute it may or may not
have read. A plausible-looking number in `waiting_period_days` is worse than an empty one,
because an empty one blocks the case and a plausible one clears it.

So the engine is complete and the data is empty, and the empty data is the honest position.
`docs/runbooks/onboarding_a_state_compliance_profile.md` is the path from here to a working
state.

`tests/unit/compliance/test_compliance.py::test_shipped_states_are_present_but_unverified`
fails the moment either shipped file gains a statutory value. That failure is a feature: it
forces whoever adds a number to state where it came from.

## Fail-closed, and why the asymmetry is the design

`ComplianceResult.is_eligible` defaults to `False`. Every path that lacks a fact returns
ineligible with a coded reason:

| Code | Raised when |
|---|---|
| `no_state_rules` | No file for the state |
| `rules_unverified` | A file exists but nobody has checked it |
| `rules_incomplete` | Verified, but a required value is still unknown |
| `no_sale_date` | Nothing to count the waiting period from |
| `waiting_period` | The statutory blackout has not elapsed |
| `claim_deadline_passed` | The money can no longer be claimed |
| `escheated` | The money has passed to the state |
| `licence_required` | The state requires a locator licence |

The costs of the two possible errors are not comparable:

- **Wrongly blocking** costs one lead, is visible in the blocking counts, and is recovered by
  filling in a value.
- **Wrongly clearing** can mean contacting a former owner during a statutory blackout or
  agreeing a fee above a cap. Both void contracts, and in several states charging over the cap
  is a criminal matter.

Because the costs differ by orders of magnitude, the code never guesses in the permissive
direction — not once, not as a default, not "just for now". This is the same reasoning as the
parser's surplus rule, applied to a domain where the downside is legal rather than commercial.

### Unknown is not permissive

Three specific traps this design avoids:

**`waiting_period_days: null` does not mean "contact immediately".** It means "we do not know
when contact becomes lawful", and it holds every case in the state. `0` is the way to say
there is no wait, and it is a real answer that must come from the statute like any other.

**`fee_cap_basis: none` does not mean "unknown".** It is a positive finding that the state caps
nothing, and it makes the state usable. Recording "I could not find a cap" this way would clear
any fee the business chose to quote, so the two are kept strictly distinct: `none` is an answer,
a `null` amount under `percentage_of_recovery` is a gap.

**`maximum_fee_for()` returns `None` to mean "cannot quote", never "no limit".** A caller that
treats a missing cap as an absent cap inverts the whole engine, so the function refuses rather
than returning a large number.

### Verified is a gate, not a label

A file can be written, reviewed, committed and read while carrying `verified: false`; the
engine will still refuse every case under it. `verified: true` asserts that a person checked
every value against the cited statute, so the loader **rejects** a file marked verified that
still has gaps — a verified file with holes is a contradiction, and catching it at load time is
better than trusting it at decision time.

`statute_citations` is required for usability for the same reason. A value with no citation is
indistinguishable from a guess once the person who entered it has moved on.

`is_usable` is defined as `not missing_fields()` rather than as its own list of checks. Two
independent lists would eventually disagree, and the failure mode of that disagreement — a
state reported usable while `missing_fields()` still named a gap — is exactly the bug this
phase exists to prevent.

## Every reason is reported, not just the first

`evaluate()` collects all applicable blocking reasons rather than returning at the first. A
case can be inside the waiting period *and* past its claim deadline *and* in a state requiring
a licence; a reviewer who fixes one and re-runs, three times over, learns the system is wasting
their time. The engine says everything it knows in one pass.

## Disclosures are reproduced, never composed

`required_disclosures()` returns the configured strings verbatim. Statutes frequently prescribe
disclosure wording exactly, and a paraphrase of prescribed wording is not the prescribed
wording. The system contributes no language of its own here — if the config is empty, the
result is empty, and the gap is visible rather than papered over with something plausible.

## A missing state is not an exception

`ComplianceRulesLoader.load_or_none()` returns `None` for a state with no file, and the engine
turns that into a `no_state_rules` blocking reason. Batches span counties across many states,
and one unwritten file must not abort the run: the other states still evaluate, and the missing
one is reported honestly and held back. `load()` still raises for callers that genuinely
require a state — the CLI's `validate` uses it — so the strict path exists where it belongs.

## Configuration, not code

There is no state-specific branching anywhere in `surplus_ai/compliance/`. Adding a state is
adding a YAML file, in the same way adding a county is adding a YAML file. The schema in
`_template.yaml` is the contract; if a state cannot be expressed in it, that is a gap in the
schema to fix for every state, never a reason to special-case one in Python.

## What is deliberately not here

- **Legal advice.** The engine enforces recorded parameters. It does not interpret statutes,
  and a filled-in file is only as good as the person who filled it in.
- **Federal or municipal layers.** Local rules exist in some jurisdictions. The schema is
  per-state because that is where the regulation the business actually faces sits; a municipal
  layer would be an additional file, not a change to this one.
- **Automatic statute updates.** Statutes change. `verified_on` records when a file was last
  checked so a review cadence can be built on it, but nothing here watches for amendments.
- **Persistence of compliance decisions.** Results are computed on demand. Storing them
  belongs with lead creation in Phase 5, where there is a lead to attach them to.

## Files

```
surplus_ai/compliance/
    exceptions.py       ComplianceError and its subclasses
    state_rules.py      StateComplianceRules, FeeCapBasis, is_usable / missing_fields
    rules_loader.py     YAML loading, validation, load_all / verified_states
    waiting_period.py   earliest contact date, elapsed check, days remaining
    fee_cap.py          cap validation and maximum-fee quoting
    disclosure.py       verbatim disclosures, contract requirements
    engine.py           ComplianceCase, BlockingReason, ComplianceResult, ComplianceEngine
config/compliance/states/
    _template.yaml      documented schema; the only place to start a new state
    md.yaml, in.yaml    the corpus states — present, empty, unverified
surplus_ai/cli/commands/compliance.py
                        surplusai compliance states / validate / check
```

47 tests in `tests/unit/compliance/test_compliance.py`, weighted towards proving refusal:
that unknown states block, that unverified files block, that each required fact is
independently required, that a batch spanning unknown states still completes, that the CLI
exits non-zero on every refusal rather than reporting a blocked state cheerfully, and that
the states shipped today clear nothing.
