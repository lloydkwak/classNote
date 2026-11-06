import json
from pathlib import Path

import numpy as np
import pymupdf
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.evidence import EvidenceGraph, EvidenceNode, EvidenceEdge
from app.main import app
from app.models import Alignment, AlignedWindow, Annotations
from app.pipeline import run as pipeline, grounded_notes
from app.pipeline.export_pdf import export_annotated_pdf
from app.pipeline.layout import gutter_width
from app.grounding.document import display_box, extract_display_pdf, build_registries
from app.grounding.ipc import WorkerFailure
from ground_page import synthetic_fixture
from verify_phase_d import FixtureContentProvider, FixtureVLM


@pytest.fixture
def grounded_job(tmp_path,monkeypatch):
    inputs=tmp_path/"inputs";inputs.mkdir()
    pdf,_,items,segments,_,_=synthetic_fixture(inputs)
    provider=FixtureContentProvider(items)
    def aligned(windows,extraction):
        return Alignment(pages=[1],windows=[AlignedWindow(**w.model_dump(),page=1,raw_page=1,
                                                          confidence=1,similarities=[1]) for w in windows])
    class Tracking(FixtureVLM):
        calls=0
        def select_targets(self,*a,**kw):
            type(self).calls+=1
            return super().select_targets(*a,**kw)
    monkeypatch.setattr(pipeline,"transcribe",lambda *a,**k:segments)
    monkeypatch.setattr(pipeline,"align_windows",aligned)
    monkeypatch.setattr("app.pipeline.rich_notes.get_provider",lambda:provider)
    monkeypatch.setattr(grounded_notes,"PROVIDER_FACTORY",Tracking)
    job=pipeline.create_job(pdf,None,visual_grounding=True)
    return job,pipeline.job_dir(job),Tracking


def test_rich_grounding_actions_graph_and_pdf_export(grounded_job):
    job,folder,provider=grounded_job
    before=(folder/"input.pdf").read_bytes()
    pipeline.run_pipeline(job)
    assert provider.calls==7
    notes=pipeline.read_detailed_notes(job)
    assert len(notes.items)==7 and sum(v.reason=="source_quote_not_found" for v in notes.validation)==1
    graph=EvidenceGraph.model_validate_json((folder/"evidence_graph.json").read_text())
    assert graph.metrics["grounding_status_counts"]=={"accepted":5,"unmatched":1,"ambiguous":1}
    assert graph.metrics["annotations_rendered"]==7
    assert graph.metrics["lecture_semantic_coverage"] is None
    assert graph.export["file"]=="annotated.pdf"
    incoming={n.id:[e for e in graph.edges if e.target==n.id] for n in graph.nodes}
    kinds={n.id:n.kind for n in graph.nodes}
    for rendered in [n for n in graph.nodes if n.kind=="rendered_annotation"]:
        action=incoming[rendered.id][0].source
        decision=incoming[action][0].source
        knowledge=incoming[decision][0].source
        assert (kinds[action],kinds[decision],kinds[knowledge])==("annotation_action","grounding_decision","knowledge")
        assert any(kinds[e.source]=="speech_segment" for e in incoming[knowledge])
    with pymupdf.open(folder/"annotated.pdf") as doc:
        embedded=json.loads(doc.embfile_get("classnote-evidence.json"))
        assert len(embedded["annotations"])==7
        ink=[a for p in doc for a in p.annots()]
        assert ink and all("provenance" in json.loads(a.info["content"]) for a in ink)
    assert (folder/"input.pdf").read_bytes()==before
    annotations=pipeline.density_annotations(job,"balanced")
    for p in annotations.pages:
        for n in p.notes:
            assert n.content_id==n.provenance.knowledge_id
            assert n.start==n.provenance.timestamp_range[0]
            if n.provenance.grounding_status in {"ambiguous","unmatched"}:
                assert not n.bboxes and n.provenance.target_element_id is None and n.sketch.mark=="none"


def test_density_style_and_resume_reuse_grounding(grounded_job,monkeypatch):
    job,folder,provider=grounded_job
    pipeline.run_pipeline(job)
    saved=(folder/grounded_notes.GROUNDING_NAME).read_bytes()
    calls=provider.calls
    first=pipeline.density_annotations(job,"balanced")
    first_ids=[n.id for p in first.pages for n in p.notes]
    for density in ("minimal","balanced","detailed"):
        pipeline.density_annotations(job,density)
        pipeline.export_density(job,density)
    monkeypatch.setenv("NOTE_FONT","nanum-pen")
    pipeline.export_density(job,"balanced")
    assert (folder/grounded_notes.GROUNDING_NAME).read_bytes()==saved and provider.calls==calls
    monkeypatch.setenv("NOTE_FONT","pretendard")
    pipeline.run_pipeline(job,from_step="notes")
    assert provider.calls==calls
    assert [n.id for p in pipeline.density_annotations(job,"balanced").pages for n in p.notes]==first_ids


@pytest.mark.parametrize("failure",["worker_start_failed","error:InputBudgetExceeded","oom:OutOfMemoryError"])
def test_vlm_failure_keeps_rich_notes_and_existing_planner(grounded_job,monkeypatch,failure):
    job,folder,_=grounded_job
    class Offline(FixtureVLM):
        def select_targets(self,*a,**kw):
            raise WorkerFailure(failure)
    monkeypatch.setattr(grounded_notes,"PROVIDER_FACTORY",Offline)
    pipeline.run_pipeline(job)
    assert len(pipeline.read_detailed_notes(job).items)==7
    annotations=pipeline.density_annotations(job,"balanced")
    assert sum(len(p.notes) for p in annotations.pages)==7
    assert all(n.provenance.target_element_id is None and n.provenance.fallback_reason==failure
               for p in annotations.pages for n in p.notes)
    assert (folder/"annotated.pdf").read_bytes().startswith(b"%PDF")


@pytest.mark.parametrize("changed",["input.pdf","segments.json","alignment.json"])
def test_changed_sources_never_serve_stale_grounded_pdf(grounded_job,changed):
    job,folder,provider=grounded_job
    pipeline.run_pipeline(job)
    calls=provider.calls
    path=folder/changed
    if changed=="input.pdf":
        path.write_bytes(path.read_bytes()+b"\n%changed-source\n")
    else:
        value=json.loads(path.read_text())
        if changed=="segments.json": value[0]["text"]+=" changed"
        else: value["windows"][0]["confidence"]=0.25
        path.write_text(json.dumps(value))
    with TestClient(app) as client:
        assert client.get(f"/api/jobs/{job}/annotations").status_code==409
        assert client.get(f"/api/jobs/{job}/annotated.pdf").status_code==409
        assert client.get(f"/api/jobs/{job}/page-audio").status_code==200
    assert provider.calls==calls


def test_api_provenance_registry_mismatch_and_pdf_cache(grounded_job):
    job,folder,_=grounded_job
    pipeline.run_pipeline(job)
    with TestClient(app) as client:
        data=client.get(f"/api/jobs/{job}/annotations").json()
        note=next(n for p in data["pages"] for n in p["notes"] if n["provenance"]["grounding_status"]=="accepted")
        assert note["provenance"]["source_segment_ids"]
        assert note["provenance"]["registry_fingerprint"] and note["quote"]
        assert client.get(f"/api/jobs/{job}/detailed-notes").json()["items"]
        assert client.get(f"/api/jobs/{job}/annotated.pdf").status_code==200
    # A result's claimed registry identity cannot authorize another registry's geometry.
    path=folder/grounded_notes.GROUNDING_NAME
    saved=json.loads(path.read_text())
    record=next(r for r in saved["records"].values() if r["decision"]["status"]=="accepted")
    record["response"]["grounding"]["registry_hash"]="wrong-registry"
    path.write_text(json.dumps(saved))
    annotations=pipeline.density_annotations(job,"balanced")
    assert any(n.provenance.fallback_reason=="registry_hash_mismatch" and n.provenance.target_element_id is None
               for p in annotations.pages for n in p.notes)
    exported=pipeline.export_density(job,"balanced")
    exported.write_bytes(b"corrupted cached export")
    assert pipeline.export_density(job,"balanced").read_bytes().startswith(b"%PDF")


def test_prompt_and_validation_invalidation_scope(grounded_job,monkeypatch):
    job,folder,provider=grounded_job
    pipeline.run_pipeline(job)
    calls=provider.calls
    monkeypatch.setattr(grounded_notes,"VALIDATION_VERSION","new-validation")
    assert any(n.provenance.grounding_status=="accepted" for p in pipeline.density_annotations(job,"balanced").pages for n in p.notes)
    assert provider.calls==calls
    monkeypatch.setattr(grounded_notes,"SELECTION_VERSION","changed-prompt")
    annotations=pipeline.density_annotations(job,"balanced")
    assert all(n.provenance.target_element_id is None for p in annotations.pages for n in p.notes)
    assert provider.calls==calls
    pipeline.run_pipeline(job,from_step="notes")
    assert provider.calls>calls


def test_partial_resume_reuses_validated_checkpoints(grounded_job):
    job,folder,provider=grounded_job
    pipeline.run_pipeline(job)
    path=folder/grounded_notes.GROUNDING_NAME
    saved=json.loads(path.read_text())
    saved["status"]="partial"
    saved["records"].pop(next(iter(saved["records"])))
    path.write_text(json.dumps(saved))
    calls=provider.calls
    pipeline.run_pipeline(job,from_step="notes")
    assert provider.calls==calls+1
    assert json.loads(path.read_text())["telemetry"]["checkpoint_hits"]==6


def test_v2_versioned_mentions_and_cleanup_failure_preserve_content(grounded_job,monkeypatch):
    job,folder,provider=grounded_job
    monkeypatch.setenv("ANNOTATION_ENGINE","v2")
    monkeypatch.setattr("app.pipeline.pdf_extract._load_ocr",lambda:None)
    monkeypatch.setattr(provider,"close",lambda self:(_ for _ in ()).throw(OSError("cleanup")))
    pipeline.run_pipeline(job)
    saved=json.loads((folder/grounded_notes.GROUNDING_NAME).read_text())
    mentions=json.loads((folder/"mentions.json").read_text())
    assert mentions["registry_hashes"]["1"]==grounded_notes.registry_fingerprint(
        build_registries(folder/"input.pdf")[0])
    assert not any("stale_or_unversioned" in warning for warning in saved.get("warnings",[]))
    assert "worker_cleanup:OSError" in saved["warnings"]
    assert len(pipeline.read_detailed_notes(job).items)==7
    assert (folder/"annotated.pdf").exists()


def test_invalid_graph_segment_and_dangling_reference_are_rejected(grounded_job):
    job,folder,_=grounded_job
    with pytest.raises(ValidationError,match="dangling"):
        EvidenceGraph(job_id=job,revision="x",sources={},nodes=[EvidenceNode(id="a",kind="knowledge")],
                      edges=[EvidenceEdge(source="missing",target="a",relation="supports")],metrics={})
    pipeline.run_pipeline(job)
    content=pipeline.read_detailed_notes(job)
    item=content.items[0].model_copy(update={"segment_ids":[999]})
    registry=build_registries(folder/"input.pdf")[0]
    with pytest.raises(ValueError,match="invalid_segment"):
        grounded_notes.local_request(item,registry,None,None,[],pipeline.read_alignment(job))


@pytest.mark.parametrize("rotation",[0,90,180,270])
def test_export_matches_original_crop_rotation_and_registry(rotation,tmp_path,monkeypatch):
    source=tmp_path/"source.pdf"
    with pymupdf.open() as doc:
        p=doc.new_page(width=600,height=400)
        p.draw_rect(pymupdf.Rect(110,100,150,130),color=None,fill=(1,0,0))
        p.insert_text((110,160),"alpha = 1",fontsize=20)
        p.set_cropbox(pymupdf.Rect(70,30,570,370));p.set_rotation(rotation)
        expected=pymupdf.Rect(40,70,80,100)*p.rotation_matrix
        gutter=gutter_width(p.rect.width)
        doc.save(source)
    before=source.read_bytes()
    monkeypatch.setattr("app.pipeline.pdf_extract._load_ocr",lambda:None)
    extraction=extract_display_pdf(source,geometry=True)
    registry=build_registries(source)[0]
    block=next(e for e in registry.elements if e.kind=="block")
    assert extraction.pages[0].blocks[0].bbox==pytest.approx(display_box(registry,block.bbox),abs=1e-5)
    native_word=next(e for e in registry.elements if e.kind=="word" and e.text=="alpha")
    word=next(w for w in extraction.pages[0].words if w.word_id==native_word.id)
    assert word.bbox==pytest.approx(display_box(registry,native_word.bbox),abs=1e-5)
    target=tmp_path/"out.pdf"
    export_annotated_pdf(source,Annotations(job_id="rotation",pages=[]),target)
    with pymupdf.open(target) as doc:
        p=doc[0].get_pixmap(alpha=False)
        image=np.frombuffer(p.samples,dtype=np.uint8).reshape(p.height,p.width,3)
        ys,xs=np.where((image[:,:,0]>220)&(image[:,:,1]<30)&(image[:,:,2]<30))
        actual=(xs.min()-gutter,ys.min(),xs.max()+1-gutter,ys.max()+1)
        assert actual==pytest.approx(tuple(expected),abs=1)
    assert source.read_bytes()==before
    # Canonical geometry follows the same transform into actual PDF ink, including gutters.
    from app.models import Note,AnnotationProvenance
    from app.pipeline.consolidate import consolidate
    provenance=AnnotationProvenance(action_id="action",knowledge_id="k",evidence_id="e",
        source_segment_ids=[0],timestamp_range=(1,2),pdf_sha256=registry.pdf_sha256,
        registry_fingerprint=grounded_notes.registry_fingerprint(registry),
        target_element_id=native_word.id,target_kind="word",grounding_status="accepted",decision_id="d")
    note=Note(content_id="k",page=1,block_ids=[],type="explanation",note="A parameter",quote="alpha",
              language="en",importance=5,start=1,end=2,target_bboxes=[word.bbox],target_mark="underline",
              provenance=provenance)
    annotations=consolidate([note],extraction,"rotation")
    expected_strokes=annotations.pages[0].notes[0].sketch.strokes
    export_annotated_pdf(source,annotations,tmp_path/"grounded.pdf")
    with pymupdf.open(tmp_path/"grounded.pdf") as doc:
        page=doc[0]
        ink=next(page.annots())
        assert json.loads(ink.info["content"])["provenance"]["target_element_id"]==native_word.id
        assert np.array(ink.vertices)==pytest.approx(
            np.array([[(x+gutter,y) for x,y in stroke] for stroke in expected_strokes]),abs=1e-4)
    assert source.read_bytes()==before
