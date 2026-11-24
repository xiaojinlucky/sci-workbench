# Six-Dimension Structural Review Protocol & Anti-Style-Bias Firewall

This handbook defines the diagnostic taxonomy, executable editing verb grammar, press-release narrative check, and fairness firewall for `paper-structure-optimization`.

## 1. Six-Dimension Structural Taxonomy

| Dimension | Core Diagnostic Question | High-Signal Structural Failure Modes |
| --- | --- | --- |
| **D1. RQ & Contribution Alignment** | Do Abstract/Intro questions and contribution claims close across Methods, Results, and Conclusion? | Promised mechanism/ablation never tested in Methods/Results; Conclusion introduces an unmotivated claim; Intro lacks a falsifiable question. |
| **D2. Section Structure & Narrative Order** | Does section sequence and literature organization serve the argument rather than a chronological diary? | Multi-paper year-by-year literature laundry list (`Author A (2019)... Author B (2020)...`) without comparison axes; procedural config buried inside Results; trial-and-error log ("first we tried X and failed"). |
| **D3. Argument Chain (`Claim -> Evidence -> Warrant`)** | Does every inferential step bind to explicit evidence and a valid warrant? | Unreconciled same-metric contradictions across cited works or sections; `therefore/thus` conclusion without supporting premise; unanchored superiority/significance claim. |
| **D4. Paragraph Relations & Redundancy** | Does each paragraph advance a distinct claim without cross-section duplication? | Discussion repeating entire Results numbers verbatim; Intro duplicating Related Work; overloaded multi-topic paragraphs. **Exempt**: concise high-level Abstract restatement of main findings. |
| **D5. Conclusion Boundary & Epistemic Scale** | Are claims scoped strictly to the experimental design and data? | Observational/correlational study escalated to causal verb (`proves`, `causes`, `drives`); universal out-of-sample extrapolation (`all patients`, `universally`); missing concrete Limitations; apologetic self-undermining phrasing in primary claims. |
| **D6. Structural Integrity & Anomalies** | Is the manuscript structurally complete and internally consistent? | Missing canonical sections (`[MISSING]`), inline citations without References section, abrupt mid-sentence truncation, unbalanced delimiters. |

## 2. Closed-Set Executable Editing Verb Grammar

Every action card MUST use exactly one verb from this closed set and bind to `(section, paragraph_id, line_range, quote_excerpt)`:

- **`MOVE`**: Relocate a sentence, paragraph, or subsection to its functionally appropriate section (e.g., procedural parameters from Results to Methods; apologetic caveat to Limitations).
- **`MERGE`**: Consolidate duplicated or fragmented paragraphs into one authoritative home location and leave a brief cross-reference in the secondary section.
- **`SPLIT`**: Separate an overloaded multi-claim paragraph or a trial-and-error narrative block into distinct, single-focus argumentative units.
- **`NARROW_CLAIM`**: Constrain causal strength or domain scope to match the actual experimental design, sample, and statistical evidence.
- **`ADD_BRIDGE`**: Insert a missing premise, explicit warrant, RQ-to-Conclusion link, or concrete Limitations paragraph.
- **`REGROUP_BY_DIMENSION`**: Reorganize a chronological study-by-study literature list into thematic comparison groups (by experimental condition, method axis, or evaluation metric) led by an author-owned claim.
- **`DEFER_TO_AUTHOR`**: Flag an unreconciled scientific contradiction, missing section, or truncated text where resolving the issue requires the author's domain decision or missing source material.

## 3. Press-Release Narrative Principle (Argument-First Discipline)

1. Treat a scientific paper as an evidence briefing, not a chronological lab diary.
2. Main findings and comparative advantages must be stated directly with exact Table/Figure anchors.
3. Separate objective scientific limitations (mandatory in `Limitations`) from self-undermining apologetic phrasing ("unfortunately, our preliminary attempt only...") embedded in primary result statements.

## 4. Anti-Style-Bias & Anti-Vague-Advice Firewall (ACL H1-H17 Alignment)

1. **No style-only rejections**: Do NOT classify passive voice, British vs American spelling, sentence length variation, or benign domain phrasing as structural defects.
2. **No fabricated "missing experiments"**: Do NOT demand extra SOTA comparisons or out-of-scope datasets unless the manuscript's own stated claim explicitly requires them.
3. **No vague slogans**: Reject any draft critique containing bare instructions such as "strengthen logic", "improve coherence", or "polish flow" without a specific paragraph anchor and closed-set editing verb.
4. **Author decision authority**: Every card exposes `Accept | Partially Accept | Reject`. Never rewrite the whole manuscript unsolicited or alter the author's scientific stance.
