# Runbook: Onboarding a State Compliance Profile

Onboarding a county makes its data readable. Onboarding a **state** makes its cases
workable. Until a state's profile is filled in and verified, every case in that state is
reported ineligible with the reason named — including states whose counties parse
perfectly. That is the intended behaviour, not a bug to route around.

This runbook is the only correct way to bring a state online.

## Before anything else

**Nothing in a state profile may be guessed.** Not from a neighbouring state, not from what
"most states" do, not from a summary article, not from another vendor's marketing page, and
not from a language model — including the one that wrote this system. Surplus recovery is
regulated state by state and the variation between states is the entire point of the file.

The two errors are not symmetric:

| Error | Cost |
|---|---|
| Wrongly holding a state back | Leads sit idle. Visible in `surplusai compliance states`. Recoverable. |
| Wrongly clearing a state | Contacting a former owner during a statutory blackout, or charging above a cap. Voids contracts; in several states charging over the cap is a criminal offence. |

Leaving a value `null` is safe. Filling one in without a citation is not.

## 1. See where the state stands

```bash
surplusai compliance states
surplusai compliance validate MD
```

`states` lists every file and says how many are usable. `validate` names exactly what one
state still needs. A state with no file at all is reported by `check`:

```bash
surplusai compliance check TX --sale-date 2024-03-01
#   [no_state_rules] No compliance rules exist for TX...
```

## 2. Create the file

```bash
cp config/compliance/states/_template.yaml config/compliance/states/tx.yaml
```

The filename must be the lower-case two-letter code and must match `state_code` inside the
file. The loader rejects a mismatch rather than silently reading Texas rules under a Tennessee
heading.

## 3. Research each value against the statute

Read the statute itself, or have counsel read it. Record the citation as you go — a value
without a citation is indistinguishable from a guess six months later, which is why the
system treats a verified file with no `statute_citations` as incomplete.

The four values that gate usability:

| Field | Meaning | Notes |
|---|---|---|
| `verified` | A person has checked every value below | Set last, never first |
| `waiting_period_days` | Days after the sale before a former owner may be contacted | `0` is a real answer meaning "no wait". `null` means unknown and holds the state |
| fee cap | `max_contingency_fee_pct` or `max_flat_fee_amount`, per `fee_cap_basis` | See below |
| `statute_citations` | Where each value came from | At least one required |

`fee_cap_basis` deserves care because two of its values look similar and mean opposite
things:

- `percentage_of_recovery` — the cap is a percentage; set `max_contingency_fee_pct`.
- `flat_amount` — the cap is a fixed sum; set `max_flat_fee_amount`.
- `none` — **the state caps nothing, and you have confirmed that.** This is a positive
  finding from the statute, not a way to record "I could not find a cap". If you did not
  find the answer, leave `fee_cap_basis` as it is and leave the amount `null`.

The remaining fields are optional in the sense that they do not block a state, but each one
left `null` is a fact the system will not enforce:

- `claim_deadline_days` / `escheatment_period_days` — after these pass, the engine blocks the
  case as unclaimable rather than sending the team after money that is gone.
- `requires_locator_license` — when true, **every** case in the state is blocked until you
  confirm the licence is held. This is deliberately blunt; unlicensed practice is the fastest
  way to lose the business.
- `requires_written_contract`, `requires_notarized_contract`, `prohibits_assignment_of_claim`,
  `cooling_off_days` — surfaced as contract requirements on every cleared case.

### Disclosures are copied, never written

`required_disclosures` is reproduced **verbatim** on every case in the state. Statutes
frequently prescribe disclosure wording exactly, and a paraphrase of prescribed wording is
not the prescribed wording. Paste the statutory text; the system supplies no language of its
own and will never improve on what you put there.

## 4. Verify

Only when every value above has been checked against the cited statute:

```yaml
verified: true
verified_by: "A. Counsel"
verified_on: 2026-08-02
statute_citations:
  - "Tex. Prop. Code § 76.001"
```

The loader refuses a file marked `verified: true` that still has gaps — verification means
every value was checked, so a verified file with holes in it is a contradiction and is
rejected at load time rather than trusted.

## 5. Confirm it went live

```bash
surplusai compliance validate TX
#   usable: True

surplusai compliance check TX --sale-date 2024-03-01 --amount 12500.00
```

`check` reports eligibility, the earliest lawful contact date, the maximum fee at that
recovery amount, the contract requirements, and every required disclosure. If it still
reports a blocking reason, the reason names the missing piece.

## 6. Tell the corpus

`tests/unit/compliance/test_compliance.py` contains
`test_shipped_states_are_present_but_unverified`, which fails the moment any shipped state
gains a statutory value. **This failure is the point.** It exists so that nobody can add a
number quietly; when you bring a state online legitimately, update that test to record which
states are now verified and by whom. Do not delete it.

## What must never be done

- **Do not fill in a value you did not read in the statute.** A plausible number is worse
  than a null, because a null blocks and a plausible number clears.
- **Do not copy a neighbouring state's file.** The values differ between states in ways that
  are not predictable from geography, and a copied file carries a citation that does not
  cover it.
- **Do not set `verified: true` to unblock a demo.** Every case in the state is cleared
  against whatever is in the file at that moment.
- **Do not use `fee_cap_basis: none` to mean "unknown".** It means the state caps nothing,
  and it will clear any fee you quote.
- **Do not paraphrase a required disclosure.** Prescribed wording is prescribed.
- **Do not weaken the fail-closed default in code** to get a state working. If a state cannot
  be brought online through its YAML file, that is a gap in the schema worth fixing for every
  state — not a reason to make "unknown" mean "permitted".
