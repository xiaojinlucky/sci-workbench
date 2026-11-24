# Output Contract Specification (`product-arena-09-0e8befd4`)

## Required Files Under `/app/output/`

1. `result.json` — Must include `case_id`, `status` (`ok` | `partial_with_gaps`), `sources`, `uncertainty`, `artifacts`, `mode`, `signature_verification`, `conflicts`, `metrics`, `metric_provenance`, `findings`, and `gaps`.
2. `nanopublications.json` — Canonical 4-graph Nanopublication list (`head`, `assertion`, `provenance`, `publication_info`), `signature_verification`, `conflicts`, `rejected_proposals`, and `no_external_publish: true`.
3. `nanopublications.jsonld` — Standard JSON-LD `@context` + `@graph` serialization.
4. `nanopublications.trig` — Valid RDF TriG serialization of all four named graphs.
5. `evidence-report.md` — Human-readable coauthor review report listing every accepted Nanopublication, its locator, verbatim quote, separated identities, Trusty code, conflicts, and rejected/gap items.

## Case-Specific Contract Compliance

Always inspect `/input/task.md` and `/input/case.json` for any additional `output_contract` fields required by a specific fixed case; preserve all required fields without removing the core 4-graph artifacts.
