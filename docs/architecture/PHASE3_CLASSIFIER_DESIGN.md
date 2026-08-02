# Phase 3: Owner Classification

**Status: implemented and green.**

The brief asks for two things: remove companies, keep individuals. Doing exactly that would
have thrown away good leads, so this note records where the implementation goes beyond the
literal instruction and why.

---

## 1. Estates and trusts are leads, not companies

`ESTATE OF JERIMIAH GILBERT` and `ESTATE OF LOIS BYRD` are both in the Harford corpus file.
Neither is a company and neither is a living individual, so a two-way split has to put them
somewhere, and "not an individual" would discard them.

That would be the wrong way round. An estate is arguably the best lead in this business: the
owner has died, the heirs are legally entitled to the money, and they are frequently unaware
it exists. A trust is similar — there is a human trustee to contact.

So classification produces six answers (individual, company, estate, trust, government,
unknown) and **which of them are pursued is configuration**, in
`config/classification/entity_keywords.yaml`. Estates and trusts are pursued by default.
That keeps a commercial decision out of the code, where changing it would mean a release.

## 2. Whole-word matching, because the errors are asymmetric

The obvious implementation is a substring check against a list of suffixes. It reads `inc`
inside `VINCENT`, `co` inside `COOPER`, and `lp` inside `ALPERT` — three real corpus-shaped
names turned into businesses.

The two directions of error are not equally costly:

| Error | Cost |
|---|---|
| A person classified as a company | The lead is dropped silently and never appears again |
| A company classified as a person | One wasted call, visible immediately |

The first is far worse, so matching is whole-word only and there are explicit regression
tests for each trap.

## 3. Order of authority

Checks run government → estate → trust → company → person-shape. Two of those orderings are
load-bearing:

- **Government before company**, or `CALVERT COUNTY` reads as an ordinary business name.
- **Estate before company**, because an estate carries no company marker and no person shape,
  and would otherwise fall through to unknown and be lost.

A name matching nothing, and not shaped like a person, is `unknown` at zero confidence.
Recognition leaves plenty of these — cells holding only `&` or `| &` — and they are recorded
rather than deleted, which keeps them off a call list without losing the record.

## 4. Confidence reflects the kind of evidence

A marker match scores 0.95: a legal suffix exists precisely to name a legal form, so it is
positive evidence. A person scores 0.80, because nothing positive was found — the name is
inferred from shape, and an unfamiliar organisation with no recognisable suffix looks exactly
like a person. The gap is deliberate and the two must not be reported as equally certain.

## 5. What the corpus shows

The two Maryland counties and the Indiana county have opposite shapes, which is why both are
tested. Tuning on either alone would produce a classifier that looks fine and fails the other.

| County | Individuals | Companies | What it is |
|---|---|---|---|
| Calvert MD | 83% | 15% | tax sale of occupied property — real homeowners |
| Marion IN | 13% | 82% | lien auction — institutional bidders |

And the funnel the whole system produces:

| County | Rows | Claimable | Pursuable | Leads |
|---|---|---|---|---|
| Calvert MD | 96 | 0 | 80 | **0** |
| Harford MD | 49 | 49 | 35 | **35** |
| Marion IN 2023 | 950 | 130 | 140 | **47** |
| Marion IN 2024 | 917 | 139 | 192 | **59** |

Calvert yields nothing despite 80 pursuable owners, because it publishes no surplus and the
system will not invent one. Marion falls from 950 rows to 47 because most overbids were
already refunded *and* most bidders are companies. Both reductions are pinned by tests, so a
change that quietly loosens either one fails.

## 6. Not built

A trained classifier. The rules are config-driven and the corpus supports them;
ARCHITECTURE.md places a model in Phase 12, once enough reviewed names exist to train on.
`ClassificationResult.method` already distinguishes `rule` from `ml` and `manual`, so a model
can be added alongside the rules rather than replacing them, and a disagreement between the
two becomes visible instead of silent.
