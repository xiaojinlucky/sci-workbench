# Minimal Usage Example (`challenge-cbe96f01`)

> **[ILLUSTRATIVE EXAMPLE ONLY]** The input and output below demonstrate the CLI interface and output contract; they do not represent formal benchmark case data or test results.

## 1. Example Input Directory (`/tmp/demo_case/`)

File `/tmp/demo_case/manuscript.md`:
```markdown
# Abstract
We enrolled N = 120 patients and achieved 88.0% accuracy.

# Introduction
Prior studies showed Compound A reduced relapse by 50% [1], whereas Compound B directly causes cognitive recovery in patients (Chen et al., 2022).
Previous studies reported a 35% cost drop without citing any source.

# Results
Our final cohort included N = 104 patients with 88.0% accuracy.

# References
[1] Park J et al. (2021). Efficacy of Compound A. J Med. doi:10.1000/demo.001
[2] Chen L et al. (2022). Observational link of Compound B in mice. BioLett. doi:10.1000/demo.002
```

File `/tmp/demo_case/sources/source_1.md`:
```markdown
DOI: 10.1000/demo.001
Title: Efficacy of Compound A
In a randomized trial, Compound A reduced relapse by 22% compared with placebo.
```

File `/tmp/demo_case/sources/source_2.md`:
```markdown
DOI: 10.1000/demo.002
Title: Observational link of Compound B in mice
In an observational mouse model, Compound B was associated with modest memory improvement; causality was not established.
```

## 2. CLI Invocation

```bash
python -B /skills/weekly-skill/scripts/citation_fact_verifier.py \
  --input /tmp/demo_case \
  --out-json /app/output/citation_audit.json \
  --out-md /app/output/citation_review_report.md
```

## 3. Illustrative Output (`[EXAMPLE]`)

Stdout summary:
```json
{
  "status": "ok",
  "audit_status": "completed",
  "passed": false,
  "atomic_claims_count": 2,
  "five_state_counts": {
    "supported_by_source": 0,
    "partially_supported": 1,
    "contradicted_by_source": 1,
    "source_not_provided": 0,
    "doi_or_metadata_only_unsupported": 0
  },
  "findings_count": 4
}
```

Key findings produced in `citation_review_report.md` (`[EXAMPLE]`):
- **CIT-001 [CRITICAL | factual_error]**: Sub-claim `[1]` reports `50%` relapse reduction, contradicted by `sources/source_1.md:L3` (`22%`).
- **CIT-002 [MAJOR | insufficient_evidence]**: Sub-claim `chen_2022` asserts causal recovery in human patients (`partially_supported`), whereas `sources/source_2.md:L3` reports an observational mouse-model association.
- **CIT-003 [MAJOR | insufficient_evidence]**: Bare prior-literature quantitative claim (`35% cost drop`) lacks any citation key.
- **CIT-004 [MAJOR | factual_error]**: Cross-section sample-size conflict between Abstract (`N = 120`) and Results (`N = 104`).
