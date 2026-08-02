# Phase 2 Design: County-Agnostic Parsing Pipeline

**Status: Phase 2 complete. 2A extraction, 2B interpretation and 2C OCR plus county
profile learning are all implemented and green.**

This supersedes ARCHITECTURE.md §2.2/§2.3 and amends the Phase 1 schema where the new
surplus rule requires it.

Everything below is grounded in the five real PDFs now in `data/test_pdfs/`. Each design
decision names the corpus evidence that forced it.

## Implementation record for 2A

Three things changed once the design met the files:

1. **Region emptiness is a density test, not a zero test.** The design assumed a scanned
   data region contains no characters. St. Mary's contains exactly one stray glyph inside
   its table image, which defeated an `== 0` check and classified the document as
   searchable. The test is now characters per 1000 square units, where a genuine text page
   measures about 9.1 and that region measures 0.002.
2. **Continuation is decided by column count alone.** The design had a continuation page
   matching on headers. In practice a headerless continuation page begins with a data row,
   and header detection nominates that row because data rows still score moderately well as
   labels — 0.806 against 1.000 for a real header. Requiring a header match split Calvert
   into two tables and promoted a real record into a header, destroying it. Matching column
   count is the stable signal.
3. **Strategies are compared on a sample, then the winner runs alone.** Running every
   strategy across a 55-page document tripled the work for no additional information, since
   a county prints one layout on every page. Selection now uses the first few readable
   pages, which halved corpus parse time with identical results.

Measured outcome on the corpus: Marion 2023 extracts 950 rows and Marion 2024 extracts 917,
each exactly matching the count the document declares about itself.

## Implementation record for 2B

Two rules were tightened once the corpus was run through them:

1. **Exact matching alone picks the wrong Marion column.** The brief listed "Overbid" as a
   surplus term, and Marion publishes a column called exactly that — so exact matching
   would have selected the gross overbid, which on a redeemed parcel has already been
   refunded in full. A second rule was added: when several published headers contain the
   same surplus-ish token, none is chosen and the county is reported ambiguous. Marion's
   three overbid columns therefore produce no answer until its config file settles it, and
   the shipped config pins `Remaining Overbid`.
2. **An unresolved surplus must not auto-accept.** Every other column in Marion resolves
   cleanly, so an unconfigured county scored high enough to be routed as ready to work
   while the one figure that matters was unknown. Ambiguity now caps routing at review,
   the same way OCR does.

## Implementation record for 2C

Recognition on the corpus's scanned county needed three corrections that only real pixels
revealed:

1. **Recognise the whole page, then filter to the region.** Cropping to the data region
   before recognition deprives Tesseract of the surrounding layout and cut word recovery
   from 202 words to 37. Words are filtered to the region afterwards instead.
2. **Gutter detection had to become fractional.** The text-layer algorithm looks for
   columns no word ever crosses. Recognised word boxes are ragged and long owner names
   spill sideways, so on the scanned page every position between the first and last column
   was covered by some line, and strict emptiness found one gutter where there are four.
   A position now counts as a gutter when a configurable share of lines leave it clear;
   text layers keep the strict default, so nothing about 2A changed.
3. **Rows and headers arrive fragmented.** Owner names sit on their own baseline and the
   header is stacked three deep. A line is folded into the record above it when it leaves
   the first column empty and fills only columns that record left empty, and leading
   short-label lines are rejoined into one header. A spanning title is excluded by length,
   which is what separates it from a stack of column labels.

The result is imperfect and that is the point: recognition misreads digits, and the same
parcel number came back cleanly at one setting and as "fos-03a933" at another. Every
OCR-derived row is therefore capped at human review whatever it scores. Chasing a perfect
read of one county would have been overfitting to it.

St. Mary's is titled "Balance of Bids/Excess Funds" but publishes no per-row amount, so it
correctly resolves to no surplus. Naming a document after surplus is not publishing one.

Profiles landed as designed: append-only, identified by a hash of layout that deliberately
excludes record counts, so Marion's 950-record and 917-record files share one profile and
the second is recorded as another sighting rather than a new version.

Measured outcome: Calvert yields no surplus with its three published money figures intact;
Harford yields an explicit surplus on all 49 rows; Marion yields 950 and 917 rows carrying
a figure, of which **130** and **139** are actually claimable — the rest read `$0.00`
because the money has already gone back. Those two counts were verified independently
against the PDFs.

---

## 1. What the corpus already proves

| PDF | Pages | Text layer | Headers | Money columns | Surplus explicit? |
|---|---|---|---|---|---|
| Calvert County MD | 2 | searchable | row 2 (rows 0–1 are title/date), `SALE\nAMOUNT` wrapped | ASSESSMENT, SALE AMOUNT, BID AMOUNT | **No** |
| Harford County MD | 1 | searchable | row 0 | SURPLUS | **Yes** |
| Marion County IN 2023 | 49 | searchable | row 0, repeats every page | Face Value, Overbid, Purchase Amount, Refunded Overbid, Remaining Overbid | **Ambiguous — 3 columns say "overbid"** |
| Marion County IN 2024 | 55 | searchable | same | same | same |
| St. Mary's County MD | 1 | **1,277 chars of chrome, 0 chars in the data region** | none extractable | unknown until OCR | unknown |

Five findings that drive the whole design:

**(a) "Is the PDF searchable?" is the wrong question.** St. Mary's has a healthy text layer
— disclaimers, phone numbers, navigation links like "Pay Your Bill" — and the entire
surplus table is a single 505×862 raster image at `(60,642)–(566,1504)`. Cropping to that
region yields **zero characters**. A document-level searchable/scanned flag would extract
the disclaimer and report zero rows. The decision must be made **per region**, driven by
where the tabular data actually is.

**(b) Header rows are not row 0.** Calvert's header is row 2; rows 0–1 are a title and a
sale date. Header position must be detected, never assumed.

**(c) Headers wrap across physical lines.** Calvert has `SALE\nAMOUNT`; Marion has
`Purchase\nAmount`, `Refunded\nOverbid`, `Remaining\nOverbid`. Reading the linearized text
is actively misleading here: line 1 ends `... Face Value | Overbid | Purchase | Refunded |
Remaining` and line 2 reads `Amount | Overbid | Overbid`, which invites the wrong pairing.
The word boxes settle it — each line-2 word sits at the exact x-center of its line-1
partner (`Amount` 646.5 under `Purchase` 646.5; `Overbid` 716.3 under `Refunded` 716.3;
`Overbid` 786.1 under `Remaining` 786.1). The published headers are therefore `Face Value`,
`Overbid`, `Purchase Amount`, `Refunded Overbid`, `Remaining Overbid`, and the arithmetic
confirms it: Face Value $5,118.74 + Overbid $3,203.00 = Purchase Amount $8,321.74. Header
reconstruction must use **x-coordinate alignment, never text order**, and the reconstructed
header is verified independently rather than taken on trust from any one library.

**(d) Multi-page continuation behaves in opposite ways within one corpus.** Calvert page 2
has **no header** and must inherit page 1's. Marion repeats its header on **all 49 pages**
and those repeats must not become data rows. Proof the arithmetic works: naive extraction
gives 999 rows across 49 pages; 999 − 49 header rows = **950**, exactly the `Parcel Count:
950` the document declares about itself. That self-declared count is a free row-recall
assertion, and the 2024 file declares 917.

**(e) The surplus trap is real and already in the corpus.** Calvert publishes ASSESSMENT,
SALE AMOUNT and BID AMOUNT — bid ($15,000.00) exceeds sale amount ($3,743.93), so a surplus
plainly exists arithmetically, but **the county does not publish it**. Per requirement 7 the
system must record `surplus_amount = NULL` and preserve the three published figures in their
own fields. Marion is the opposite trap: three columns contain the token "overbid"
(`Overbid`, `Refunded Overbid`, `Remaining Overbid`) alongside two further money columns
(`Face Value`, `Purchase Amount`), and a substring or fuzzy match would fire on all three.

---

## 2. Core principle: extraction and interpretation are separate layers

Extraction answers *"what does the page literally say?"* — verbatim, lossless, no business
meaning. Interpretation answers *"what does that mean in our schema?"* — configurable,
confidence-scored, reversible, and always additive.

The raw layer is immutable truth. Interpretation can be re-run, corrected, and improved
years later against the stored raw layer without re-reading the PDF. Nothing in the raw
layer is ever dropped, renamed, trimmed, or coerced — that satisfies requirements 4, 5 and 6
structurally rather than by discipline.

---

## 3. Pipeline stages

```
PDF
 └─ 1. Document/Region Profiling      → per-page & per-region text-vs-image verdict
 └─ 2. Strategy Cascade               → run candidate extractors, score each
 └─ 3. Table Detection & Quality      → pick best candidate structurally
 └─ 4. Header Reconstruction          → locate header row(s), rejoin wrapped headers
 └─ 5. Multi-page Stitching           → inherit or dedupe headers across pages
 └─ 6. Verbatim Row Capture           → RawTable, original headers preserved
 └─ 7. Column Interpretation          → original header → canonical field + confidence
 └─ 8. Type Inference & Coercion      → currency/date/id, originals retained
 └─ 9. Surplus Resolution             → explicit-only rule
 └─ 10. Profile Learning              → append a new immutable CountyProfileVersion
 └─ 11. Confidence Gate               → auto-accept / review / quarantine (never discard)
```

### Stage 1 — Document and region profiling

`DocumentClassifier` produces a `PageProfile` per page and aggregates to a
`DocumentProfile`. Signals per page: character count, character density per unit area,
image count and total image coverage ratio, vector rect/line counts, and — decisively —
**text density inside candidate data regions versus outside them**.

Region logic: any image whose area exceeds a configurable fraction of the page is treated as
a candidate data region. If the text density inside it is below threshold, that region is
`image_only` and requires OCR **even when the page as a whole has abundant text**. St.
Mary's is precisely this case.

Output: `page_type ∈ {text, image_only, hybrid}`, `ocr_required: bool`, and a confidence.
The document-level `pdf_type` is the aggregate, but per-page decisions drive extraction.

### Stage 2 — Strategy cascade (not a single choice)

Requirement 2 says "automatically choose the best extraction strategy." Choosing implies
comparing, so the pipeline **runs several strategies and scores their output**, rather than
branching on a guess. No county-specific branching exists anywhere.

| Strategy | Suited to | Notes |
|---|---|---|
| `PdfplumberLatticeStrategy` | ruled tables | uses lines/rects |
| `PdfplumberStreamStrategy` | whitespace-aligned | corpus default; all four searchable PDFs have `lines: 0` but hundreds of `rects` |
| `CamelotLatticeStrategy` | strongly ruled | needs Ghostscript |
| `CamelotStreamStrategy` | column-gap inference | independent second opinion |
| `WordClusterStrategy` | irregular layouts | our own: cluster word boxes by x/y, derive columns from x-gap histogram |
| `OcrTableStrategy` | `image_only` regions | rasterize → Tesseract TSV (word boxes) → same clustering as `WordClusterStrategy` |

Ordering comes from `config/parsing/strategies.yaml`, and a county profile can pin a
known-good strategy first (speed, not correctness — the winner is still chosen by score).

### Stage 3 — Structural quality scoring

`TableQualityScorer` scores each candidate 0–1 using only structure, never county
knowledge, so it generalizes to counties never seen:

- column-count consistency (share of rows matching the modal column count)
- cell fill rate
- header plausibility of the best candidate row (short, alphabetic, unique, no currency)
- per-column type coherence (share of cells matching one inferred type)
- column x-alignment regularity
- penalty for overflow artifacts (single cells containing many delimiters)

Highest score wins; the score feeds the confidence model. Losing candidates are retained in
the parse record for diagnostics.

### Stage 4 — Header reconstruction

`HeaderReconstructor` scans the first N rows for the most header-like row using the
plausibility signals above (Calvert: row 2, not row 0). Wrapped headers are rejoined by
**x-overlap of the word boxes**, not by text order — the continuation word `AMOUNT` joins
`SALE` because their boxes share an x-span, and this is what stops `Purchase Overbid` from
degrading into `Purchase Amount`. Duplicate header labels are disambiguated positionally
(`Overbid`, `Overbid__2`) so no column is ever lost to a name collision.

### Stage 5 — Multi-page stitching

`TableStitcher` compares each page's first row against the established header:
- **matches** → it is a repeated header; record it, exclude it from data (Marion)
- **does not match, column count compatible, types consistent** → headerless continuation;
  inherit headers (Calvert page 2)
- **neither** → a genuinely new table; start a new `RawTable` (multiple tables per document)

Every row keeps its true `page_number` and `table_index` regardless of stitching
(requirement 9). Stitching decisions are recorded with confidence and are reversible.

### Stage 6 — Verbatim capture

`RawTable` stores `original_headers` exactly as published and each row as a
`dict[original_header → verbatim cell text]`. Cells are never trimmed beyond preserving the
string, never type-coerced, never renamed. Content that does not fit the table shape
(orphan lines, footers, the Marion `Parcel Count:` banner) is captured in
`unparsed_fragments` rather than discarded — requirement 5 holds even for material we cannot
interpret.

Each row carries: `source_pdf_path`, `source_pdf_sha256`, `page_number`, `table_index`,
`row_index_on_page`, `extraction_method`, `extraction_confidence`.

### Stage 7 — Column interpretation

`ColumnInterpreter` resolves each original header to a canonical field, in precedence order:

1. **County profile override** (`config/counties/<state>/<slug>.yaml`) — deterministic, 1.0
2. **Global alias registry** exact match after normalization (casefold, strip punctuation,
   `#`/`no.`→`number`, collapse whitespace) — e.g. `Account Number`, `Parcel Number`,
   `Tax Map ID`, `ACCT #`, `Tax Parcel` → `parcel_id`
3. **Fuzzy match** (RapidFuzz) above threshold → confidence scaled by similarity
4. **Value-based inference** when the header is missing or unresolved: sample the column and
   classify by pattern (currency, date, parcel-like, person-name-like, address-like)
5. **Unresolved** → preserved verbatim as an extra field and flagged for review, never
   dropped

Every column yields `ColumnMapping(original_header, canonical_field | None, confidence,
method, evidence)` — requirement 8, at column granularity.

The canonical schema is deliberately a superset so distinct concepts never collide:

- identity: `parcel_id`, `case_number`, `certificate_number`, `unique_id`, `tax_year`
- parties: `owner_name`, `owner_mailing_address`, `purchaser_name`
- property: `property_address`, `property_city`, `property_zip`, `legal_description`
- dates: `sale_date`, `foreclosure_date`, `redemption_deadline`, `claim_deadline`
- money — each its **own** field, never merged: `surplus_amount`, `sale_amount`,
  `winning_bid`, `opening_bid`, `judgment_amount`, `assessed_value`, `appraised_value`,
  `taxes_due`, `fees_amount`, `face_value_amount`, `overbid_amount`, `purchase_amount`,
  `refunded_amount`, `remaining_amount`
- status/meta: `parcel_status`, `notes`

### Stage 8 — Type inference

Currency (`$1,234.56`, `1,234.56`, parentheses negatives), dates (multiple formats plus
embedded times, as in Marion's `10/02/2023\n09:40:45 AM EDT`), and identifiers are parsed
into typed values **alongside** the verbatim original, never replacing it. A coercion
failure lowers row confidence; it never drops the row.

### Stage 9 — Surplus resolution (the highest-risk rule)

`SurplusResolver` implements requirement 7's "if and only if". Mislabeling a sale price as
surplus manufactures a false lead and a compliance exposure, so this stage is deliberately
the most conservative in the system.

1. **Denylist wins over everything.** `sale amount`, `bid amount`, `winning bid`, `high
   bid`, `opening bid`, `minimum bid`, `assessment`, `assessed value`, `appraised value`,
   `taxes due`, `judgment`, `face value`, `purchase amount` can never become
   `surplus_amount`, at any confidence, by any path.
2. **Fuzzy matching is disabled for `surplus_amount`.** Only exact matches against the
   explicit vocabulary in `config/parsing/surplus_terms.yaml` qualify: `surplus`, `excess
   funds`, `excess proceeds`, `overage`, `overplus`, `balance of bid`, `funds due owner`,
   `unclaimed surplus`. Every other money column keeps its own canonical field.
3. **Ambiguity does not resolve itself.** If two or more columns qualify — Marion's four
   overbid columns — the resolver does **not** pick one. It sets `surplus_amount = NULL`,
   `surplus_source = ambiguous`, and routes the county to review. A one-line county config
   settles it permanently thereafter.
4. **No arithmetic inference.** `surplus = bid − sale` is never computed implicitly. Calvert
   therefore yields `surplus_amount = NULL` with `assessed_value`, `sale_amount` and
   `winning_bid` all populated. A county may opt in to a derived formula in its config, and
   the result is then stamped `surplus_source = derived`, `surplus_is_explicit = false` —
   visibly different from published data.

Result per case: `surplus_amount`, `surplus_is_explicit`, `surplus_source ∈ {explicit,
derived, ambiguous, absent}`, and `surplus_source_column` naming the published header it
came from.

**Confirmed county decisions.** These live in county config, not in parser code:

- **Marion County IN** → `surplus_amount` comes from **`Remaining Overbid`**, the money the
  county still holds. `Overbid`, `Purchase Amount` and `Refunded Overbid` are preserved in
  their own fields. Rows where `Remaining Overbid` is `$0.00` have already been refunded and
  must not become leads — the qualification filter is `remaining > 0`, applied in Phase 5,
  not by dropping rows here.
- **OCR-derived rows never auto-accept.** Any row whose values came from OCR is routed to
  human review regardless of score, because a single misread digit in a money field is
  expensive. Implemented as a hard rule in the Stage 11 gate — `extraction_method == OCR`
  caps the routing outcome at `review`, independent of the composite confidence.

### Stage 10 — County profile learning

`CountyProfile` is **append-only and never overwritten**, as required. It extends the
existing `parsing_profile_versions` table, which is already unique on
`(county_id, version_hash)`.

Learned and stored per version — exactly the fields requested:

| Field | Source |
|---|---|
| `county_name`, `state` | config / filename / document text, confirmed at onboarding |
| `original_column_names` | Stage 6 verbatim headers |
| `pdf_type` | Stage 1 (`searchable` / `scanned` / `hybrid`) |
| `table_structure` | table count, columns per table, header row index, stitching mode |
| `ocr_required` | Stage 1 |
| `typical_owner_types` | observed name-shape statistics (entity-suffix frequency). Recorded only — real classification is Phase 3 and is not implemented here |
| `surplus_explicitly_listed` | Stage 9 |
| `required_parsing_strategy` | winning strategy + parameters from Stage 3 |
| `confidence_stats`, `sample_file_hashes`, `times_observed`, `created_at` | provenance |

`ProfileLearner` computes a `version_hash` over the semantic content. Identical content
appends an **observation** (increment `times_observed`, append the file hash) without
altering the stored profile; different content creates a **new version row**. Older versions
are marked `superseded_by` but never modified or deleted, so the full history is auditable
and any past parse remains reproducible.

`ProfileResolver` loads the newest approved profile as a **prior**: known column mappings
become high-confidence hints and the previously winning strategy is tried first. With no
profile, the full cascade cold-starts. This is the "smarter every time" mechanism —
Marion 2024 benefits from what Marion 2023 taught the system, and the two files are in the
corpus specifically to prove it.

### Stage 11 — Confidence gate

Composite confidence combines document classification, strategy/table quality, header
reconstruction, per-column mapping and per-row type validation, with weights in
`config/parsing/confidence.yaml`. Thresholds route to `auto_accept` / `review` /
`quarantine`. **All three are stored.** Low confidence means "a human should look", never
"discard" — requirement 5 again.

---

## 4. Configuration surface (requirements 10 and 11)

```
config/parsing/
    field_aliases.yaml      canonical field ↔ alias lists (global, grows over time)
    surplus_terms.yaml      explicit surplus vocabulary + denylist
    strategies.yaml         cascade order and per-strategy parameters
    confidence.yaml         weights and routing thresholds
    type_patterns.yaml      currency/date/parcel/name/address pattern library
config/counties/
    _template.yaml
    md/calvert.yaml         per-county overrides — only when needed
    in/marion.yaml
```

Onboarding a county is: drop the PDF in, run `surplusai parser inspect`, and either accept
the cold-start result or write a short YAML. **No parser code changes, ever.** A county that
cold-starts cleanly needs no file at all.

---

## 5. Module layout

New package, additive; no Phase 1 module is modified except the schema noted in §6.

```
surplus_ai/parser/
    models.py                 PageProfile, DocumentProfile, RawTable, ExtractionCandidate,
                              ColumnMapping, ParsedDocument
    exceptions.py
    document_classifier.py    stage 1
    strategy_selector.py      stage 2
    quality.py                stage 3
    headers.py                stage 4
    stitching.py              stage 5
    strategies/               base + 6 strategy implementations
    interpretation/           alias_registry, column_interpreter, type_inference,
                              surplus_resolver, confidence
    profiles/                 models, learner (append-only), resolver, store
    pipeline.py               ParsingPipeline orchestrator
```

---

## 6. Required Phase 1 schema amendments

The new surplus rule is incompatible with the current schema and forces a migration:

- **`surplus_cases.surplus_amount` must become nullable.** It is `NOT NULL` today. Calvert
  proves a valid case can exist with no published surplus. This is the single most important
  change; without it the pipeline would be forced to invent a value.
- add `surplus_is_explicit BOOLEAN NOT NULL DEFAULT false`, `surplus_source` enum
  `{explicit, derived, ambiguous, absent}`, `surplus_source_column TEXT`
- add distinct money columns: `winning_bid`, `opening_bid`, `assessed_value`,
  `appraised_value`, `taxes_due`, `fees_amount`, `face_value_amount`, `overbid_amount`,
  `purchase_amount`, `refunded_amount`, `remaining_amount`
- `raw_surplus_rows`: add `table_index`, `row_index_on_page`, `original_headers JSONB`,
  `source_pdf_sha256`, `extraction_confidence`
- new `parsed_documents` — one row per PDF: page count, pdf_type, ocr_required, winning
  strategy, confidences, file hash, row counts
- new `document_column_mappings` — per document per column: original header, canonical
  field, confidence, method, evidence (audit trail and future training data)
- extend `parsing_profile_versions`: `times_observed`, `observed_file_hashes JSONB`,
  `is_approved`, `superseded_by`
- new `parse_review_queue` for items below the auto-accept threshold

Downstream consumers must treat `surplus_amount IS NULL` as "not a qualified lead", never as
zero. That constraint lands in Phase 3/5, and I will note it in those phases' inputs.

---

## 7. Test strategy and anti-overfitting (requirements 12 and 13)

**Corpus-parametrized tests.** Every PDF in `data/test_pdfs/` is auto-discovered and
parametrized, so dropping in a new county file creates test cases with no code change —
requirement 13, structurally.

Per-PDF golden files (`data/test_pdfs/expected/<name>.json`) record expected pdf_type,
ocr_required, table count, header set, row count and spot-check rows. Assertions the corpus
already supports:

- Marion 2023 → exactly **950** data rows, 49 header rows excluded (matches its own
  declared `Parcel Count: 950`); Marion 2024 → **917**
- Calvert → header at row 2; page 2 inherits headers; `surplus_amount IS NULL`;
  `assessed_value`, `sale_amount`, `winning_bid` all populated
- Harford → `surplus_amount` populated, `surplus_is_explicit = true`, sourced from `SURPLUS`
- St. Mary's → `ocr_required = true` **despite** 1,277 chars of page text, and every
  resulting row routed to `review` rather than auto-accepted
- Marion → all five money columns preserved as distinct fields; `surplus_amount` sourced
  from `Remaining Overbid`; a row with `Remaining Overbid = $0.00` is not a lead
- Regression guard: with Marion's county config removed, `surplus_source` must fall back to
  `ambiguous` and never silently pick one of the three overbid columns

**Leave-one-county-out evaluation.** `surplusai parser evaluate --holdout <county>` re-runs
the pipeline with that county's profile *and* its contributed aliases excluded, measuring
cold-start accuracy on a format the system has effectively never seen. This is the concrete
guard against overfitting: it directly measures generalization rather than memorization.

**Corpus-wide metrics gate.** Column-mapping precision/recall, row recall, OCR-fallback
rate, and — held to a higher bar — **surplus-identification precision, which must remain
1.0**. A false positive there is the expensive kind of error. A change that improves one
county but lowers the corpus average fails CI.

---

## 8. Implementation phasing

Per CLAUDE.md, one phase completes before the next begins.

- **2A — Extraction core.** Schema migration, models, document/region classifier, strategy
  cascade, quality scorer, header reconstruction, stitching, verbatim capture. Deliverable:
  the four searchable PDFs extract every row with original headers preserved; Marion hits
  950/917 exactly.
- **2B — Interpretation.** Alias registry, column interpreter, type inference,
  `SurplusResolver`, confidence model. Deliverable: Harford yields explicit surplus, Calvert
  yields NULL surplus with its three money fields intact, Marion's four overbid columns stay
  distinct.
- **2C — OCR and profiles.** OCR strategy for image regions, `ProfileLearner`/`Resolver`,
  review queue, `parser` CLI, leave-one-out evaluation harness. Deliverable: St. Mary's
  parses via OCR; Marion 2024 measurably benefits from Marion 2023's profile.

## 9. Dependencies

Python: `pdfplumber` (installed and verified), `camelot-py[cv]`, `pytesseract`, `pdf2image`,
`Pillow`, `rapidfuzz`, `PyYAML`, `python-dateutil`.

System binaries — **none are currently installed** in this environment; all three are
available from apt and will be added to the Dockerfile, CI workflow and README:
`tesseract-ocr` (5.3.4), `poppler-utils` (24.02.0), `ghostscript` (10.02.1). Only the OCR
path (2C) and the Camelot strategies hard-require them; 2A and 2B run on pdfplumber alone,
so extraction and interpretation are not blocked if OCR install is deferred.
