# Nanopublication 4-Graph Extraction, Audit & Verification (`product-arena-09-0e8befd4`)

Offline, pure Python 3.12 stdlib Skill for converting scientific literature passages into **atomic, source-grounded, 4-graph Nanopublications** (`Head`, `Assertion`, `Provenance`, `PublicationInfo`) with separated identity roles (`assertion_agent` vs `provenance_authors` vs `pubinfo.creator` vs `npx:signedBy`) and tamper-evident local cryptographic verification.

## Package Structure

- `SKILL.md` — DeepSeek Harness operational workflow, hard invariants, and exception handling.
- `scripts/nanopub_builder_engine.py` — Multi-format paragraph indexer, compound-clause atomic splitter, active/passive relation direction & E/M/L/S/I verifier, conflict detector, and 4-graph exporter.
- `scripts/nanopub_signature_verifier.py` — Canonical 4-graph SHA-256 + local HMAC-SHA256 Trusty-style signer/verifier, JSON-LD and RDF TriG exporters.
- `scripts/{preflight_probe,result_guard,budget_guard,block_indexer,gap_gate,final_audit}.py` — Standard Harbor M1–M6 runtime guards.
- `references/` — 4-graph schema, controlled predicate ontology table, rejection & tamper-audit protocol, and output contract.
- `examples/` — Minimal illustrative input, claims draft, and expected schema example (labeled as examples; not formal test outputs).

## Quick Local Run (Offline Linux / Python 3.12+)

```bash
# Auto-extract and audit atomic Nanopublications
python scripts/nanopub_builder_engine.py --input /input --output /app/output --work /app/work

# Audit refined atomic claims with explicit role separation
python scripts/nanopub_builder_engine.py \
  --input /input --output /app/output --work /app/work \
  --claims /app/work/draft_claims.json \
  --curator-id "urn:agent:literature-curator" \
  --signer-id "urn:agent:local-draft-signer"

# Independently verify 4-graph digests and signatures
python scripts/nanopub_signature_verifier.py --verify /app/output/nanopublications.json
```

## Boundaries

- Local offline draft export only (`no_external_publish: true`); never pushes to external Nanopublication servers.
- Zero third-party binary or network dependencies required; PDFs without text layers are reported transparently in `gaps`.
