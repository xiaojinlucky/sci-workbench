# Third-Party Notices (`challenge-9e7302fd`)

All runtime scripts in this package are pure-Python standard-library implementations requiring zero bundled third-party packages in offline containers.

Algorithmic and conceptual acknowledgements:

- **Regularized incomplete Beta/Gamma continued fractions & Acklam normal quantile**: standard numerical analysis methods (Lentz modified continued fraction; Acklam rational approximation with Halley refinement), implemented from scratch in `scripts/stat_math_core.py`.
- **p-Curve distributional test concepts** (`scripts/phack_detect.py`, `references/phack_concepts.md`): pure-stdlib clean-room reimplementation of classical p-curve / Elliott-Kudrin-Wuethrich LCM / Gerber-Malhotra caliper concepts (inspired by MIT-licensed `p-hacking-skills` conceptual framing, with all numpy/scipy dependencies removed and single-paper non-accusatory guardrails enforced).
- **Evidence-bound review & ACL H1–H17 fairness firewall**: adapted from public peer-review fairness protocols and defect-first triage principles.
- **Harbor `common_core` utilities**: shared internal track utilities under permissive terms.

No copyleft, proprietary, or banned libraries are bundled or imported.
