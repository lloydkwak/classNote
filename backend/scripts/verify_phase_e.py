"""Compare both production layouts on an isolated copy of an existing job; no inference."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.cache import hash_file
from app.pipeline import run as pipeline
from app.pipeline.layout_v2 import enrich_geometry,evaluate
from app.models import PdfExtraction
import pymupdf


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--exercise-overflow",action="store_true",help="Synthetic layout-capacity probe, repeats existing note text")
    args=parser.parse_args()
    source=args.job.resolve();root=args.output.resolve()
    root.mkdir(parents=True,exist_ok=False)
    original={p.relative_to(source).as_posix():hash_file(p) for p in source.rglob("*") if p.is_file()}
    data=root/"state"; folder=data/"jobs"/source.name
    shutil.copytree(source,folder)
    os.environ["DATA_DIR"]=str(data)
    report={"source_job":source.name,"new_gpu_inferences":0,"new_llm_calls":0,"pages":{}}
    started=time.perf_counter()
    extraction=PdfExtraction.model_validate_json((folder/"extraction.json").read_text())
    enrich_geometry(folder/"input.pdf",extraction)
    content=pipeline.read_detailed_notes(source.name)
    from app.pipeline import grounded_notes
    def forbid_inference(*args,**kwargs):
        raise AssertionError("Layout evaluation must not launch a VLM")
    grounded_notes.PROVIDER_FACTORY=forbid_inference
    for engine,name in (("v1","baseline"),("v2","layout-v2")):
        before=time.perf_counter()
        annotations=pipeline.density_annotations(source.name,"balanced",engine)
        artifact=pipeline.export_density(source.name,"balanced",layout=engine)
        shutil.copyfile(artifact,root/f"{name}.pdf")
        report.setdefault("cpu_seconds",{})[engine]=time.perf_counter()-before
        if engine=="v2":
            (root/"annotations.json").write_text(annotations.model_dump_json(indent=2))
            (root/"layout-debug.json").write_text(json.dumps([
                {"page":p.page,"id":n.id,"knowledge_id":n.content_id,"provenance":n.provenance.model_dump(mode="json") if n.provenance else None,
                 "placement":n.sketch.placement} for p in annotations.pages for n in p.notes],indent=2))
        bypage={p.page:p for p in extraction.pages}
        report[engine]={str(p.page):evaluate([n.sketch for n in p.notes],bypage[p.page],p.gutter,p.output_height) for p in annotations.pages}
        report.setdefault("retention",{})[engine]={"actions_rendered":sum(len(p.notes) for p in annotations.pages),
            "provenance_retained":sum(n.provenance is not None for p in annotations.pages for n in p.notes)}
        selected=sum(row.get("status")=="selected" for row in annotations.planning)
        grounded_selected=sum(row.get("status")=="selected" and row.get("grounding",{}).get("grounding_status")=="accepted"
                              for row in annotations.planning)
        grounded_rendered=sum(n.provenance is not None and n.provenance.grounding_status=="accepted"
                              for p in annotations.pages for n in p.notes)
        report["retention"][engine].update(action_to_render_retention=
            report["retention"][engine]["actions_rendered"]/selected if selected else None,
            grounded_selected=grounded_selected,grounded_rendered=grounded_rendered,
            valid_grounded_annotation_placement_rate=grounded_rendered/grounded_selected if grounded_selected else None)
        report["pages"][engine]=len(pymupdf.open(root/f"{name}.pdf"))
    images=[]
    with pymupdf.open(root/"baseline.pdf") as old,pymupdf.open(root/"layout-v2.pdf") as new:
        pages=sorted({p.page for p in annotations.pages}|({4,6} if len(new)>=6 else set()))
        for index in pages:
            pair=[]
            for label,doc in (("baseline",old),("v2",new)):
                page=doc[index]
                pix=page.get_pixmap(matrix=pymupdf.Matrix(min(1.3,1000/page.rect.width),min(1.3,1000/page.rect.width)))
                path=root/f"{label}-page-{index}.png";pix.save(path)
                pair.append((label,index,path,pix.width,pix.height))
            images.append(pair)
        with pymupdf.open() as contact:
            sheet=contact.new_page(width=2000,height=sum(max(i[4] for i in pair)+32 for pair in images));y=0
            for pair in images:
                for column,(label,index,path,width,height) in enumerate(pair):
                    sheet.insert_text((column*1000+12,y+20),f"{label} / page {index}",fontsize=14)
                    sheet.insert_image(pymupdf.Rect(column*1000,y+32,column*1000+width,y+32+height),filename=str(path))
                y+=max(i[4] for i in pair)+32
            sheet.get_pixmap().save(root/"contact-sheet.png")
    report["loss_audit"]=annotations.loss_audit
    if (folder/"evidence_graph.json").exists():
        shutil.copyfile(folder/"evidence_graph.json",root/"evidence_graph.json")
    if args.exercise_overflow and annotations.pages:
        from app.pipeline.layout import LayoutNote
        from app.pipeline.layout_v2 import layout_page_v2
        from app.pipeline.export_pdf import export_annotated_pdf
        from app.models import PageAnnotations
        original_note=annotations.pages[0].notes[0]
        page=extraction.pages[annotations.pages[0].page-1]
        layout_notes=[LayoutNote(f"capacity-{i}",original_note.note+" "+original_note.note*60,False,[]) for i in range(12)]
        layout=layout_page_v2(layout_notes,page,annotations.note_font)
        overflow_notes=[original_note.model_copy(deep=True,update={"id":n.id,"sketch":s,"note":n.text})
                        for n,s in zip(layout_notes,layout.sketches)]
        for note in overflow_notes:
            if note.provenance:
                note.provenance.action_id=note.id
        # Explicit capacity fixture: repeated text, no inference, not extra lecture knowledge.
        overflow=annotations.model_copy(deep=True)
        overflow.pages=[PageAnnotations(page=page.page,width=page.width,height=page.height,gutter=layout.gutter,
                                       output_height=layout.output_height,notes=overflow_notes,layout_metrics=layout.metrics)]
        export_annotated_pdf(folder/"input.pdf",overflow,root/"overflow.pdf")
        with pymupdf.open(root/"overflow.pdf") as doc:
            doc[page.page].get_pixmap(matrix=pymupdf.Matrix(.4,.4)).save(root/"overflow-preview.png")
        report["overflow_capacity_probe"]={"synthetic_repeated_text":True,"notes":12,"metrics":layout.metrics,
                                           "original_height":page.height,"output_height":layout.output_height}
    report["original_files_unchanged"]=all(hash_file(source/p)==h for p,h in original.items())
    report["pdf_export_validity"]={}
    with pymupdf.open(folder/"input.pdf") as original_pdf:
        for name in ("baseline","layout-v2"):
            with pymupdf.open(root/f"{name}.pdf") as exported:
                report["pdf_export_validity"][name]={"opens":True,"page_count":len(exported),
                    "original_text_retained":all(set(original_pdf[i].get_text().split())<=set(exported[i+1].get_text().split()) for i in range(len(original_pdf)))}
    assert report["original_files_unchanged"]
    assert report["pages"]["v1"]==report["pages"]["v2"]
    assert report["retention"]["v1"]==report["retention"]["v2"]
    report.update(status="completed_requires_visual_review",seconds=time.perf_counter()-started)
    (root/"metrics.json").write_text(json.dumps({k:report[k] for k in ("v1","v2","retention","loss_audit")},indent=2))
    (root/"report.json").write_text(json.dumps(report,indent=2))
    print(json.dumps({"status":report["status"],"report":str(root/"report.json")}))


if __name__=="__main__":main()
