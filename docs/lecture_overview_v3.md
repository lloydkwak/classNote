# Lecture Overview v3

The previous PDF printed processing counts, page/time inventories and concatenated short notes. The v3 renderer presents a study page: lecture purpose, learning goals, integrated concepts, formulas/principles, representative examples, connections and review points. No timestamps, alignment warnings, model/registry identifiers or validation counts are printed in its body. Such diagnostics remain internal JSON/API data.

`pipeline/overview_synthesis.py` uses the existing LLM provider on the whole lecture: validated full ContentItems, every transcript segment, adjacent speech groups (with exact segment IDs), native formulas and slide topics. Long input uses bounded cited map/reduce with explicit failure instead of silent input truncation. Citations reference canonical source IDs; the server supplies exact original text if the quote is omitted. Supplied quotes must match, all numeric claims need cited support, equations must occur in native/validated source content, and prose may not sneak in unverified equations.

A separate provider critique checks every study claim against its cited sources. It distinguishes citation existence from semantic entailment and can trigger at most two synthesis repairs. Unsupported optional connection/review advice can be removed with an explicit omission record; unsupported core concepts are never silently removed. This is model assessment, not a human factual correctness guarantee. Failed/incomplete synthesis is recorded and uses labeled source explanations instead of invented synthesis. Detailed Notes preserve complete content.

Calls happen during notes preparation. `load_synthesis` is read-only and verifies source, provider, prompt/schema/validation fingerprints and source support again. Density/style exports reuse synthesis; PDF/transcript/alignment/knowledge/policy changes invalidate it.

`overview.py` keeps the existing source/export index contract: exported Overview index 0; original slide index 0 remains original slide index 0, exported at index 1. Source page/audio APIs remain 1-based and unchanged. The exporter always starts from original input, so re-export cannot accumulate Overview pages. Old stored jobs work without synthesis files. Meaningful concept-title PDF links use existing source mapping.

The study renderer uses embedded Unicode-capable existing fonts and a 10.5pt lower bound. Whole cards are prioritized on overflow, with full study JSON and Detailed Notes retained. It records presentation overflow/font/bottom internally. This cannot guarantee every long lecture fits one page without prioritization; no equations are clipped mid-claim.

See `annotation_agent_v3.md` for reproduction and actual experiment reports under ignored `data/annotation-v3-runs/`. Current implementation/experiment status is maintained in `codex_handoff.md` and `annotation_quality_report.md`.
