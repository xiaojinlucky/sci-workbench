# Assertion Quality Gates & Cryptographic Tamper Verification

## 1. Automated Rejection Taxonomy

Every proposed or auto-extracted assertion is audited by `nanopub_builder_engine.py`. Rejected drafts are excluded from `nanopublications.json` and logged in `rejected_proposals` and `gaps`:

| Code | Meaning | Remediation |
|---|---|---|
| `MISSING_ASSERTION_OR_ANCHOR` | One of the 8 required fields is empty | Supply complete atomic draft |
| `UNKNOWN_RELATION_OR_POLARITY` | Predicate or polarity outside controlled vocabulary | Map to `controlled_predicate_vocabulary.md` |
| `OVERBROAD_NON_ATOMIC_ASSERTION` | Statement >65 words or spans ≥2 sentences | Split into one assertion per falsifiable claim |
| `ANCHOR_NOT_UNIQUE` | `(source_file, paragraph_id)` matches 0 or >1 indexed paragraphs | Use exact locator from `/app/work/source_paragraphs.json` |
| `QUOTE_NOT_VERBATIM_IN_ANCHOR` | Quote is not a whitespace-normalized substring of the paragraph | Copy exact verbatim sentence/clause |
| `SUBJECT_OR_OBJECT_NOT_IN_QUOTE` | Named entity does not appear in quote | Align subject/object strings with source text |
| `CONDITION_NOT_IN_QUOTE` | Declared condition absent from quote | Ground condition in source quote |
| `NEGATION_NOT_PRESERVED` / `UNCERTAINTY_NOT_PRESERVED` | Negation or hedging dropped or invented | Keep exact polarity cues |
| `NUMERIC_VALUE_UNIT_OR_COMPARATOR_CHANGED` | Numeric magnitude, unit, or comparator differs between quote and statement | Preserve exact values and units |
| `DIRECTION_OR_RELATION_NEEDS_REVIEW` | Subject/object reversed or predicate unsupported in active/passive form | Fix direction or log manual review gap |
| `SOURCE_SCOPE_REQUIRES_EXPLICIT_CONDITION` | Quote contains scope cue (`only when`, `at X °C`, `in vitro`) omitted from `condition` | Copy scope clause into `condition` and `statement` |

## 2. Cryptographic Integrity & Tamper Detection Protocol

`nanopub_signature_verifier.py` computes:
1. Per-graph canonical JSON SHA-256 digests (`head_sha256`, `assertion_sha256`, `provenance_sha256`, `pubinfo_unsigned_sha256`).
2. Combined base64url Trusty-style artifact code (`RA...`).
3. Local HMAC-SHA256 signature binding all four graph digests to `publication_info.signed_by`.

Tampering with ANY triple in `assertion`, `provenance` (e.g., changing `paragraph_id`, `quote`, or `doi`), `head`, or `signed_by` causes `--verify` to exit non-zero and flag the exact tampered graph (`PROVENANCE_GRAPH_TAMPERED`, `ASSERTION_GRAPH_TAMPERED`, `SIGNER_IDENTITY_TAMPERED`, `SIGNATURE_VERIFICATION_FAILED`).
