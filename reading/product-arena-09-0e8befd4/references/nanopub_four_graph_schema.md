# Nanopublication 4-Graph Schema & Identity Separation

## 1. Four Named Graphs (Aligned with Nanopublication Guidelines `nschema.html`)

A valid local Nanopublication separates scientific content, evidence provenance, and publication/signing metadata into four named graphs:

1. **Head (`<uri>#Head`)**: Declares `<uri> a np:Nanopublication` and binds `np:hasAssertion`, `np:hasProvenance`, and `np:hasPublicationInfo`.
2. **Assertion (`<uri>#assertion`)**: Holds ONE atomic, falsifiable scientific claim (`subject`, `predicate`, `predicate_uri`, `object`, `atomic_statement`, `condition`, `polarity`, `quantities`, `assertion_agent`).
3. **Provenance (`<uri>#provenance`)**: Grounds `#assertion` via `prov:wasDerivedFrom` to `source_file`, unique `paragraph_id`, verbatim `quote`, `evidence_type` (`direct_empirical` | `indirect_inference` | `cited_secondary`), `voice_verified`, optional true `page` (PDF only), and trusted `source_metadata` (only fields explicitly supplied by the input).
4. **Publication Info (`<uri>#pubinfo`)**: Records who curated the nanopub (`dct:creator`), who signed it (`npx:signedBy`), creation timestamp, `state: "local_draft"`, `no_external_publish: true`, and the `integrity` envelope covering all four graphs.

## 2. Role Separation Matrix (Addresses `nanopub-java` Issue #127 / PR #128)

Never conflate the four distinct identities below:

| Role Field | Graph | Meaning | Example |
|---|---|---|---|
| `assertion.assertion_agent` | Assertion | Actor/study group making or subject of the scientific claim | `paper_authors:Chen et al.` or `urn:orcid:0000-0001-...` |
| `provenance.source_metadata.authors` | Provenance | Authors of the underlying paper/source document | `["Chen, L.", "Wang, H."]` |
| `publication_info.creator` | Pubinfo | Curator or workflow agent extracting the nanopublication | `urn:agent:nanopub-curator` |
| `publication_info.signed_by` | Pubinfo | Signer identity bound to the cryptographic digest (`npx:signedBy`) | `urn:agent:local-draft-signer` |

If a claim explicitly states `asserted_by_distinct_agent: true` and `assertion_agent == signed_by`, `nanopub_signature_verifier.py` rejects it with `AGENT_SIGNER_ROLE_CONFLATION`.

## 3. Dual Machine-Readable Serializations

- `nanopublications.json`: Native JSON array with full 4-graph objects, rejected proposals, conflicts, and verification summary.
- `nanopublications.jsonld`: Standard JSON-LD `@context` + `@graph` named graph containers.
- `nanopublications.trig`: Valid RDF TriG syntax preserving `<uri>#Head`, `#assertion`, `#provenance`, and `#pubinfo` blocks.
