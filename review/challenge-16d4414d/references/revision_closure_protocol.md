# Multi-Round Revision Closure Protocol (challenge-16d4414d)

Designed for major revisions and replacement reviewers joining in round 2/3, where heavy rewriting obscures whether prior reviewer comments were genuinely addressed in the manuscript text.

## 1. Automatic Activation Inputs
Revision Closure Mode activates automatically when `/input` contains:
- Prior reviewer comments (`*review*`, `*comment*`, `*referee*`, `*审稿意见*`)
- Plus either an author point-by-point response (`*rebuttal*`, `*response*`, `*reply*`, `*答复*`, `*回复*`) or a manuscript revision pair (`*v1*`/`*old*`/`*initial*` and `*v2*`/`*revised*`/`*final*`).

## 2. Closure State Matrix

| `closure_status` | Condition | Audit Finding Emitted |
| --- | --- | --- |
| `closed` | Author addressed the comment and `v1 -> v2` diff contains substantiating additions/modifications with matching domain terms | None (verified closed with exact `v2` line anchors) |
| `partially_closed` | Limited overlap in `v1 -> v2` diff or no `v1` baseline supplied for exact diff | `minor` (`needs_human_review`) spot-check cue |
| `unclosed_claimed_fixed` | Rebuttal explicitly claims "added / revised / updated / 已补充 / 已修改", yet `v1 -> v2` diff shows zero substantiating edits | `major` `factual_error` (`revision_unclosed_claimed_fixed`) |
| `unclosed_unaddressed` | Neither rebuttal rationale nor `v1 -> v2` diff addresses the comment | `major` `insufficient_evidence` (`revision_unclosed_unaddressed`) |
| `declined_with_rationale` | Author explicitly declines (e.g., out of scope / infeasible) without falsely claiming a manuscript edit | Logged neutrally for editorial judgment (not auto-faulted) |

## 3. Scope Isolation of Newly Discovered Issues
Late-round reviewers are often instructed not to introduce non-essential new demands. Therefore:
- Every logic/argument defect detected on `manuscript_v2` outside prior comments is tagged as either:
  - `introduced_in_revision_diff`: appeared on lines newly inserted or modified in `v2` (legitimate to raise in the current round), or
  - `new_issue_isolated_outside_prior_comments`: existed outside prior comments in unchanged text (isolated in `revision_closure.new_issues_isolated` so the editor can decide whether it falls within late-round review scope).
- Never replace item-by-item comment mapping with a generic summary paragraph.
