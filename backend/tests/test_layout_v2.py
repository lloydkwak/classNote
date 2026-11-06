import io
import json
import wave
from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import Block, Page
from app.pipeline import run as pipeline
from app.pipeline.layout import LayoutNote
from app.pipeline.layout_v2 import candidates, evaluate, layout_page_v2, route, safe_path
from test_evidence_pipeline import grounded_job  # existing production fixture, actual model mocked


def fixture_page():
    return Page(page=1,title="Math",width=800,height=500,blocks=[
        Block(block_id="formula",page=1,text="x = α + β",bbox=(.35,.4,.6,.48)),
        Block(block_id="caption",page=1,text="Loss curve",bbox=(.7,.81,.94,.87)),
        Block(block_id="figure",page=1,text="",kind="figure",bbox=(.7,.5,.94,.8)),
    ])


def test_free_space_candidates_collision_exclusions_and_caption():
    page=fixture_page()
    choices=candidates(pymupdf.Rect(300,200,400,240),150,40,800,500,264,10,[(.01,.01,.3,.2)])
    assert {name for name,_ in choices}>={"left","right","above","below","left_margin","right_margin","free_space"}
    notes=[LayoutNote("formula","The parameter",False,[page.blocks[0]],"underline"),
           LayoutNote("figure","A figure explanation",False,[page.blocks[2]],"circle")]
    result=layout_page_v2(notes,page)
    assert result.metrics["original_text_occlusion_area"]==0
    assert result.metrics["annotation_box_overlap_count"]==0
    assert result.metrics["figure_caption_interference_count"]==0
    assert result.metrics["out_of_page_count"]==0
    assert result.sketches[1].mark=="none"  # no figure-wide circle or invented sub-target
    assert not result.sketches[1].arrow
    assert result.sketches[1].placement["mark_reason"]=="figure_detail_unverified"


def test_routing_obstacles_intersections_and_search_budget():
    obstacles=[pymupdf.Rect(40,0,60,25)]
    path,reason=route((0,10),(100,10),obstacles,[],(0,0,100,100),step=10)
    assert path and reason=="routed" and safe_path(path,obstacles)
    blocked,reason=route((0,10),(100,10),obstacles,[],(0,0,100,100),step=10,max_nodes=0)
    assert not blocked and reason=="search_budget_or_no_safe_route"
    assert evaluate([],fixture_page())["annotation_placement_rate"] is None


@pytest.mark.parametrize("style",["clean","natural","highlighter"])
def test_deterministic_strokes_and_full_text_overflow(style):
    page=fixture_page()
    notes=[LayoutNote(str(i),"A grounded explanation "*100,False,[page.blocks[0]],"circle") for i in range(10)]
    first=layout_page_v2(notes,page,style=style,max_route_nodes=0)
    second=layout_page_v2(notes,page,style=style,max_route_nodes=0)
    assert first==second
    assert first.output_height>page.height
    assert len(first.sketches)==10
    for original,sketch in zip(notes,first.sketches):
        assert "".join(sketch.lines).replace(" ","")==original.text.replace(" ","")
        assert sketch.placement["status"]=="placed"
    assert first.metrics["annotation_box_overlap_count"]==0
    assert first.metrics["out_of_page_count"]==0
    if style=="highlighter":assert all(s.stroke_opacity==.28 for s in first.sketches)


def test_production_v2_export_density_api_and_provenance(grounded_job):
    job,folder,provider=grounded_job
    pipeline.run_pipeline(job)
    raw=(folder/"grounding_results.json").read_bytes();calls=provider.calls
    source=(folder/"input.pdf").read_bytes()
    baseline=pipeline.density_annotations(job,"balanced","v1")
    result=pipeline.density_annotations(job,"balanced","v2")
    assert len(result.loss_audit)==1
    assert sum(len(p.notes) for p in result.pages)==sum(len(p.notes) for p in baseline.pages)
    assert result.note_font=="nanum-pen"
    assert all(n.provenance for p in result.pages for n in p.notes)
    assert all(n.sketch.mark=="bracket" for p in result.pages for n in p.notes if n.provenance.target_kind=="formula")
    for density in ("minimal","balanced","detailed"):
        file=pipeline.export_density(job,density,layout="v2",ink_style="clean")
        with pymupdf.open(file) as doc:
            assert len(doc)==1 and doc[0].get_text()
            assert len(json.loads(doc.embfile_get("classnote-evidence.json"))["annotations"])==7
    with TestClient(app) as client:
        value=client.get(f"/api/jobs/{job}/annotations?layout=v2&ink_style=highlighter").json()
        assert value["layout_engine"]=="v2"
        assert client.get(f"/api/jobs/{job}/annotated.pdf?layout=v2").content.startswith(b"%PDF")
        assert client.get(f"/api/jobs/{job}/annotations?layout=unknown").status_code==422
        assert client.get(f"/api/jobs/{job}/page-audio").status_code==200
    assert source==(folder/"input.pdf").read_bytes()
    assert provider.calls==calls and raw==(folder/"grounding_results.json").read_bytes()


@pytest.mark.parametrize("rotation",[0,90,180,270])
def test_cropped_rotated_v2_export_preserves_geometry(rotation,tmp_path):
    from app.grounding.document import extract_display_pdf,build_registries,display_box
    from app.pipeline.consolidate import consolidate
    from app.pipeline.export_pdf import export_annotated_pdf
    from app.models import Note
    source=tmp_path/"original.pdf"
    with pymupdf.open() as doc:
        p=doc.new_page(width=600,height=450);p.insert_text((130,140),"parameter alpha",fontsize=18)
        p.set_cropbox(pymupdf.Rect(60,30,560,430));p.set_rotation(rotation);doc.save(source)
    before=source.read_bytes()
    extraction=extract_display_pdf(source)
    from app.pipeline.layout_v2 import enrich_geometry
    enrich_geometry(source,extraction)
    registry=build_registries(source)[0]
    element=next(e for e in registry.elements if e.kind=="word" and e.text=="alpha")
    box=display_box(registry,element.bbox)
    note=Note(page=1,block_ids=[],type="explanation",note="A parameter",quote="alpha",language="en",
              importance=5,start=0,end=1,target_bboxes=[box],target_mark="underline")
    # Normally target_bboxes always carry provenance; use an ordinary canonical block here.
    note.target_bboxes=None;note.block_ids=["target"]
    extraction.pages[0].blocks.append(Block(block_id="target",page=1,text="alpha",bbox=box))
    annotations=consolidate([note],extraction,"rotated",layout_engine="v2")
    strokes=annotations.pages[0].notes[0].sketch.strokes
    if strokes and rotation in {90,270}:
        assert max(x for x,y in strokes[0])-min(x for x,y in strokes[0])<1
    output=tmp_path/"out.pdf";export_annotated_pdf(source,annotations,output)
    with pymupdf.open(output) as doc:
        assert len(doc)==1 and "parameter" in doc[0].get_text()
        assert annotations.pages[0].notes[0].bboxes==[box]
        assert annotations.pages[0].layout_metrics["out_of_page_count"]==0
    assert source.read_bytes()==before


def test_real_pyav_18_decode_and_lock_pin():
    import av
    import tomllib
    from faster_whisper.audio import decode_audio
    assert av.__version__=="18.1.0"
    wav=io.BytesIO()
    with wave.open(wav,"wb") as audio:
        audio.setparams((1,2,16000,0,"NONE","not compressed"));audio.writeframes(b"\0\0"*1600)
    wav.seek(0)
    assert len(decode_audio(wav,sampling_rate=16000))==1600
    lock=tomllib.loads((Path(__file__).parents[1]/"uv.lock").read_text())
    assert {p["version"] for p in lock["package"] if p["name"]=="av"}=={"18.1.0"}


def test_v2_does_not_drop_high_priority_text_for_space(grounded_job):
    from app.pipeline.rich_notes import plan_annotations
    from app.models import PdfExtraction
    job,folder,_=grounded_job
    pipeline.run_pipeline(job)
    content=pipeline.read_detailed_notes(job)
    content.items[0].short_note="A faithful long explanation "*100
    content.items[0].importance=5
    content.items[1].importance=1
    extraction=PdfExtraction.model_validate_json((folder/"extraction.json").read_text())
    legacy=plan_annotations(content,extraction,"minimal")
    expanded=plan_annotations(content,extraction,"minimal",allow_overflow=True)
    assert content.items[0].id not in {n.content_id for n in legacy}
    assert content.items[0].id in {n.content_id for n in expanded}
    assert content.items[1].id not in {n.content_id for n in expanded}


def test_retry_preserves_inputs_and_rejects_running_job(grounded_job,monkeypatch):
    from app.api import jobs
    job,folder,_=grounded_job
    submitted=[]
    monkeypatch.setattr(jobs._executor,"submit",lambda *args:submitted.append(args))
    jobs._write_status(folder,"error","private backend exception")
    original=(folder/"input.pdf").read_bytes()
    with TestClient(app) as client:
        assert client.post(f"/api/jobs/{job}/retry").status_code==202
        assert client.post(f"/api/jobs/{job}/retry").status_code==409
    assert submitted and (folder/"input.pdf").read_bytes()==original
    pipeline.run_pipeline(job)
    content=pipeline.read_detailed_notes(job)
    content.complete=False
    (folder/pipeline.CONTENT_NAME).write_text(content.model_dump_json())
    jobs._write_status(folder,"done")
    with TestClient(app) as client:
        assert client.post(f"/api/jobs/{job}/retry").status_code==202
    assert submitted[-1]==(jobs._run_job,job,"notes")
