"""Synthetic production E2E: fixture STT/alignment/semantic extraction, real isolated 8B grounding."""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import wave
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from app.cache import hash_file
from app.models import Alignment, AlignedWindow
from app.pipeline import run as pipeline
from app.pipeline import grounded_notes
from app.pipeline.rich_notes import DetailedNotes
from ground_page import synthetic_fixture
from benchmark_vlm import DeviceSampler


class FixtureContentProvider:
    name="synthetic-semantic-fixture"
    model="phase-d-fixture-v1"
    def __init__(self,items):
        types={"word":"definition","formula":"derivation","figure":"slide_reference"}
        short={"word":"Gradient gives the ascent direction", "symbol":"Alpha controls update size",
               "formula":"The parameter update rule", "operator":"Gradient operator",
               "figure":"The Loss curve", "absent":"Beta is absent here", "repeated":"Theta is updated"}
        self.rows=[{"type":types.get(i.id,"explanation"),"title":i.id,"content":i.content,
                    "source_quote":i.source_quote,"short_note":short[i.id],"block_ids":i.block_ids,
                    "importance":5,"language":"en"} for i in items]
        self.rows.append({**self.rows[0],"source_quote":"This was never uttered","title":"invalid quote"})
    def complete_json(self,prompt):
        return json.dumps({"items":self.rows},ensure_ascii=False)


class FixtureVLM:
    """CPU integration mode only, clearly marked. Default mode uses the isolated real model."""
    def __init__(self,root):
        pass
    def select_targets(self,request,**kwargs):
        from app.grounding.schema import TargetSelections,TargetProposal
        from app.grounding.selection import validate_selection
        item=request.items[0]
        e=next((e for e in request.registry.elements if e.id in request.viewport.visible_element_ids
                and e.kind==item.target_kind and e.text and e.text in item.source_quote),None)
        if item.target_kind=="figure":
            e=next(e for e in request.registry.elements if e.kind=="figure" and e.id in item.block_ids)
        proposal=TargetProposal(knowledge_id=item.id,page=item.page,status="selected" if e else "unmatched",
                                target_element_id=e.id if e else None,target_text=e.text if e else "",confidence=0.9)
        reply=TargetSelections(selections=[proposal])
        return {"selection":reply.model_dump(mode="json"),"grounding":validate_selection(request,reply),
                "metrics":{"mock":True},"cache_hit":False,"new_inference":False,"oom_count":0}
    def close(self):
        pass


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,default=Path("data/phase-d-runs")/datetime.now().strftime("%Y%m%dT%H%M%S%f"))
    parser.add_argument("--mock-vlm",action="store_true")
    args=parser.parse_args()
    root=args.output.resolve()
    if root.exists() and any(root.iterdir()):
        parser.error("Use a fresh output directory")
    root.mkdir(parents=True,exist_ok=True)
    os.environ.update(DATA_DIR=str(root/"state"),STT_PROVIDER="mock",LLM_PROVIDER="mock",
                      EMBEDDING_PROVIDER="mock",DEMO_MODE="0",FFMPEG_PATH="/usr/bin/ffmpeg")
    inputs=root/"fixture";inputs.mkdir()
    pdf,registry,items,segments,_,expected=synthetic_fixture(inputs)
    audio=inputs/"recording.wav"
    with wave.open(str(audio),"wb") as wav:
        wav.setparams((1,2,16000,0,"NONE","not compressed"))
        wav.writeframes(b"\0\0"*16000*int(max(s.end for s in segments)+1))
    job=pipeline.create_job(pdf,audio,job_id="phase-d-synthetic",visual_grounding=True)
    folder=pipeline.job_dir(job)
    options=json.loads((folder/"job.json").read_text())
    options["synthetic_fixtures"]={"stt":True,"alignment":True,"semantic_provider":True,"vlm":args.mock_vlm}
    (folder/"job.json").write_text(json.dumps(options))
    def aligned(windows,extraction):
        return Alignment(pages=[1],windows=[AlignedWindow(**w.model_dump(),page=1,raw_page=1,
                                                          confidence=1,similarities=[1]) for w in windows])
    sampler=DeviceSampler()
    started=time.perf_counter()
    report={"synthetic":True,"human_verified":False,"real_vlm":not args.mock_vlm,"job_id":job,"job_folder":str(folder)}
    if not args.mock_vlm:
        sampler.start()
    try:
        provider=FixtureContentProvider(items)
        with patch("app.pipeline.run.transcribe",side_effect=lambda *a,**k:segments), \
             patch("app.pipeline.run.align_windows",side_effect=aligned), \
             patch("app.pipeline.rich_notes.get_provider",return_value=provider), \
             patch.object(grounded_notes,"PROVIDER_FACTORY",FixtureVLM if args.mock_vlm else grounded_notes.PROVIDER_FACTORY):
            pipeline.run_pipeline(job)
        manifest=json.loads((folder/grounded_notes.GROUNDING_NAME).read_text())
        source_snapshot={name:hash_file(folder/name) for name in (pipeline.CONTENT_NAME,pipeline.SEGMENTS_NAME,
                                                                  pipeline.ALIGNMENT_NAME,grounded_notes.GROUNDING_NAME)}
        # Density and style must never start a worker or rerun semantic extraction.
        with patch.object(grounded_notes,"PROVIDER_FACTORY",side_effect=AssertionError("density invoked VLM")):
            for density in ("minimal","balanced","detailed"):
                annotations=pipeline.density_annotations(job,density)
                pipeline.export_density(job,density)
                report.setdefault("density_rendered",{})[density]=sum(len(p.notes) for p in annotations.pages)
        assert all(hash_file(folder/n)==digest for n,digest in source_snapshot.items())
        with patch.object(grounded_notes,"PROVIDER_FACTORY",side_effect=AssertionError("resume invoked VLM")), \
             patch("app.pipeline.rich_notes.get_provider",return_value=provider):
            pipeline.run_pipeline(job,from_step="notes")
        assert hash_file(folder/grounded_notes.GROUNDING_NAME)==source_snapshot[grounded_notes.GROUNDING_NAME]
        report["resume_reused_grounding_without_worker"]=True
        graph=json.loads((folder/"evidence_graph.json").read_text())
        report.update(status="completed_requires_visual_review",metrics=graph["metrics"],
                      grounding_fingerprint=manifest["fingerprint"],pdf_sha256=hash_file(folder/"annotated.pdf"))
        responses=[r["response"] for r in manifest["records"].values() if "response" in r]
        report["gpu_requests"]={"new_inferences":sum(r.get("new_inference",False) for r in responses),
                                 "cache_hits":sum(r.get("cache_hit",False) for r in responses),
                                 "metrics":[r.get("metrics",{}) for r in responses]}
        report["backend_python"]=sys.executable
        report["worker_environments"]=[r.get("worker_environment") for r in responses]
        assert len(pipeline.read_detailed_notes(job).items)==len(items)
        assert any(v.reason=="source_quote_not_found" for v in pipeline.read_detailed_notes(job).validation)
        for name in ("annotated.pdf","grounding_results.json","evidence_graph.json","annotation_actions.json"):
            shutil.copyfile(folder/name,root/name)
        command=["pdftoppm","-png","-scale-to","1800","-singlefile",str(root/"annotated.pdf"),str(root/"preview")]
        subprocess.run(command,check=True,capture_output=True)
        report["graph_example"]=next((n for n in graph["nodes"] if n["kind"]=="annotation_action"
                                      and n["data"]["grounding_status"]=="accepted"),None)
        assert report["graph_example"] is not None, "No grounded annotation reached the PDF"
        import pymupdf
        with pymupdf.open(root/"annotated.pdf") as document:
            bundle=json.loads(document.embfile_get("classnote-evidence.json"))
            assert len(bundle["annotations"])==7
            assert any(n["provenance"]["target_element_id"] for n in bundle["annotations"])
        (root/"metrics.json").write_text(json.dumps(report["metrics"],ensure_ascii=False,indent=2))
    except Exception as error:
        report.update(status="failed",error_type=type(error).__name__)
        raise
    finally:
        if sampler.thread.is_alive():
            report["gpu_sampling"]=sampler.stop()
        report["seconds"]=time.perf_counter()-started
        (root/"report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps({"status":report["status"],"report":str(root/"report.json")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
