# Quality Plan: notes that look like the best student's

Written 2026-11-07 against `main` at `bd973a5`. Read with `CLAUDE.md` and `docs/codex_handoff.md`.
Goal: annotated slides a strong student would be proud of. Speed is out of scope for now.

## 1. Where we are

### What exists

| Area | State |
|---|---|
| Pipeline | extract → caption → transcribe → window → align → page_audio → evidence → notes → consolidate → export, resumable |
| Engines | three annotation paths: `legacy` (rich_notes + grounded_notes), `ai_planner` and `visual_agent` (`annotation_agent/` v3), plus `ANNOTATION_ENGINE=v1/v2` and `LAYOUT_ENGINE=v1/v2` switches |
| Grounding | native word/glyph registry, Set-of-Mark page images, math sub-expression IDs, simple-plot pixel candidates, local Qwen3-VL-8B NF4 worker on an RTX 3080 |
| Validation | verbatim quote check, number check, exam-wording check, novelty guard, registry/fingerprint/provenance checks, evidence graph |
| Layout/ink | layout_v2 "slide-first": inside free space first, then margin, then a bottom band; seeded natural ink |
| Study page | Overview page (cover) with sources, time links, PDF TOC |
| Evaluation | label schema, inspector UI, A/B/C harness, contact sheets; **no verified human labels** |
| Tests | ~509 backend, 31 frontend |

### What the reports and the renders say

- **Alignment is the root failure.** On the 36-minute lecture, exact page accuracy was 31.7% (±1 page: 46.1%), and the
  first window landed on page 47. On the 11-page lecture, pages 3 and 6 have near-zero confidence. Every
  downstream step inherits wrong pages: notes about K-Means init on the wrong slide cannot be fixed by layout.
- **Recall has collapsed under validation.** 11 validated knowledge items for an 11-page lecture; the v3 agent kept
  7-8 distinct items vs 11 for the baseline. Rejections come from string mechanics (quotes cut mid-sentence,
  "zero/one" vs 0/1), not from false claims. Exact-substring matching is the wrong truth test for paraphrased,
  misrecognized speech.
- **The planning model is weak.** Real runs used `gemini-3.1-flash-lite` (quota) and a 4-bit 8B local VLM.
  Judgment about what a professor meant is the hardest part of this product and is given the smallest models.
- **Layout still reads as machine output.** In a mock render of the current default: leaders cross slide text and each
  other, notes sit far from targets, whole bullet lines are boxed or underlined uniformly, note text is cut
  mid-token ("T(n"), and announcements ("자, 잠깐 공지할게요") are written onto slides.
- **Too many engines, no referee.** Effort went into provenance, policy, and fallbacks across three engines, while no
  human-verified gold exists to say which output is better.

## 2. Principles for the next phase

1. **One path.** Converge on: lecture brain (strong LLM) → page designer (strong VLM, SoM image) → deterministic
   validator → layout optimizer → renderer. Keep `legacy` as the fallback; freeze and stop extending the rest.
2. **Spend model budget where judgment matters.** Use a frontier model for understanding and design (about 1 call per
   3-5 pages plus one whole-lecture call). Keep code for geometry, checks, and drawing.
3. **Point, don't copy.** The model cites evidence by segment/word IDs; code resolves them. Truth is checked by
   entailment, not by substring equality.
4. **Fewer, better marks.** A page with three precise marks and two sharp notes beats ten uniform underlines.
5. **Measure against humans first.** No change ships without beating the current output on the gold set.

## 3. Phase 0: referee (do first, 1-2 days)

- **Alignment gold:** label one real lecture in the existing inspector (`#/jobs/<id>/inspect`): page change times only.
  At 2× playback a 36-minute lecture takes about 30 minutes. Mark it `verified`.
- **Public alignment benchmark:** add the MaViLS dataset (20 English lectures, sentence→slide labels,
  github.com/andererka/MaViLS) as a regression set via `scripts/benchmark_alignment.py`. Check its license first.
- **Notes gold:** for 10 slides, collect a real student's handwritten notes from that lecture (photo is fine) and transcribe
  them into the action schema in `samples/golden/`. This is the target, not what the model currently does.
- **Judge:** a pairwise LLM-as-judge that sees two rendered pages (PNG via `pdftoppm`), the slide, and the transcript
  span, and picks the better one on a fixed rubric: correct target, faithful, adds what isn't on the slide,
  condensed note style, readable, uncluttered, would a top student write this. Run both orders to cancel position bias.
  Report win rate plus the automatic metrics below. Use promptfoo or a small script.
- **Automatic metrics (per page):** alignment exact/±1, target precision/recall vs gold word IDs, entailment rate,
  leader-text crossings, leader-leader crossings, overlaps, truncated notes, marks per page, notes on off-topic speech.

## 4. Phase 1: alignment that is right

### Results (completed 2026-11-07)

All systems evaluated on 20 MaViLS lectures (10 train / 10 test, deterministic split).
Metric: sentence-level exact accuracy = correctly-assigned sentences / sentences-with-valid-gold.

| System | Config | Train | Test | Δ vs baseline |
|--------|--------|------:|-----:|:---:|
| v1 + e5 (baseline) | 30 s windows, exp=— | 51.7% | 62.9% | — |
| v2 + e5 | sentence windows, exp=3.0, bm25=0 | 48.1% | 53.5% | −9.4 |
| v2 + e5 (tuned) | exp=1.0, bm25=0.1 | 54.7% | 60.9% | −2.0 |
| v2 + bge-m3 | exp=3.0, bm25=0 | 49.3% | 58.3% | −4.6 |
| v2 + bge-m3 (tuned) | exp=1.0, bm25=0.2 | 56.6% | 65.0% | **+2.1** |
| v2 + qwen3 | exp=3.0, bm25=0 | 55.5% | 62.3% | −0.6 |
| v2 + qwen3 (tuned) | exp=1.0, bm25=0.2 | 56.9% | **66.8%** | **+3.9** |
| v2 + qwen3 + cues (4a) | exp=1.0, bm25=0 | — | 64.9% | +2.0 |

Best config: `v2 + qwen3, sharpening_exp=1.0, bm25_weight=0.2` → **66.8% test** (+3.9 pts over v1 baseline).
SNU target (swpp-2025 team 01): 81.7%. Gap: **14.9 pts**.

Gold-label gate (job `05a433631737`, 1 verified label):

| System | Exact | ±1 | Verdict |
|--------|------:|----:|---------|
| v1 + e5 | 100% | 100% | — |
| v2 + e5 | 80.6% | 80.6% | gate holds |
| v2 + bge-m3 | 80.6% | 80.6% | gate holds |

`ALIGNMENT_VERSION` default stays `v1` until v2 beats v1 on verified gold.

### Implementation shipped

- `app/pipeline/sentence_align.py` — sentence-window builder, max-normalize → sharpen → DP, BM25 mix,
  slide-turn cue boosts (4a), logistics/announcement off-topic detection (4b)
- `app/pipeline/llm_arbitration.py` — low-confidence stretch detection, LLM arbitration with thumbnails (4c)
- `app/pipeline/page_align.py` — `BgeM3Embedder`, `Qwen3Embedder`, `get_embedder()` model routing
- `app/pipeline/run.py` / `app/pipeline/live.py` — `ALIGNMENT_VERSION=v1|v2` switch (default v1)
- `scripts/benchmark_mavils.py` — grid-search tuning, train/test reporting, `--arbitrate` flag
- `scripts/benchmark_alignment.py --job` — per-system comparison on verified gold

### Techniques, in order of expected gain:

1. **Sentence-level DP with calibrated scores.** Align sentences (from word timestamps), not 30 s windows. Per sentence:
   normalize scores so the best is 1, apply exponential sharpening, boost confident sentences, then DP with a forward
   jump cost per skipped slide and a higher backward cost. A course team at SNU (swpp-2025 team 01) reports 81.7% on
   MaViLS with this recipe vs 53% audio-only in the MaViLS paper; most of the gain came from a stronger embedder.
2. **Stronger multilingual embeddings.** Benchmark BAAI/bge-m3 and Qwen3-Embedding (0.6B/4B) against e5-base on the gold
   lecture and MaViLS. Keep the best.
3. **Lexical channel.** BM25 over slide words plus the existing `mentions.py` (rapidfuzz, jamo, transliteration glossary).
   Mix with embeddings using weights fitted on gold (the MaViLS paper fits weights per lecture with gradient descent).
4. **Slide-turn cues.** Detect spoken cues ("다음 장", "넘어가서", "next slide", "앞에서 본") as transition priors.
5. **LLM arbitration for doubtful stretches only.** Where DP confidence is low, send the stretch plus candidate pages
   (±2, text and thumbnail) to the strong model and let it choose. A few calls per lecture.
6. **Off-topic detection.** Classify announcements, logistics, and chatter (one cheap call per lecture or a rule list) so
   they never become notes.
7. **Human correction in the viewer.** Let the user drag page boundaries on the alignment chart; rerun notes for affected
   pages only, and save the correction as gold.
8. **Live mode** uses the page the presenter shows (already planned).

Exit criterion: ≥80% exact and ≥90% ±1 on the gold lecture, and no regression on MaViLS.

## 5. Phase 2: the lecture brain (what to write)

- **One whole-lecture call** with a long-context model: all page texts plus page images (or captions), and the aligned,
  sentence-numbered transcript. Output, pydantic-validated:
  - outline of the lecture (5-8 steps with page ranges), concept list with one-line definitions,
  - per page: knowledge items with a role (`definition`, `intuition`, `example`, `derivation_step`, `warning`,
    `common_mistake`, `exam_hint`, `question`, `answer`, `connection`, `off_slide_addition`, `correction`), an
    importance score, a condensed note text, evidence as sentence IDs, and target word/line/figure IDs,
  - lecture-wide: professor's emphases, exam points, open questions, glossary of misrecognitions.
- **Evidence by ID, checked by entailment.** Code resolves sentence IDs to text. Check each note is entailed by its
  evidence: MiniCheck (fact-checking model, reported GPT-4-level on LLM-AggreFact at much lower cost; check language
  coverage and license) or a batched strong-LLM verifier for Korean. Keep the number check, but normalize numbers
  (zero/영/0, 100,000/10만) before comparing. Replace exact-substring quote checks with this.
- **Novelty vs the slide:** embedding similarity between note and slide text, plus the model's own `already_present` flag.
- **Recall check:** after generation, ask the verifier which explained points in each page's transcript are missing from
  the items; regenerate once for pages with gaps. Report recall against the gold.

## 6. Phase 3: the page designer (how it is marked)

- **Design grammar** (one visual language across the lecture, shown as a legend on the cover):

  | Meaning | Mark | Ink |
  |---|---|---|
  | key term / definition | highlighter behind words | yellow |
  | professor stressed | underline (double for exam) | red/orange |
  | result, theorem, final formula | box | blue |
  | the thing being pointed at | circle | blue |
  | lines explained together | bracket + one note | blue |
  | derivation steps | numbered badges ①②③ | green |
  | missing word or step | caret insert | blue |
  | correction | strike + replacement | red |
  | exam point | ★ + short note, at most one sticky per page | red |
  | relation between two items | short curved arrow + label | purple |

  Budgets scale with slide density (for example 3-6 marks, 2-4 notes, 0-1 sticky).
- **Designer call:** batch 3-5 pages. Give the SoM page image, word/line/figure ID lists, that page's knowledge items,
  and the grammar. The model returns actions (the v3 tool vocabulary is fine) choosing targets by ID. For regions inside
  figures, allow boxes in Gemini's 0-1000 coordinate convention relative to the figure crop; code clamps them.
- **Validator:** IDs exist, targets are supported by evidence (mentions in the evidence time range, or words in it),
  budgets, no duplicate targets. Record every drop with a reason (already in place).
- **Critic loop:** render, show the strong VLM the rendered page and the rubric, apply only deterministic fixes
  (drop, downgrade, move, shorten). Two rounds at most. The v3 review exists; give it a strong model.

## 7. Phase 4: layout and ink that look hand-made

- **Placement as optimization.** Generate candidates (free-space rectangles near the target, both margins, above/below
  lines), then minimize an energy: distance to target, leader length, leader-text crossings, leader-leader crossings,
  note overlap, reading order, side consistency. Simulated annealing as in D3-Labeler, or OR-Tools CP-SAT over the
  candidate set (exact for 10-20 notes per page). For margin notes, use boundary labeling: sort by target y so leaders
  never cross.
- **Leaders:** short, one gentle curve, attached to the target's nearest edge; no leader when adjacent. Route around text
  boxes with a visibility graph if a straight curve crosses text.
- **Text fitting:** never truncate. Break lines on word boundaries (Unicode line breaking, e.g. `uniseg`), and if a note
  doesn't fit, ask the model for a shorter version with a character budget, then fall back to the bottom band.
- **Ink:** perfect-freehand (now in `uv.lock`) for pressure-varying pen outlines with simulated pressure; rough.js /
  rough-notation algorithms for bowing and double passes; highlighter behind text (`overlay=False` in PyMuPDF,
  `mix-blend-mode: multiply` in SVG); a slight random tilt and baseline drift on handwritten notes; one seed per job.
- **Hierarchy:** fewer heavy marks, thinner lines for secondary marks, ink color reserved by meaning (see the grammar).

## 8. Phase 5: study pages

- **Cover page:** title and stats, lecture flow with page links, exam points with page + time, key concepts, the
  professor's own words, pen legend (the existing Overview, restyled to the grammar).
- **Concept map:** render the lecture brain's concept graph as a small tree (markmap-style) on the cover or a second page.
- **Cornell-style review page:** cue questions per section with answers linked to pages (NoTeeline found cue questions
  and a toggle between short and expanded notes valuable).
- **Editing:** move, delete, and rewrite notes in the viewer; save edits as gold for the judge.

## 9. Open source to use or study

| Purpose | Project | Use |
|---|---|---|
| Alignment benchmark | MaViLS (andererka/MaViLS) | dataset + DP baseline, regression set |
| Alignment recipe | SNU swpp-2025 team 01 wiki | sentence DP, score sharpening, 81.7% on MaViLS |
| Phonetic slide matching | moves-cli | idea only (GPL-3.0, archived) |
| Embeddings | BAAI/bge-m3, Qwen3-Embedding | benchmark vs e5 |
| Lexical | rank_bm25, rapidfuzz (in use) | lexical channel |
| Fact checking | MiniCheck (Liyan06/MiniCheck), Vectara HHEM | entailment instead of substring |
| Layout/OCR | Surya (datalab-to/surya), RapidOCR/PaddleOCR | scanned or image slides, reading order |
| Formulas | UniMERNet, Pix2Text | LaTeX for formulas in images |
| Element detection | OmniParser (microsoft) | reference for SoM on raster slides; check weight licenses |
| Page-image retrieval | ColPali / ColQwen | alignment for image-only slides |
| Grounding | Gemini box_2d (0-1000), Qwen3-VL (in use) | figure regions |
| Label placement | D3-Labeler, d3fc-label-layout, OR-Tools | placement optimizer |
| Boundary labeling | KIT boundary labeling papers | crossing-free margin leaders |
| Ink | perfect-freehand (in lock), rough.js, rough-notation | strokes and marks |
| Note UX | NoTeeline (paper), lecture2notes (HHousen), lecture-to-notes | review page, notes style |
| Concept map | markmap | cover concept tree |
| Prompt regression | promptfoo | judge runs |

Check each license before adding code or weights. Prefer reimplementing small algorithms over adding heavy runtimes.

## 10. Build order

Commit after each step with before/after contact sheets (render with `pdftoppm`) and the metrics table.

1. Phase 0 referee: gold alignment for one lecture, MaViLS import, 10-slide notes gold, pairwise judge.
2. Alignment: sentence DP + embedder benchmark + lexical mix; then LLM arbitration and off-topic filter.
3. Lecture brain with ID evidence and entailment checks, behind `ANNOTATION_MODE=brain`.
4. Page designer + validator with the design grammar.
5. Layout optimizer, text fitting, ink upgrade.
6. Cover, concept map, review page, viewer editing.
7. Judge: new path vs `legacy` on the gold set. Switch the default only if it wins on win rate and does not lose on
   faithfulness. Then delete or archive the unused engines.

## 11. Cost

Per 50-page lecture: 1 whole-lecture call, about 10-15 designer calls, about 10-15 critic calls, a few alignment
arbitration calls: roughly 25-35 strong-model calls with page images. This does not fit the free tier (about 20
requests per day per model), so plan for a paid key. Report actual calls and tokens per run.
