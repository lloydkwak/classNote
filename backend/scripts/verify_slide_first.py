"""Real saved evidence revalidation → isolated Qwen → slide-first PDF + overview.

The original job is read-only. Saved rejected candidates are replayed through the ordinary
Rich Notes validators; no new STT, alignment, or semantic generation is claimed.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time
from collections import Counter

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.cache import hash_file
from app.models import Alignment,PdfExtraction,Segment
from app.pipeline import run as pipeline,grounded_notes
from app.pipeline.rich_notes import DetailedNotes,ExtractedItem,extract_content
from app.llm.mock import INPUT_MARKER
from benchmark_vlm import DeviceSampler
import pymupdf


class SavedCandidateReplay:
    name="saved-candidate-revalidation"
    model="no-semantic-generation-v1"
    def __init__(self,content):
        self.rows={}
        byid={i.id:i for i in content.items}
        for span in content.spans:
            rows=[]
            for record in content.validation:
                if span["span_id"] not in record.item_id:continue
                row=record.candidate or byid.get(record.item_id)
                if row:rows.append({key:getattr(row,key) for key in ExtractedItem.model_fields})
            self.rows[span["page"]]=rows
    def complete_json(self,prompt):
        payload=json.loads(prompt.split(INPUT_MARKER)[-1])
        first=payload["blocks"][0]["block_id"]
        page=int(re.match(r"p(\d+)_",first)[1])
        if page not in self.rows:raise ValueError("saved_span_unavailable")
        return json.dumps({"items":self.rows[page]},ensure_ascii=False)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--gpu",action="store_true")
    parser.add_argument("--resume",action="store_true",help="Re-render this run from its validated checkpoints; originals stay read-only")
    args=parser.parse_args()
    root=args.output.resolve();root.mkdir(parents=True,exist_ok=args.resume)
    previous=json.loads((root/"report.json").read_text()) if args.resume and (root/"report.json").exists() else {}
    source=args.job.resolve();folder=root/"state/jobs"/source.name
    snapshot={p.relative_to(source).as_posix():hash_file(p) for p in source.rglob("*") if p.is_file()}
    if not folder.exists():shutil.copytree(source,folder)
    elif not args.resume or hash_file(folder/"input.pdf")!=hash_file(source/"input.pdf"):
        raise ValueError("Resume requires the same source PDF")
    os.environ["DATA_DIR"]=str(root/"state")
    os.environ["FFMPEG_PATH"]="/usr/bin/ffmpeg"
    os.environ["DEMO_MODE"]="0"
    if (source/"annotated.pdf").exists():shutil.copyfile(source/"annotated.pdf",root/"before.pdf")
    original=DetailedNotes.model_validate_json((folder/"detailed_notes.json").read_text())
    extraction=PdfExtraction.model_validate_json((folder/"extraction.json").read_text())
    alignment=Alignment.model_validate_json((folder/"alignment.json").read_text())
    segments=[Segment.model_validate(s) for s in json.loads((folder/"segments.json").read_text())]
    content=extract_content(alignment,extraction,segments,provider=SavedCandidateReplay(original))
    assert {i.id for i in original.items}<={i.id for i in content.items},"Existing validated knowledge lost"
    by_id={i.id:i for i in content.items}
    retained=all((i.content,i.short_note,i.source_quote)==(by_id[i.id].content,by_id[i.id].short_note,by_id[i.id].source_quote) for i in original.items)
    assert retained,"Existing validated explanation text changed"
    for field,name in (("alignment_hash","alignment.json"),("extraction_hash","extraction.json"),
                       ("transcript_hash","segments.json"),("pdf_hash","input.pdf")):
        setattr(content,field,hash_file(folder/name))
    content.provenance={"visual_grounding":True,"source":"saved-candidate-revalidation","new_semantic_calls":0}
    (folder/"detailed_notes.json").write_text(content.model_dump_json(indent=2))
    report={"source_job":source.name,"synthetic":False,"new_stt_calls":0,"new_alignment_calls":0,"new_semantic_calls":0,
            "existing_knowledge_text_preserved":retained,"old_validated_count":len(original.items),"new_validated_count":len(content.items),"real_vlm":args.gpu or previous.get("real_vlm",False),
            "gpu_requested_this_run":args.gpu,"previous_gpu_sampling":previous.get("gpu_sampling") or previous.get("previous_gpu_sampling"),
            "first_run_seconds":previous.get("first_run_seconds",previous.get("seconds"))}
    sampler=DeviceSampler();started=time.perf_counter()
    try:
        if args.gpu:
            sampler.start()
            grounded_notes.prepare_grounding(folder,content,alignment,segments)
        annotations=pipeline.density_annotations(source.name,"balanced","v2")
        artifact=pipeline.export_density(source.name,"balanced",layout="v2")
        shutil.copyfile(artifact,root/"after.pdf")
        (root/"annotations.json").write_text(annotations.model_dump_json(indent=2))
        for name in ("evidence_graph.json","annotation_actions.json","grounding_results.json"):
            if (folder/name).exists():shutil.copyfile(folder/name,root/name)
        planned=Counter();rendered=Counter();inline=margin_only=direct=0
        for p in annotations.pages:
            for n in p.notes:
                inside=0<=n.sketch.x and n.sketch.x+n.sketch.width<=p.width and n.sketch.y+n.sketch.height<=p.height
                inline+=inside
                margin_only+=not inside and not n.sketch.strokes and not n.sketch.arrow
                direct+=bool(n.provenance and n.provenance.grounding_status=="accepted" and (n.sketch.strokes or n.sketch.arrow))
                if n.sketch.strokes:rendered[n.sketch.mark]+=1
                if n.sketch.arrow:rendered["connector_arrow"]+=1
        if (folder/"annotation_actions.json").exists():
            planned.update(a["annotation_type"] for a in json.loads((folder/"annotation_actions.json").read_text()) if a["selected"])
        count=sum(len(p.notes) for p in annotations.pages)
        report["action_metrics"]={"selected_annotations":count,"original_slide_text_count":inline,
            "margin_only_annotation_count":margin_only,"directly_anchored_count":direct,
            "directly_anchored_percentage":100*direct/count if count else None,"margin_fallback_rate":(count-inline)/count if count else None,"planned_by_type":dict(planned),
            "rendered_by_type":dict(rendered),"target_accuracy_human_gold":None,
            "page_layout_metrics":{str(p.page):p.layout_metrics for p in annotations.pages}}
        with pymupdf.open(root/"after.pdf") as doc,pymupdf.open(folder/"input.pdf") as original_pdf:
            mapping=json.loads(doc.embfile_get("classnote-page-map.json"))
            assert len(doc)==len(original_pdf)+1
            retained=all(set(original_pdf[i].get_text().split())<=set(doc[i+1].get_text().split()) for i in range(len(original_pdf)))
            assert retained
            for source_page,name in ((None,"overview"),(3,"slide3"),(4,"slide4"),(5,"slide5"),(6,"slide6")):
                if source_page is not None and source_page>len(original_pdf):continue
                p=doc[0 if source_page is None else source_page]
                p.get_pixmap(matrix=pymupdf.Matrix(min(1.6,1400/p.rect.width),min(1.6,1400/p.rect.width))).save(root/f"{name}-preview.png")
            source_audio=source/pipeline.PAGE_AUDIO_NAME
            audio_same=None;audio_checks=[]
            if source_audio.exists():
                native_audio=json.loads(source_audio.read_text())
                audio_same=native_audio==json.loads((folder/pipeline.PAGE_AUDIO_NAME).read_text())
                assert audio_same
                for entry in native_audio["pages"]:
                    assert 1<=entry["page"]<=len(original_pdf)
                    mapped=next(m for m in mapping["pages"] if m["source_page"]==entry["page"])
                    for interval in entry["audio_ranges"]:
                        assert mapped["export_page_index"]==entry["page"]
                        audio_checks.append({"source_page":entry["page"],"export_page_index":mapped["export_page_index"],
                                             "start":interval["start"],"end":interval["end"],"confidence":interval["confidence"]})
            # Inspect exported glyph/word bounds too: nominal layout boxes alone missed
            # a real collision with a math fragment filtered by semantic extraction.
            actual_collisions=[];actual_bounds_failures=[]
            for source_index,original_page in enumerate(original_pdf):
                output_page=doc[source_index+1]
                layout=next((p for p in annotations.pages if p.page==source_index+1),None)
                gutter=layout.gutter if layout else 0
                originals=[]
                for word in original_page.get_text("words"):
                    area=pymupdf.Rect(word[:4])*original_page.rotation_matrix
                    area+=pymupdf.Rect(gutter,0,gutter,0)
                    originals.append((area,word[4]))
                for word in output_page.get_text("words"):
                    box=pymupdf.Rect(word[:4])
                    is_original=any(word[4]==text and max(abs(a-b) for a,b in zip(box,area))<.03 for area,text in originals)
                    if is_original:continue
                    for area,_ in originals:
                        if (box&area).get_area()>.05:
                            actual_collisions.append({"source_page":source_index+1,"note_box":list(box),"source_box":list(area)})
                    if not output_page.rect.contains(box):actual_bounds_failures.append({"source_page":source_index+1,"box":list(box)})
            report["action_metrics"]["rendered_note_text_occlusion_count"]=len(actual_collisions)
            report["action_metrics"]["rendered_note_text_out_of_bounds_count"]=len(actual_bounds_failures)
            report["rendered_text_collisions"]=actual_collisions
            report["rendered_text_out_of_bounds"]=actual_bounds_failures
            report["mapping"]={"source_pages":len(original_pdf),"export_pages":len(doc),"overview_export_index":0,
                               "original_order_and_text_retained":retained,"page_map":mapping["pages"],
                               "audio_source_page_numbers_unchanged":audio_same,"audio_seek_mapping_checks":audio_checks}
        # Controlled layout comparison: identical validated knowledge, selected actions,
        # canonical targets and font; only the layout/render engine differs.
        from app.pipeline.consolidate import consolidate
        from app.pipeline.export_pdf import export_annotated_pdf
        from app.pipeline.layout_v2 import enrich_geometry,evaluate
        baseline_notes,_,_=grounded_notes.plan_grounded(folder,content,extraction,"balanced",allow_overflow=True)
        previous_font=os.environ.get("NOTE_FONT")
        os.environ["NOTE_FONT"]=annotations.note_font
        try:baseline=consolidate(baseline_notes,extraction,source.name,max_notes_per_page=len(baseline_notes),layout_engine="v1")
        finally:
            if previous_font is None:os.environ.pop("NOTE_FONT",None)
            else:os.environ["NOTE_FONT"]=previous_font
        baseline.overview=annotations.overview;baseline.page_mapping=annotations.page_mapping
        export_annotated_pdf(folder/"input.pdf",baseline,root/"same-evidence-baseline.pdf")
        assert {n.content_id for p in baseline.pages for n in p.notes}=={n.content_id for p in annotations.pages for n in p.notes}
        enrich_geometry(folder/"input.pdf",extraction)
        report["same_evidence_baseline"]={"font":baseline.note_font,"knowledge_ids_match":True,
            "original_slide_text_count":sum(0<=n.sketch.x and n.sketch.x+n.sketch.width<=p.width and n.sketch.y+n.sketch.height<=p.height for p in baseline.pages for n in p.notes),
            "margin_text_count":sum(not (0<=n.sketch.x and n.sketch.x+n.sketch.width<=p.width and n.sketch.y+n.sketch.height<=p.height) for p in baseline.pages for n in p.notes),
            "page_layout_metrics":{str(p.page):evaluate([n.sketch for n in p.notes],extraction.pages[p.page-1],p.gutter,p.output_height) for p in baseline.pages}}
        report["grounding_telemetry"]=json.loads((folder/"grounding_results.json").read_text()).get("telemetry") if (folder/"grounding_results.json").exists() else None
        report["new_vlm_inferences_this_run"]=report["grounding_telemetry"].get("new_inferences",0) if args.gpu and report["grounding_telemetry"] else 0
        report["loss_audit"]=annotations.loss_audit
        report["overview"]=annotations.overview
        report["original_files_unchanged"]=all(hash_file(source/p)==value for p,value in snapshot.items())
        assert report["original_files_unchanged"]
        report["status"]="completed_requires_visual_review"
    finally:
        if sampler.thread.is_alive():report["gpu_sampling"]=sampler.stop()
        report["seconds"]=time.perf_counter()-started
        (root/"report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2))
    (root/"action-metrics.json").write_text(json.dumps(report["action_metrics"],indent=2))
    (root/"page-mapping-tests.json").write_text(json.dumps(report["mapping"],indent=2))
    print(json.dumps({"status":report["status"],"report":str(root/"report.json")}))


if __name__=="__main__":main()
