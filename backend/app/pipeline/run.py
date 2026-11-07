"""Run the pipeline for a job. Each step reads and writes JSON in the job folder.

Because each step's inputs are on disk, a run can resume from any step.
"""

import json
import logging
import shutil
import time
import uuid
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger(__name__)

from app.config import get_settings
from app.cache import hash_file
from app.models import Alignment, Annotations, Note, PageAudio, PdfExtraction, Segment, Window
from app.pipeline.audio import convert_to_mp3
from app.pipeline.consolidate import consolidate
from app.pipeline.figure_caption import caption_figures
from app.pipeline.export_pdf import EXPORT_NAME, export_annotated_pdf
from app.pipeline.page_align import align_windows
from app.pipeline.page_audio import build_page_audio
from app.pipeline.mentions import find_mentions
from app.pipeline.pdf_extract import add_geometry, extract_pdf
from app.pipeline.sentence_align import SentenceAlignConfig, align_sentence_windows, build_sentence_windows
from app.pipeline.stt import transcribe
from app.pipeline.windowing import build_windows
from app.pipeline.rich_notes import DetailedNotes, Density, extract_content, plan_annotations, planning_report

JOB_OPTIONS_NAME = "job.json"
PDF_NAME = "input.pdf"
AUDIO_SOURCE_PREFIX = "audio_source"
AUDIO_NAME = "audio.mp3"
EXTRACTION_NAME = "extraction.json"
SEGMENTS_NAME = "segments.json"
WINDOWS_NAME = "windows.json"
ALIGNMENT_NAME = "alignment.json"
PAGE_AUDIO_NAME = "page_audio.json"
MENTIONS_NAME = "mentions.json"
NOTES_NAME = "notes.json"
CONTENT_NAME = "detailed_notes.json"
NOTES_COMPLETE_NAME = "notes_complete.json"
ANNOTATIONS_NAME = "annotations.json"
REPLACE_ATTEMPTS = 10
REPLACE_RETRY_SECONDS = 0.1


def job_dir(job_id: str) -> Path:
    return get_settings().data_dir / "jobs" / job_id


def create_job(
    pdf: Path, audio: Path | None, job_id: str | None = None, stt_model: str | None = None,
    visual_grounding: bool | None = None, annotation_mode: str | None = None,
) -> str:
    """Copy the inputs into a new job folder and return its id."""
    annotation_mode=annotation_mode or get_settings().annotation_mode
    if annotation_mode not in {"legacy","ai_planner","visual_agent"}:raise ValueError("unsupported_annotation_mode")
    job_id = job_id or uuid.uuid4().hex[:12]
    folder = job_dir(job_id)
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(pdf, folder / PDF_NAME)
    if audio is not None:
        shutil.copyfile(audio, folder / f"{AUDIO_SOURCE_PREFIX}{audio.suffix.lower()}")
    (folder / JOB_OPTIONS_NAME).write_text(json.dumps({"stt_model": stt_model,
        "visual_grounding":get_settings().visual_grounding if visual_grounding is None else visual_grounding,
        "annotation_mode":annotation_mode}), encoding="utf-8")
    return job_id


def _job_stt_model(folder: Path) -> str | None:
    path = folder / JOB_OPTIONS_NAME
    return json.loads(path.read_text(encoding="utf-8"))["stt_model"] if path.exists() else None


def _write_models(path: Path, models: list) -> None:
    path.write_text(
        json.dumps([model.model_dump() for model in models], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _read_list(path: Path, model: type) -> list:
    return [model(**row) for row in json.loads(path.read_text(encoding="utf-8"))]


def _read_extraction(folder: Path) -> PdfExtraction:
    return PdfExtraction.model_validate_json((folder / EXTRACTION_NAME).read_text(encoding="utf-8"))


def _step_extract(folder: Path) -> None:
    if _grounding_enabled(folder):
        from app.grounding.document import extract_display_pdf
        extraction=extract_display_pdf(folder/PDF_NAME)
    else:
        extraction = extract_pdf(folder / PDF_NAME)
    (folder / EXTRACTION_NAME).write_text(
        extraction.model_dump_json(indent=2), encoding="utf-8"
    )


def _step_caption(folder: Path) -> None:
    extraction = _read_extraction(folder)
    caption_figures(folder / PDF_NAME, extraction)
    (folder / EXTRACTION_NAME).write_text(
        extraction.model_dump_json(indent=2), encoding="utf-8"
    )


def _step_transcribe(folder: Path) -> None:
    sources = sorted(folder.glob(f"{AUDIO_SOURCE_PREFIX}.*"))
    audio = convert_to_mp3(sources[0], folder / AUDIO_NAME) if sources else None
    if _grounding_enabled(folder):
        segments = transcribe(audio, _read_extraction(folder).key_terms, _job_stt_model(folder),serialize_gpu=True)
    else:
        segments = transcribe(audio, _read_extraction(folder).key_terms, _job_stt_model(folder))
    _write_models(folder / SEGMENTS_NAME, segments)


def _step_window(folder: Path) -> None:
    windows = build_windows(_read_list(folder / SEGMENTS_NAME, Segment))
    _write_models(folder / WINDOWS_NAME, windows)


def _step_align(folder: Path) -> None:
    extraction = _read_extraction(folder)
    if get_settings().alignment_version == "v2":
        segments = _read_list(folder / SEGMENTS_NAME, Segment)
        if segments and not any(s.words for s in segments):
            log.warning(
                "ALIGNMENT_VERSION=v2 but no word timestamps in segments.json. "
                "Re-run the transcribe step (faster-whisper emits word timestamps): "
                "uv run python scripts/run_pipeline.py --job-id %s --from-step transcribe",
                folder.name,
            )
        windows = build_sentence_windows(segments)
        sa_config = SentenceAlignConfig.from_env()
        llm = None
        if sa_config.arbitration_threshold > 0:
            try:
                from app.llm import get_provider
                llm = get_provider()
            except Exception as exc:
                log.warning("LLM arbitration requested but provider unavailable: %s", exc)
        alignment = align_sentence_windows(
            windows, extraction, config=sa_config,
            llm=llm, pdf_path=folder / PDF_NAME,
        )
    else:
        windows = _read_list(folder / WINDOWS_NAME, Window)
        alignment = align_windows(windows, extraction)
    (folder / ALIGNMENT_NAME).write_text(alignment.model_dump_json(indent=2), encoding="utf-8")
    _step_page_audio(folder)


def _write_atomically(path: Path, text: str) -> None:
    """Replace a file that may be read at any moment, as live annotations are."""
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(text, encoding="utf-8")
    for attempt in range(REPLACE_ATTEMPTS):
        try:
            temporary.replace(path)
            return
        except PermissionError:  # On Windows, while the file is being served.
            if attempt == REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(REPLACE_RETRY_SECONDS)


def _read_alignment(folder: Path) -> Alignment:
    return Alignment.model_validate_json((folder / ALIGNMENT_NAME).read_text(encoding="utf-8"))


def _step_page_audio(folder: Path) -> None:
    page_audio = build_page_audio(_read_alignment(folder))
    _write_atomically(folder / PAGE_AUDIO_NAME, page_audio.model_dump_json(indent=2))


def _step_evidence(folder: Path) -> None:
    """For ANNOTATION_ENGINE=v2: the slide's words and free space, and what was said about them."""
    if get_settings().annotation_engine != "v2":
        return
    extraction = _read_extraction(folder)
    if _grounding_enabled(folder):
        from app.grounding.document import extract_display_pdf
        geometry=extract_display_pdf(folder/PDF_NAME,geometry=True)
        for page,source in zip(extraction.pages,geometry.pages):
            page.words,page.lines,page.free_rects=source.words,source.lines,source.free_rects
    else:
        add_geometry(folder / PDF_NAME, extraction)
    (folder / EXTRACTION_NAME).write_text(extraction.model_dump_json(indent=2), encoding="utf-8")
    mentions = find_mentions(
        _read_alignment(folder), extraction, _read_list(folder / SEGMENTS_NAME, Segment)
    )
    if _grounding_enabled(folder):
        from app.grounding.document import build_registries,registry_fingerprint
        stored={"schema_version":1,"mentions":[m.model_dump(mode="json") for m in mentions],
                "registry_hashes":{str(r.page):registry_fingerprint(r) for r in build_registries(folder/PDF_NAME)}}
        _write_atomically(folder/MENTIONS_NAME,json.dumps(stored,ensure_ascii=False,indent=2))
    else:
        _write_models(folder / MENTIONS_NAME, mentions)


def _step_notes(folder: Path) -> None:
    content = extract_content(
        _read_alignment(folder),
        _read_extraction(folder),
        _read_list(folder / SEGMENTS_NAME, Segment),
    )
    content.alignment_hash = hash_file(folder / ALIGNMENT_NAME)
    content.extraction_hash = hash_file(folder / EXTRACTION_NAME)
    content.transcript_hash = hash_file(folder / SEGMENTS_NAME)
    content.pdf_hash = hash_file(folder / PDF_NAME)
    content.provenance={"visual_grounding":_grounding_enabled(folder)}
    _write_atomically(folder / CONTENT_NAME, content.model_dump_json(indent=2))
    from app.annotation_agent.pipeline import configured_mode
    mode=configured_mode(folder)
    if get_settings().overview_mode=='synthesis':
        try:
            from app.pipeline.overview_synthesis import prepare_synthesis
            from app.grounding.document import build_registries
            prepare_synthesis(folder,content,_read_list(folder/SEGMENTS_NAME,Segment),build_registries(folder/PDF_NAME))
        except Exception as error:
            from app.pipeline.grounded_notes import write_json
            write_json(folder/'overview_synthesis.json',{'status':'fallback','reason':type(error).__name__})
    if mode!='legacy':
        from app.annotation_agent.pipeline import prepare_agent
        prepare_agent(folder,content,_read_list(folder/SEGMENTS_NAME,Segment),_read_alignment(folder),mode=mode)
    elif content.provenance["visual_grounding"]:
        from app.pipeline.grounded_notes import prepare_grounding
        prepare_grounding(folder,content,_read_alignment(folder),_read_list(folder/SEGMENTS_NAME,Segment))
    notes,_,_ = _plan_notes(folder,content,"balanced")
    complete = content.complete
    _write_models(folder / NOTES_NAME, notes)
    (folder / NOTES_COMPLETE_NAME).write_text(json.dumps(complete), encoding="utf-8")


def _step_consolidate(folder: Path) -> None:
    if (folder/CONTENT_NAME).exists():
        annotations=density_annotations(folder.name,"balanced")
        _write_atomically(folder / ANNOTATIONS_NAME, annotations.model_dump_json(indent=2))
        return
    notes = _read_list(folder / NOTES_NAME, Note)
    annotations = consolidate(
        notes, _read_extraction(folder), folder.name,
        max_notes_per_page=len(notes) if (folder / CONTENT_NAME).exists() else 5,
    )
    complete = json.loads((folder / NOTES_COMPLETE_NAME).read_text(encoding="utf-8"))
    annotations.incomplete = not complete
    if (folder / CONTENT_NAME).exists():
        annotations.planning = planning_report(read_detailed_notes(folder.name), annotations, "balanced")
    _write_atomically(folder / ANNOTATIONS_NAME, annotations.model_dump_json(indent=2))


def _step_export(folder: Path) -> None:
    if (folder/CONTENT_NAME).exists():
        export_density(folder.name,"balanced",folder/EXPORT_NAME)
        return
    annotations = Annotations.model_validate_json(
        (folder / ANNOTATIONS_NAME).read_text(encoding="utf-8")
    )
    export_annotated_pdf(folder / PDF_NAME, annotations, folder / EXPORT_NAME)


# (step name, status shown to the user, function), in run order.
STEPS: list[tuple[str, str, Callable[[Path], None]]] = [
    ("extract", "extracting_pdf", _step_extract),
    ("caption", "extracting_pdf", _step_caption),
    ("transcribe", "transcribing", _step_transcribe),
    ("window", "aligning", _step_window),
    ("align", "aligning", _step_align),
    ("page_audio", "aligning", _step_page_audio),
    ("evidence", "aligning", _step_evidence),
    ("notes", "writing_notes", _step_notes),
    ("consolidate", "writing_notes", _step_consolidate),
    ("export", "writing_notes", _step_export),
]
STEP_NAMES = [name for name, _, _ in STEPS]


def run_pipeline(
    job_id: str,
    from_step: str | None = None,
    to_step: str | None = None,
    on_status: Callable[[str], None] = lambda status: None,
) -> None:
    """Run steps `from_step` through `to_step`, inclusive, in order."""
    folder = job_dir(job_id)
    start = STEP_NAMES.index(from_step) if from_step else 0
    stop = STEP_NAMES.index(to_step) if to_step else len(STEPS) - 1
    for name, status, function in STEPS[start : stop + 1]:
        on_status(status)
        function(folder)


def read_annotations(job_id: str) -> Annotations:
    path = job_dir(job_id) / ANNOTATIONS_NAME
    return Annotations.model_validate_json(path.read_text(encoding="utf-8"))


def density_annotations(job_id: str, density: Density, layout: str | None = None, ink_style: str = "natural") -> Annotations:
    folder = job_dir(job_id)
    content_path = folder / CONTENT_NAME
    if not content_path.exists():
        from app.pipeline.overview import attach_overview
        return attach_overview(folder,read_annotations(job_id))
    content = read_detailed_notes(job_id)
    extraction = _read_extraction(folder)
    layout=layout or get_settings().layout_engine
    if layout not in {"v1","v2"} or ink_style not in {"clean","natural","highlighter"}:
        raise ValueError("unsupported layout or ink style")
    if layout=="v2":
        from app.pipeline.layout_v2 import enrich_geometry
        enrich_geometry(folder/PDF_NAME,extraction)
    notes,actions,manifest = _plan_notes(folder,content,density,allow_overflow=layout=="v2")
    annotations = consolidate(notes, extraction, job_id, max_notes_per_page=len(notes),layout_engine=layout,ink_style=ink_style)
    annotations.incomplete = not content.complete
    from app.annotation_agent.pipeline import configured_mode
    mode=configured_mode(folder)
    if mode!='legacy':
        annotations.agent_report=manifest.get('agent_report') if manifest else {'mode':mode,'status':'fallback','reason':'agent_missing_or_stale'}
    annotations.planning = planning_report(content, annotations, density)
    from app.pipeline.overview import attach_overview
    attach_overview(folder,annotations,content)
    if manifest is not None:
        from app.pipeline.grounded_notes import save_graph
        save_graph(folder,content,_read_list(folder/SEGMENTS_NAME,Segment),_read_alignment(folder),
                   actions,manifest,annotations,density)
        by_item={a.knowledge_id:a for a in actions}
        for row in annotations.planning:
            action=by_item.get(row["item_id"])
            if action:row["grounding"]=action.provenance.model_dump(mode="json")
            row["action_ids"]=[a.action_id for a in actions if a.knowledge_id==row["item_id"]]
    from app.pipeline.loss_audit import build_loss_audit
    annotations.loss_audit=build_loss_audit(folder,content,annotations)
    return annotations


def _grounding_enabled(folder):
    path=folder/JOB_OPTIONS_NAME
    options=json.loads(path.read_text()) if path.exists() else {}
    return bool(options.get("visual_grounding",get_settings().visual_grounding)) or options.get("annotation_mode")=="visual_agent"


def _plan_notes(folder,content,density,allow_overflow=False):
    extraction=_read_extraction(folder)
    from app.annotation_agent.pipeline import configured_mode,cached_plan
    mode=configured_mode(folder)
    if mode!='legacy':
        notes,actions,manifest,report=cached_plan(folder,content,extraction,density,mode)
        if manifest is not None:manifest['agent_report']=report
        return notes,actions,manifest
    if content.provenance.get("visual_grounding"):
        from app.pipeline.grounded_notes import plan_grounded
        return plan_grounded(folder,content,extraction,density,allow_overflow=allow_overflow)
    return plan_annotations(content,extraction,density,allow_overflow=allow_overflow),[],None


def export_density(job_id,density,output=None,layout=None,ink_style="natural"):
    folder=job_dir(job_id)
    annotations=density_annotations(job_id,density,layout,ink_style)
    import hashlib
    key=hashlib.sha256((hash_file(folder/PDF_NAME)+annotations.model_dump_json()
                       +hash_file(Path(__file__).with_name("export_pdf.py"))+hash_file(Path(__file__).with_name("overview.py"))+"export-v3-overview").encode()).hexdigest()[:20]
    target=output or folder/f"annotated-{density}-{key}.pdf"
    digest_path=target.with_suffix(".pdf.sha256")
    if (not target.exists() or not digest_path.exists()
            or digest_path.read_text()!=hash_file(target) or output is not None):
        temporary=target.with_name(f".{target.stem}.{uuid.uuid4().hex}.tmp.pdf")
        try:
            export_annotated_pdf(folder/PDF_NAME,annotations,temporary)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        _write_atomically(digest_path,hash_file(target))
    content=read_detailed_notes(job_id) if (folder/CONTENT_NAME).exists() else None
    from app.annotation_agent.pipeline import configured_mode
    if content is not None and (content.provenance.get("visual_grounding") or configured_mode(folder)!="legacy"):
        from app.pipeline.grounded_notes import save_graph
        _,actions,manifest=_plan_notes(folder,content,density,allow_overflow=annotations.layout_engine=="v2")
        if manifest is not None:save_graph(folder,content,_read_list(folder/SEGMENTS_NAME,Segment),_read_alignment(folder),
                   actions,manifest,annotations,density,
                   export={"file":target.name,"sha256":hash_file(target),"evidence_revision":annotations.grounding["revision"]})
    return target


def read_detailed_notes(job_id: str) -> DetailedNotes:
    folder = job_dir(job_id)
    content = DetailedNotes.model_validate_json((folder / CONTENT_NAME).read_text(encoding="utf-8"))
    if (content.alignment_hash != hash_file(folder / ALIGNMENT_NAME)
            or content.extraction_hash != hash_file(folder / EXTRACTION_NAME)
            or (content.transcript_hash and content.transcript_hash != hash_file(folder/SEGMENTS_NAME))
            or (content.pdf_hash and content.pdf_hash != hash_file(folder/PDF_NAME))):
        raise ValueError("정렬이 변경되었습니다. notes 단계부터 다시 실행해 주세요.")
    return content


def export_job(job_id: str) -> None:
    _step_export(job_dir(job_id))


def read_page_audio(job_id: str) -> PageAudio:
    """Older jobs and talks still being recorded have no file, so derive it from the alignment."""
    folder = job_dir(job_id)
    return build_page_audio(_read_alignment(folder))


def read_alignment(job_id: str) -> Alignment:
    path = job_dir(job_id) / ALIGNMENT_NAME
    return Alignment.model_validate_json(path.read_text(encoding="utf-8"))
