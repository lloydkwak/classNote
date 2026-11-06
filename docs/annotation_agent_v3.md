# Annotation Intelligence v3

Implementation in progress, 2026-11-06. Default mode remains `legacy`. Synthetic regression tests and real stored lecture experiments must be reported separately.

## Production path

Upload `annotation_mode=legacy|ai_planner|visual_agent` → existing STT/alignment/Rich Notes validation → optional semantic planning → capability-scoped tool validation → existing layout v2/export. `ANNOTATION_MODE` is the new-job default; saved old jobs with no mode remain legacy. Modes do not change the transcript, alignment or knowledge inventory.

`app/annotation_agent/planning.py` uses the existing LLM provider. Each page receives full validated ContentItems, complete native text rows, and native canonical math parts. The planner distinguishes already printed information, additional teaching, and visual learning goals. It may produce several complementary intents per knowledge item. Source quotes omitted by the model are resolved from the canonical ContentItem, never reconstructed by the model. A supplied false quote is rejected. Novelty checks reject slide restatements, invented numbers, unsupported equations and exam claims.

`app/annotation_agent/tools.py` owns action state. Read tools inspect one source page, native element crops, evidence and current state. Write tools highlight/underline/circle/box/connect/write-near/bracket/check and remove/replace. Every write checks source PDF SHA, registry fingerprint, segment references, speech quote, alignment support, target ID, target granularity and numeric support. Unknown targets never snap to nearby geometry. Action identity includes knowledge, physical canonical target, tool, text, registry, policy and placement. Duplicate calls reuse state. Replace is atomic. Preview/export capabilities accept page/job IDs, not paths or arbitrary commands; the model cannot edit the original PDF.

`rendering.py` adapts validated states to existing Notes and AnnotationAction objects. Marks need no duplicate text card. Multiple actions retain their own action IDs and share source knowledge/evidence IDs. `grounded_notes.save_graph` preserves all action→target→render chains. Density and ink style filter/re-render saved states without model calls. Rendering remains deterministic, with source/export/audio index mapping unchanged.

## Visual mode

The isolated `.venv-vlm` worker accepts `agent_plan` and `agent_review` in addition to existing `select_targets`. JSONL stdout is isolated from native/model logs. Paths are canonicalized and checked inside the job root. Existing process cancellation, timeout, GPU lease, cache, NF4/SDPA/batch-one policy and OOM disposal are reused. Models remain local-files-only.

The VLM sees an actual SoM page plus bounded semantic intents and canonical candidates. It cannot generate coordinates or new claims. Low-confidence/skip/warning decisions do not become accepted targets. Invalid worker output retains the last validated semantic state and records fallback.

Review renders the actual annotated PDF page, then asks the VLM to inspect it. Only supplied action IDs and canonical candidates can be edited. Remove, retarget, replace, shorten, move and reroute go through the tools. At most two mutation rounds; a final review cannot initiate another revision. Invalid edits/unsafe revised layouts preserve the prior snapshot. Model acceptance is not human correctness; compare the rendered pages independently.

## Geometry and limitations

`grounding/math_elements.py` derives glyph and subexpression IDs from native PDF characters. Prose gaps, unlike equation terms, are barriers. Parentheses, terms, sides and products retain native character geometry. Multiline/uncertain math is not flattened. Repeated symbols still require supported context; explicit two-occurrence common-factor relations are checked structurally.

`grounding/figure_elements.py` optionally detects geometric candidates for simple light-background red/blue plots. Pixel dot centers and a corroborated gray-axis intersection are not recognized labels or verified vector semantics. Only visually confirmed, evidence-associated candidates are usable; arbitrary graphs remain unsupported. Raster caption/label understanding is limited. No OCR/SAM weights were installed.

## Checkpoints

- `annotation-agent-<mode>.json`: source/policy signature, registries, semantic/visual plans, tool calls, state snapshots, previews, reviews, telemetry.
- `agent-cache/semantic-*.json`: validated semantic outputs and actual provider usage.
- `agent-previews/`: actual PDF and PNG previews.
- Existing per-density evidence graph/actions/export remain authoritative rendering outputs.

The source signature includes PDF, extraction, transcript, alignment, rich knowledge, provider/model, model revision, schema/prompt/tool/math/figure/layout/runtime code and review policy. Density/style are deliberately excluded from inference caches. Code/source changes invalidate derived states. GET/density/export never silently performs inference.

## Reproduction

```bash
cd /root/classNote
FFMPEG_PATH=/usr/bin/ffmpeg backend/.venv/bin/python backend/scripts/verify_annotation_v3.py \
  --job data/slide-first-runs/real-reviewed/state/jobs/05a433631737 \
  --output data/annotation-v3-runs/new-comparison --prepare --gpu
```

Use a new output directory. This reads the original job and copies necessary inputs into an isolated experiment state. Paid provider quotas can cause explicit fallback. Reports must retain actual model usage (including configured-provider fallback), not assume all requests used the configured model. No human accuracy/semantic coverage is inferred from action counts.
