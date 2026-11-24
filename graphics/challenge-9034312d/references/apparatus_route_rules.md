# Apparatus & Technical Route Diagram Rules

1. **Stream Semantics (Color + Dash + Marker Separation)**:
   - `material` / `fluid` / `reagent`: solid line (`#2E6B8E`), filled arrow (`arrow-material`).
   - `optical` / `laser` / `beam`: solid amber line (`#D19A45`), filled arrow (`arrow-optical`).
   - `electrical` / `signal` / `data`: dashed teal line (`#4E9187`, `5,3`), open arrow (`arrow-signal`).
   - `control` / `trigger`: dotted purple line (`#967A9C`, `2,3`), filled arrow (`arrow-control`).
   - `waste` / `exhaust`: dashed gray line (`#737373`, `4,3`), open arrow (`arrow-waste`).
   - `feedback` / `recycle`: dashed coral line (`#D77C70`, `6,3`), flat/loop marker (`arrow-feedback`).
2. **Topological & Port Soundness**:
   - Every declared `inlet` or `source` must reach at least one `outlet`, `detector`, or `milestone` unit.
   - Zero orphan apparatus modules (every non-standalone reference module must participate in >= 1 incoming or outgoing stream).
   - `stage_order` must be non-decreasing along primary routes; backward edges are permitted only when `stream_type` is `feedback` or `recycle`.
3. **Typography & Layer Separation**:
   - Dedicated `<g id="layer-*">` groups for `background`, `zones`, `streams`, `units`, `ports`, `annotations`, and `legend`.
   - Component boxes carry `id="unit-<id>"`, labels `id="unitlabel-<id>"`, and port callouts `id="port-<id>"` so `.drawio` conversion emits individually draggable vertices and edges.
