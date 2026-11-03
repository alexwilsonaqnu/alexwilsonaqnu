---
name: evidence-grading
description: Rubric for the evidence-grade badge on a solution card and for declaring each source's type. Use when drafting evidence claims or assigning/reviewing an evidence grade.
---

# Evidence grading rubric

The badge shows the **strongest verified evidence** behind a card. The verifier computes it in code; drafters
propose a grade and declare each source's type honestly. A declared type is checked against the source's
domain and text, and capped if it doesn't fit.

| Grade (badge) | Meaning | Typical sources |
|---|---|---|
| `meta_analysis_or_rct` | A systematic review / meta-analysis, or at least one randomized controlled trial, finds effects | CDC Community Guide findings, What Works Clearinghouse reports with studies meeting standards, Campbell reviews, J-PAL RCT summaries, peer-reviewed RCTs |
| `quasi_experimental` | A credible comparison group without randomization (difference-in-differences, regression discontinuity, matched comparison, interrupted time series) | Peer-reviewed or government evaluations |
| `observational_or_admin_data` | Before/after or administrative data with no comparison group | Agency reports, program dashboards, government statistics |
| `self_reported` | The implementing organization's own claims | Program websites, annual reports, press releases |
| `anecdotal` | Individual stories, news coverage | News articles, testimonials |

## Declaring `source_type` for each evidence claim
`systematic_review`, `rct`, `quasi_experimental`, `government_report`, `admin_data`,
`peer_reviewed_observational`, `org_report`, `news`, `other`.

Caps the verifier enforces:
- News domains can never exceed `anecdotal`.
- `systematic_review` / `rct` / `quasi_experimental` require a research host (government, university,
  journal, clearinghouse) **and** the page text must describe that design ("systematic review",
  "randomized", "comparison group", ...). Otherwise they are capped at `government_report` (research host)
  or `org_report` (anything else).
- The card grade never exceeds the drafter's proposal (drafters may be more conservative, never less).

## Wording that must survive into the claim
Carry the source's hedges through verbatim in meaning: "potentially positive effects", "mixed effects",
"no discernible effects", "insufficient evidence", "inconsistent effects". The verifier rejects claims that
flip the polarity of their quote.
