# Annotation v2: notes that look written by the best student in the class

Read this whole file, `CLAUDE.md`, and `docs/annotation_prototype.py` before starting. The prototype
renders a hand-placed plan onto the test slides and shows the target look: highlighter behind
the words, wobbly underlines and circles, short pen notes placed in free space right next to
what they explain, a sticky note for strong emphasis, and a cover page. Your job is to make the
pipeline produce that automatically, from the recording, for any lecture.

## The core principle

**The LLM decides what to mark and what to write. Deterministic code decides where and how
it is drawn.** Never ask the model for coordinates or free-form drawings. Models are poor at
pixel geometry and good at judgment. Give the model a small, fixed vocabulary of annotation
actions (its "tools") that point at ids of real things on the slide: words, lines, blocks,
figures. Code validates every action, lays it out without collisions, and draws it with
hand-drawn ink. This is what turns "a line somewhere on the right page" into "the exact phrase
the professor was talking about, highlighted, with a note beside it".

Keep the v1 engine working. Add v2 behind `ANNOTATION_ENGINE=v2` and switch the default only
after the evaluation in step 7 shows it is better. Reuse the existing alignment, caching,
quota handling, quote checks, and language rules.

## 1. Ground truth geometry (no LLM)

Extend `pdf_extract.py`:

- **Words:** `page.get_text("words")` gives every word's box with block, line, and word
  numbers. Store words with ids like `p3_w17`, grouped into lines (`p3_l4`) and blocks
  (existing ids). Normalize boxes to 0-1 like the rest of the code.
- **Text inside images:** run OCR on embedded images and on pages with `low_text`, so text in
  screenshots and scanned slides can be marked too. Use RapidOCR (ONNX, PP-OCR models with
  Korean support) as an optional dependency; skip with a log line if it isn't installed.
  OCR words get `source="ocr"`.
- **Free-space map:** render each page at low resolution, mark pixels that contain ink,
  dilate a little, and compute the largest empty rectangles. Notes will be placed in these
  first, before the gutters.
- **Marked page image for the model:** render the page with small numbered tags on every
  line and figure (Set-of-Mark prompting, from the Microsoft SoM paper), so the model can
  refer to what it sees by id. Word-level ids go in the accompanying text list, not on the
  image, to keep the image readable.

## 2. Transcript evidence (no LLM)

- **Word timestamps:** turn on `word_timestamps=True` in faster-whisper for uploads and live
  mode, and store words with start, end, and probability. Every annotation will get the
  exact time of the words it is based on, so the viewer can replay notes as they were said.
  WhisperX forced alignment can sharpen timestamps further; add it only as an optional extra
  if faster-whisper's word times are visibly off.
- **Mentions:** find where spoken words refer to words on the current slide. Use rapidfuzz
  for fuzzy matching, and compare Korean at the jamo level (decompose Hangul syllables with
  Unicode arithmetic, no dependency needed) so misrecognitions still match ("김알고사" vs
  "기말고사"). Also match transliterations ("베이스 케이스" vs "base case") using the
  glossary from step 3. Output: `{word_ids, time, spoken_text, score}`. These are hard
  evidence of what the professor pointed at, and they are given to the model and used by
  validation.

## 3. Lecture-level pass (one text-only LLM call)

Input: each page's title and text, the aligned transcript (condensed if needed), and the
alignment. Output (pydantic schema, every claim with a verified quote):

- lecture title and a 5-8 step flow of the lecture, each step with its page range,
- key concepts with one-line definitions grounded in the slides or the speech,
- glossary: spoken variants and misrecognitions mapped to the slide's spelling,
- professor emphasis list and exam points, each with quote, time, page,
- things said that are not on any slide, and open questions the professor raised.

Uses: the cover page, consistent colors (a key concept is highlighted the same way
everywhere), the glossary for mentions, and context for the per-page pass.

## 4. Per-page planning: the annotation tools

Batch 3-5 pages per request to save quota. For each page, send the marked page image, the
word/line/figure lists with ids, the transcript span with word times, the mentions, and the
lecture-level context. The model returns an action list validated by a pydantic schema
(use Gemini's `response_schema`; function calling is not needed).

Vocabulary (each action: `quote` verbatim, `importance` 1-5, short `reason` for debugging):

| Action | Target | Use |
|---|---|---|
| `highlight` | word range or line | key term, definition, what the professor stressed. Role: `key_term`, `emphasis` |
| `underline` | word range | style `single`, `double` (exam), `wavy` (caution) |
| `circle` / `box` | word range, figure, figure region | the thing being pointed at |
| `bracket` | line range | groups several lines the professor explained together |
| `strike` | word range | the professor corrected or deprecated the slide |
| `mark` | word range, line, figure | small symbol: `star`, `check`, `exclamation`, `question`, or a step number |
| `connect` | two targets | arrow between related things on the slide, with an optional short label |
| `note` | target or page | pen text, condensed, with an ink role and a hint `near` or `margin` |
| `insert` | after a word | caret insertion of a short missing piece, like "^ O(n) 메모리" |
| `sticky` | target or page | strong professor emphasis only, at most one per page |

Targets are ids, never coordinates. For pointing at part of a chart ("이 꼭짓점"), allow a
`figure_region` as a box inside the figure in 0-1000 units relative to the figure, which
Gemini's spatial understanding handles well; code clamps it to the figure.

Ink roles map to a fixed pen box, shown in a legend on the cover page: explanation blue,
exam red, example green, question purple, key-term yellow highlighter, emphasis orange
highlighter, correction red. Keep the existing note text rules: condensed note-taking style,
type prefixes, the language judgment rule, technical terms not translated, nothing that the
quote doesn't say.

Prompt the model as the best student in the class: mark only what the professor actually
referred to or explained, prefer marking words that appear in the mentions, write only what
isn't already on the slide, and leave a slide clean if little was said about it. Budgets per
page: at most 6 marks, 4 pen notes, and 1 sticky.

## 5. Validation (code)

- Every id exists on that page. Drop actions with unknown ids.
- A mark's target must be supported: its words appear in the quote, or a mention of them
  falls in the quote's time range. Otherwise turn it into a page-level note or drop it.
- Keep the existing checks: verbatim quote, numbers in the note appear in the quote, exam
  hints need exam wording (now including the glossary's misrecognitions).
- Times come from the quote's word timestamps, not from the model.
- Merge duplicates, enforce budgets by importance, and record every drop with a reason in
  the job folder for the debug screen.

## 6. Layout and ink (`layout.py` stays the single source of geometry)

**Placement.** For each note and sticky, generate candidate positions: free-space rectangles
inside the slide near the target first, then the gutters. Score each by distance to the
target, crossings of its leader with slide text, overlap with other notes, and reading order
(notes for earlier lines sit above notes for later ones). Place greedily by importance, then
run a short local search to fix leftovers. Hard rules: notes and stickies never cover slide
text or figures, never overlap each other, and stay inside the sheet. Wrap text to the width
of the chosen space. Seed all randomness per annotation so results are stable across runs.

**Leaders.** Short, slightly curved arrows from the note's nearest edge to the target's
nearest edge. Try a few control points and keep the path with the fewest text crossings. No
arrow when the note sits right next to its target.

**Ink.**
- Highlighter is drawn *behind* the text: in the PDF with `Shape.commit(overlay=False)`
  (verified in testing: the text stays crisp), in the viewer with `mix-blend-mode: multiply`.
  Slightly taller than the words, ragged ends, one stroke per line for multi-line targets.
- Underlines, circles, boxes, brackets, and strikes use RoughJS-style algorithms ported to
  Python: bowing, two passes with small offsets, overshoot where a circle closes. The
  rough-notation library (built on RoughJS) is a good reference for the look and its
  options (padding, iterations, multiline, bracket sides).
- Turn pen polylines into variable-width filled outlines with the Python port of
  perfect-freehand (BigBlueButton's `perfect_freehand`, `get_stroke`), so lines look like a
  pen with pressure rather than a constant-width path. Check its license and pip name before
  adding it; if it doesn't fit, implement the same idea directly.
- The viewer SVG and `annotated.pdf` keep drawing exactly the geometry `layout.py` produces.

## 7. Cover page and summary

Insert a cover page before page 1 in `annotated.pdf` and as page 0 in the viewer: title,
length, counts; the lecture flow with page links (`page.insert_link`); exam points with page
and time; key concepts; a few of the professor's own words; the pen legend. Optionally add a
closing page that collects every sticky and exam point. See the prototype for a starting
layout, and use the same font and ink colors as the notes.

Rendering check: in my test, MuPDF's rasterizer drew Pretendard's symbols (→ ★ ( ) ≈) as
wrong glyphs, while Poppler (`pdftoppm`) drew the same file correctly and text extraction
was correct. So the file is fine; verify PDF output with `pdftoppm` or a browser, not with
PyMuPDF pixmaps, and keep text drawing on the existing `text_runs` path.

## 8. Viewer

- Draw the new action types from the same geometry, with hit areas that play from the
  annotation's first word.
- Replay mode: as the audio plays, each mark draws itself at the moment its words were
  spoken, like rough-notation's animation (stroke-dashoffset), with notes writing in after
  their mark. Presentation mode can reuse this.
- Layer chips from the legend to show or hide each ink role.

## 9. Optional critic pass

Behind a setting: render finished pages, send them in batches to a vision model, and ask for
concrete problems per page (wrong target, clutter, illegible, overlap) as structured output.
Apply automatic fixes only: drop, downgrade, or move. Report how many calls it costs.

## 10. Evaluation

- Golden set: hand-write the ideal action lists for 5 real slides in `samples/golden/`, as
  JSON in the action vocabulary. These also drive step 2 of the build order, so drawing
  quality can be developed without the LLM.
- Metrics: target precision and recall (word overlap with golden targets), share of notes
  supported by their quote, overlaps (must be 0), leader crossings, marks per page.
- A contact sheet of every annotated page (`pdftoppm`) for visual review, saved per run.

## Build order

Commit after each step, and show screenshots rendered with Poppler.

1. Word geometry, OCR, free-space map, word timestamps, mentions. No LLM.
2. Action schema, layout engine, and ink renderer, driven only by the golden JSON. Iterate
   until the 5 golden slides look as good as the prototype or better.
3. Lecture-level pass and the cover page.
4. Per-page planner with marked images, batched, plus validation.
5. Viewer: new actions, layers, replay, cover page.
6. Evaluation against the golden set, v1 vs v2, then switch the default if v2 wins.
7. Optional critic pass.

## Quota

Estimate and report LLM calls per lecture before running: 1 lecture-level call, about
pages/4 planning calls, plus captions and the optional critic. On the free tier (about 20
requests per day per model) a 50-page lecture is close to the limit, so tell me when a paid
key is needed rather than silently degrading.
