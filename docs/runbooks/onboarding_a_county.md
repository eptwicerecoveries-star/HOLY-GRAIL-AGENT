# Runbook: Onboarding a County

Adding a county is a configuration task. It never requires changing parser code, and this
runbook is the check on that claim: if you find yourself editing Python to onboard a
county, the generic path has a gap worth fixing instead.

## 1. Look at the document

```bash
surplusai parser classify path/to/county.pdf
```

- `pdf_type: searchable` and `ocr_required: False` — proceed.
- `ocr_required: True` — the data is a raster image and will be read by OCR. Every row from
  such a document is routed to human review regardless of score, so expect to check the
  figures before working them. If `tesseract-ocr` and `poppler-utils` are not installed,
  parsing refuses with `OCRRequiredError` rather than returning the surrounding page text
  as if it were data.

## 2. Check what was extracted

```bash
surplusai parser inspect path/to/county.pdf --rows 5
```

Confirm the row count looks right, the headers match what the PDF shows, and the sample
rows line up with their columns. If the document states its own record count anywhere,
check it — that is the strongest signal available.

If the row count is wrong, look at `strategy` and `scores` in the output. A close contest
between strategies usually means the layout is unusual; capture the file as a corpus case
before changing anything.

## 3. Check what the columns became

```bash
surplusai parser interpret path/to/county.pdf
```

Three things to read:

**Unresolved columns.** Any column reported `(unresolved)` is preserved but not understood.
Add its wording to the right canonical field in `config/parsing/field_aliases.yaml`. That
one line helps every county that uses the same wording afterwards, which is the main way
the system improves.

**The surplus verdict.** One of:

| Verdict | Meaning | Action |
|---|---|---|
| `explicit` | One column names surplus and nothing rivals it | None |
| `absent` | No column names surplus | None — this is a correct outcome, not a failure |
| `ambiguous` | Several columns lay claim to it | Go to step 4 |
| `county_config` | A config file pinned the column | None |

**The claimable count.** `with a figure` counts rows carrying any surplus figure;
`claimable` counts those above zero. A large gap is normal and meaningful — it means most
of that county's records have already been paid out.

## 4. Settle an ambiguity

Create `config/counties/<state>/<county-slug>.yaml` from `config/counties/_template.yaml`:

```yaml
county_name: Marion County
state: IN
surplus_column: "Remaining Overbid"
```

Choose the column holding **money the county still has**, not a gross figure and not an
amount already refunded. If the county's own wording does not make that clear, ask the
county rather than guessing; getting this wrong sends the team after money that is not
there, and the opposite error loses every real lead.

Then re-run step 3 and confirm the verdict is now `county_config`.

To assert positively that a county publishes no surplus, set `surplus_column: null`. That
is different from omitting the key, which means "use the generic rules".

## 5. Add the county to the corpus

Copy the PDF into `data/test_pdfs/` and add `data/test_pdfs/expected/<name>.json`:

```json
{
  "pdf_type": "searchable",
  "ocr_required": false,
  "page_count": 12,
  "table_count": 1,
  "row_count": 431,
  "declared_row_count": 431,
  "expected_headers": [["Parcel", "Owner", "Excess Funds"]],
  "spot_check_rows": [{"page_number": 1, "values": {"Parcel": "01-1234"}}]
}
```

Discovery is automatic — the new file joins every corpus-wide test with no code change.
Include `declared_row_count` whenever the document states its own total; that assertion
comes from the publisher rather than from us and catches both dropped rows and repeated
headers counted as data.

```bash
pytest tests/unit/parser
```

## 6. Record the county profile

```bash
surplusai parser profile path/to/county.pdf --slug <county-slug> --state <xx> --county <county-slug>
```

This prints what the document reveals about the county — its columns, whether OCR was
needed, which strategy read it, whether a surplus was listed — along with a version
fingerprint. Two files in the same layout share a fingerprint even when their record counts
differ, which is how the second is recognised as another sighting rather than a new layout.

Profiles are append-only. A changed fingerprint means the county changed its layout, which
is worth looking at: it is exactly the moment a pinned `surplus_column` can start pointing
at a column that no longer exists.

## 7. Check who the owners are

```bash
surplusai classify document path/to/county.pdf --state <xx> --county <county-slug>
```

Reports how many owners are worth contacting and what the rest are. A tax sale of occupied
property is mostly individuals; a lien auction is mostly companies, and a county that is
overwhelmingly companies will yield few leads however many rows it has.

Estates and trusts count as pursuable and are reported separately. That is deliberate: an
estate has heirs entitled to the money who often do not know it exists, so discarding it as
"not an individual" would lose the best kind of lead there is.

Any owner reported `unknown` is a name that could not be read — common after OCR. It is
stored but never called.

## 8. Store the result

```bash
surplusai parser ingest path/to/county.pdf --state <xx> --county <county-slug>
surplusai parser review list
```

`ingest` writes the document, every row, the column mappings, and a queue entry for any row
that cannot be worked unattended. Re-running on the same file changes nothing: identity is
the file's contents, not its name.

Work the queue with `parser review resolve <id> --by <name>`. Resolving closes the work item
and never alters the extracted row.

## 9. Build the cases

```bash
surplusai leads build path/to/county.pdf --state <xx> --county <county-slug>
```

This does everything step 8 does and then builds the business records: the county itself,
a case per row, a property where one was described, an owner per published name, a
compliance verdict per case, and a lead for each case that clears every gate.

Read the funnel it prints. `cases created` on a second run should be zero — a republished
list is not new work. A large `owner_not_pursuable` count is normal for a lien auction and
tells you what the county's list actually contains.

**Expect zero leads.** Until the county's state has its statutes recorded, every case is
held at the compliance gate and reported as `compliance_blocked`. That is the system working:
follow `onboarding_a_state_compliance_profile.md`, then run

```bash
surplusai leads promote --state <xx> --county <county-slug>
```

which converts the held backlog into leads without re-parsing anything.

If the county needs registration details recorded — where its list comes from, how often it
publishes — add `fips_code`, `source_type`, `source_url` and `publishing_frequency` to its
config file. Prefer an official API or bulk download over anything scraped, and say which it
was: `source_type` is how the team knows.

## What must never be done

- **Do not compute a surplus** from a bid minus a debt. Liens, fees and costs are paid
  first; the difference is not the amount owed. A county that publishes no surplus
  correctly yields null.
- **Do not add surplus wordings to `field_aliases.yaml`.** Surplus vocabulary lives in
  `surplus_terms.yaml` and is matched exactly, never fuzzily, on purpose.
- **Do not resolve an ambiguity by picking the largest number.** The largest is usually the
  gross figure, which is the one already paid back.
- **Do not add county-specific branching to parser code.** If a county cannot be onboarded
  through configuration, that is a gap in the generic path worth fixing for everyone.
- **Do not treat an estate or a trust as a company.** They are pursuable by default because
  there is a person to contact and money they are entitled to. If a county's estates should
  not be worked, change `pursue` in `config/classification/entity_keywords.yaml` rather than
  reclassifying them.
- **Do not add short entity markers that collide with surnames.** Markers match whole words,
  but a marker like `an` or `de` would still fire on real names. When in doubt, prefer a
  longer, unambiguous form.
- **Do not treat zero leads as a parsing failure.** Check the rejection counts first. A
  `compliance_blocked` count equal to the claimable cases means the state's statutes are not
  recorded, which is a state onboarding task, not a county one.
