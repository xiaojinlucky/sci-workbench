# Grant Proposal Craft, Hypothesis Design, and Anti-AI Prose Discipline

Use this reference when drafting the seven grant sections and refining prose before running `grant_7element_auditor.py`.

## 1. Grant vs. Paper Framing
A grant proposal asks reviewers to evaluate **planned work**, not already-completed results:
- State **what problem matters and what the concrete gap costs** in the opening paragraph instead of generic macro importance or policy slogans.
- Express every **Aim/Goal (`G1/G2/G3`)** as a scientific question or verifiable engineering outcome, never as a bare method name (e.g., avoid "Aim 2: Run finite element modeling"; write "G2: Quantify how void-fill geometry and resin modulus govern flexural stiffness and failure mode under three-point bending").
- Separate **already reported external findings** (named authors + exact reported values) from **proposed activities (`拟开展`)** and **missing applicant proof (`[EVIDENCE_GAP: ...]`)**.

## 2. One-Center, Three-Pillars Hypothesis & Shortest Evidence Chain
Structure the core argument around one testable central hypothesis supported by three aligned work packages:
> **Template**: Prior studies by [External Authors] show [quantified phenomenon/baseline]. However, [specific unresolved mechanism or parameter boundary] remains untested. This proposal hypothesizes that [explicit variable, direction, and boundary condition]. If valid, the planned experiments will show [observable quantitative prediction].

Apply the **Shortest Evidence Chain** filter before adding any task:
1. Which specific goal (`G1/G2/G3`) does this work package answer?
2. What is the **unit of inference** (specimen batch, print build plate, biological donor, independent cohort) and control comparison?
3. What minimal observation distinguishes hypothesis support from falsification?
4. If removing this step leaves the core claim intact, drop it from the main plan.

### Aim-to-Evidence Mainline Table
Every grant chapter should align all seven elements through one coherent mapping:

| Goal ID | Core Question | Work Package (`WP`) | Method & Unit of Inference | Milestone (`<= planning_months`) | Planning Units | Verifiable Future Metric (Not Guaranteed Outcome) |
|---|---|---|---|---|---|---|
| G1 | Baseline characterization / mechanism isolation | WP1 | Controlled baseline + parameter sweep | M4 | e.g., 30 | Target evaluation protocol & threshold range |
| G2 | Process/mechanism optimization & modeling | WP2 | Factorial design / validation model | M8 | e.g., 40 | Predictive error bound or comparative gain criterion |
| G3 | Boundary stress test & robustness verification | WP3 | Independent replicate verification | M12 | e.g., 30 | Reproducibility and failure-envelope criterion |

## 3. Preliminary Foundation Four-Question Test (With Strict Attribution)
When summarizing prior literature or supplied pilot data in `研究基础与可行性`:
1. **What was observed?** Exact numerical trend, effect size, sample size, and test conditions from `/input`.
2. **How credible is it?** Replicates, controls, instrumentation, and experimental boundaries actually reported.
3. **What does it directly support?** Which parameter range or baseline protocol it provides for `WP1–WP3`.
4. **What critical evidence is missing?** Explicitly list `[EVIDENCE_GAP]` items whenever applicant-owned preliminary data, equipment availability, personnel qualification, ethics approval, or real funder rules are not supplied.

## 4. Press-Release Narrative & Anti-Defensive Rules
Treat the proposal as a concise scientific brief rather than a tentative diary:
1. **Lead with judgment and evidence**: Start every section with the core problem, hypothesis, or technical decision—never with "遗憾的是", "受限于条件", or a wall of disclaimers.
2. **Single-site honest boundaries**: State each real evidence gap or limitation once, at the exact point where it affects feasibility or human confirmation, in the pattern `已有外部证据推进到哪一步 + 本项目拟补什么 + 尚待申请人确认什么`.
3. **Concrete differentiation in `创新点`**: Anchor every innovation claim to a specific prior method/paper in `/input` (`相较于 [Author] 仅在 [条件A] 下测试 [指标X]，本项目拟在 [条件B] 引入 [机制/变量Y]`).
4. **Pre-mortem 5-column risk matrix**: Assume the planned project fails at Month 6 and work backward to specify at least 3 concrete risks with: `Risk Category & Failure Mode | Earliest Trigger Signal | Preventive Design | Decision Checkpoint | Fallback Plan`.

## 5. Anti-AI Cliché & Anti-Hype Checklist
Replace hollow phrasing without changing any scientific number, unit, citation, or uncertainty boundary:
- **Remove unverifiable priority hype**: `国际领先`, `国内首创`, `填补空白`, `首次提出`, `前所未有`, `world-first`, `unprecedented`, `breakthrough`.
- **Remove policy slogans & business buzzwords**: `赋能`, `抓手`, `卡点`, `打通最后一公里`, `重大战略意义`, `开启新时代`, `重磅`, `炸裂`.
- **Remove AI template transitions**: `综上所述`, `总的来说`, `由此可见`, `不难发现`, `众所周知`, `值得注意的是`, `研究表明` (without naming the specific study), `为未来研究奠定基础`, `提供新思路`.
- **Remove pseudo-completion verbs for planned work**: Replace `将确保提升 / 必将实现 / 已验证本项目方案 / 经费已落实` with `拟评估 / 计划验证 / 预期检验判据为 / 规划单位用于一致性核算（非真实资金）`.

## 6. Handling RFP Requirements & Conflicting Collaborator Comments
- **RFP / Workorder Completeness**: Treat every item in `organizer-workorder.json` (`sections`, `consistency`, `required_supporting_files`, `scientific_limits`, and any extra `rfp_requirements`) as a mandatory checklist; explicitly map where each item is answered or why it requires human confirmation.
- **Collaborator Feedback Conflicts**: Never silently average contradictory suggestions (e.g., one co-author asks to expand scope to clinical translation while another insists on staying within supplied benchtop data). Log both comments, select the choice compliant with `/input` evidence and `scientific_limits`, explain the rationale, and flag for applicant sign-off.
