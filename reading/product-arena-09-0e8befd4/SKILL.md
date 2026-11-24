---
name: nanopublication-four-graph-auditor
description: Extract, verify, and locally sign 4-graph Nanopublications (Head, Assertion, Provenance, PublicationInfo) from scientific papers with atomic claim decomposition, active/passive relation direction verification, strict assertion-agent vs signer identity separation, cryptographic tamper detection, and JSON-LD/TriG export. Use for Nanopublication literature reading tasks in Harbor.
---

# Nanopublication 4-Graph Extraction, Audit & Verification (`product-arena-09-0e8befd4`)

## Purpose & Trigger Scope

Use this Skill whenever a scientific reading task requires turning paper conclusions into **atomic, independently falsifiable Nanopublications** with verbatim source provenance, separated identity roles, and machine-readable RDF/JSON-LD serialization.

Key failure modes this Skill prevents:
1. **Paragraph-dumping**: stuffing multi-sentence abstracts into a single assertion instead of splitting into atomic, individually verifiable claims.
2. **Identity conflation (`nanopub-java #127 / PR #128`)**: merging who made the scientific claim (`assertion_agent`), who authored the paper (`provenance.source_metadata.authors`), who curated the nanopub (`pubinfo.creator`), and who cryptographically signed the local draft (`pubinfo.signed_by` / `npx:signedBy`).
3. **Silent polarity, quantity, or direction drift**: dropping negations (`did not`), hedges (`may`, `suggests`), scope conditions (`only under pH 7`), numeric units, or swapping subject/object in passive voice.
4. **Unverifiable or tampered exports**: exporting plain JSON without named-graph separation or tamper-evident integrity hashes.

## Four-Graph Architecture & Hard Invariants

Every accepted Nanopublication MUST maintain four distinct named graphs (see `references/nanopub_four_graph_schema.md`):

| Named Graph | Responsibility | Hard Invariants |
|---|---|---|
| **`head`** | Links `<uri>` to `#assertion`, `#provenance`, `#pubinfo` | All three graph links must match `uri + "#<graph>"` |
| **`assertion`** | Atomic triple (`subject`, `predicate`, `predicate_uri`, `object`), `atomic_statement`, `condition`, `polarity`, `assertion_agent` | Single falsifiable claim (≤65 words, ≤1 sentence boundary); `assertion_agent` kept distinct from `signed_by` |
| **`provenance`** | `source_file`, unique `paragraph_id`, verbatim `quote`, `evidence_type`, `source_metadata` | Exact quote verified in unique locator; `evidence_type` ∈ `{direct_empirical, indirect_inference, cited_secondary}`; never fabricate missing DOI/authors |
| **`publication_info`** | `creator` (`dct:creator`), `signed_by` (`npx:signedBy`), `state: "local_draft"`, `no_external_publish: true`, `integrity` | Canonical SHA-256 digests per graph + Trusty artifact code + local HMAC-SHA256 signature; **strictly local, NEVER publish externally** |

## Execution Workflow (DeepSeek Harness / Offline Linux)

Locate all scripts relative to this `SKILL.md` directory (`scripts/`). Every script runs on pure Python 3.12 stdlib with zero network access.

### Step 1: Preflight & Initial Run (Indexing + Auto Atomic Candidate Extraction)

Run the engine first without `--claims` to index every supplied source paragraph (`/app/work/source_paragraphs.json`) and run initial atomic candidate extraction & verification:

```bash
python3 -B /skills/weekly-skill/scripts/preflight_probe.py --input /input --output /app/output --out /app/work/env.json
python3 -B /skills/weekly-skill/scripts/budget_guard.py --init --state /app/work/.budget_state.json
python3 -B /skills/weekly-skill/scripts/nanopub_builder_engine.py --input /input --output /app/output --work /app/work
```

- Inspect `/app/work/source_paragraphs.json` and `/app/output/evidence-report.md`.
- Check for PDF extraction gaps (`PDF_UNREADABLE_OR_NO_TEXT_LAYER`, `PDF_SCANNED_OR_EMPTY_PAGES`) before interpreting coverage.

### Step 2: Host-Agent Semantic Refinement & Controlled Drafting (`--claims`)

When `task.md` asks for specific claims, subtle passive/nested clauses, cross-paragraph qualifiers, or domain-specific terminology beyond starter extraction:

1. Read `/app/work/source_paragraphs.json` and `/input/task.md`.
2. Consult `references/controlled_predicate_vocabulary.md` and draft `/app/work/draft_claims.json` with one atomic object per claim:
   - Required fields: `statement`, `subject`, `predicate`, `object`, `quote`, `source_file`, `paragraph_id`, `polarity` (`affirmed` | `negated` | `uncertain`).
   - Optional but strongly recommended: `condition` (mandatory whenever the quote contains scope cues like `only when`, `under`, `at X °C`, `in vitro`, `仅在`), `evidence_type` (`direct_empirical` | `indirect_inference` | `cited_secondary`), `assertion_agent`, `signer_id`, `curator_id`.
3. Re-run with `--claims` and explicit role separation if the task names distinct proposer and signer identities:

```bash
python3 -B /skills/weekly-skill/scripts/nanopub_builder_engine.py \
  --input /input --output /app/output --work /app/work \
  --claims /app/work/draft_claims.json \
  --curator-id "urn:agent:literature-curator" \
  --signer-id "urn:agent:local-draft-signer"
```

### Step 3: Independent Local Signature & Tamper Verification

Verify that all exported 4-graph Nanopublications pass canonical graph digest, role-separation, and signature validation:

```bash
python3 -B /skills/weekly-skill/scripts/nanopub_signature_verifier.py --verify /app/output/nanopublications.json
```

If any triple in `head`, `assertion`, `provenance`, or `pubinfo` is modified after signing, verification MUST fail with explicit graph-level codes (`ASSERTION_GRAPH_TAMPERED`, `PROVENANCE_GRAPH_TAMPERED`, `SIGNER_IDENTITY_TAMPERED`, `SIGNATURE_VERIFICATION_FAILED`).

### Step 4: Grounding Gate & Final Deliverable Audit

```bash
python3 -B /skills/weekly-skill/scripts/gap_gate.py --verify-json /app/output/result.json --corpus /input --gaps-out /app/work/gaps.json
python3 -B /skills/weekly-skill/scripts/final_audit.py --dir /app/output --result-file result.json \
  --require nanopublications.json --require nanopublications.jsonld \
  --require nanopublications.trig --require evidence-report.md
```

## Handling Conflicts, Evidence Tiers, and Exceptions

- **Cross-condition vs Same-condition Conflicts**: When Paper A reports `X increases Y under condition C1` and Paper B reports `X decreases Y under condition C2`, preserve BOTH assertions and classify as `different_conditions_preserved_separately`. Never silently drop or average opposite findings; classify identical-condition contradictions as `same_condition_direct_conflict`.
- **Direct vs Indirect vs Cited Secondary**: Never label a citation of prior work (`Smith et al., 2021 reported...`) or a speculative discussion sentence (`may explain...`) as `direct_empirical`.
- **Unreadable / Missing Sources**: If a PDF has blank/scanned pages or lacks a text layer, keep partial verified output, log `PDF_SCANNED_OR_EMPTY_PAGES` / `PDF_UNREADABLE_OR_NO_TEXT_LAYER` in `gaps`, and set `status: "partial_with_gaps"`. Never invent quotes or metadata.
- **No External Publish**: All exports carry `state: "local_draft"` and `no_external_publish: true`. Never attempt HTTP uploads to public nanopub servers.

## Reference Index

- `references/nanopub_four_graph_schema.md` — 4-graph data model, identity separation rules (`assertion_agent` vs `signer`), and RDF/JSON-LD mapping.
- `references/controlled_predicate_vocabulary.md` — 18 controlled predicates mapped to RO/SIO/IAO URIs, active/passive direction rules, polarity & scope gates.
- `references/assertion_quality_and_signature_audit.md` — Rejection taxonomy, Trusty digest computation, and tamper-detection test protocol.
- `references/output_contract_spec.md` — Exact deliverable file schemas and `result.json` contract.
