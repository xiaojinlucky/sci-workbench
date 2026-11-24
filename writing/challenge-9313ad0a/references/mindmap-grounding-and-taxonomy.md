# Scientific Mind Map Grounding, Node Taxonomy, and Storyline Rules

## 1. Node & Edge Taxonomy

### Node Types (`node_type`)
- `root`: Single unifying research question, manuscript title, or project objective (`parent_id: null`).
- `concept`: Core domain entity, mechanism, or theoretical construct.
- `question`: Explicit research question or sub-hypothesis.
- `method`: Experimental protocol, dataset, statistical test, or computational model.
- `evidence`: Quantitative result, observation, figure/table finding, or literature citation.
- `conclusion`: Supported takeaway bounded by stated scope.
- `task`: Concrete work package, milestone, or decision action.
- `risk`: Failure mode, limitation, bias, or contingency trigger.

### Epistemic Status (`epistemic_status`)
- `fact`: Directly reported measurement, condition, or statement grounded verbatim in `/input`.
- `summary`: Faithful synthesis combining two or more grounded facts without expanding claim strength.
- `inference`: Analytical implication or proposed next step explicitly marked as derived (never disguised as source fact).

### Cross-Link Relations (`cross_edges[].relation`)
`supports` | `depends_on` | `causes` | `contrasts` | `contradicts` | `cites` | `mitigates`

Every cross-link edge MUST carry a `source_anchor` (`file` + verbatim `quote` or PDF `locator`). Never draw causal arrows (`causes`) from purely correlational inputs; use `supports` or `contrasts` instead.

## 2. Storyline Organization & Granularity Balance

- **Storyline over chronology**: Organize branches by scientific logic (`Problem & Gap -> Methods & Controls -> Key Evidence -> Conclusions & Boundaries -> Risks & Next Actions`) rather than dumping raw paragraphs in order.
- **Sibling parity**: Nodes at the same depth should share comparable abstraction levels (avoid mixing a top-level chapter with a single reagent concentration at depth 1).
- **Readability bounds**: Aim for depth `<= 5` and `<= 60` nodes per single canvas (`> 80` triggers a readability warning; split into overview + sub-maps if larger).
- **Conflict preservation**: When two sources disagree, represent both findings as sibling `evidence` nodes connected by a `contradicts` or `contrasts` cross-edge with each source's scope conditions preserved.
