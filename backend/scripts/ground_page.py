"""Phase C: real SSD Qwen3-VL ID selection, original/SoM comparison and review artifacts."""

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pymupdf

from app.cache import hash_file
from app.grounding.document import build_registries, box_in_pixels, render_page, registry_fingerprint
from app.grounding.schema import GroundingItem, TargetRequest
from app.models import Mention, Segment
from app.vlm.provider import GPUWorkerProvider, VLMFailure
from app.vlm.schema import REPO_ROOT, VLMConfig
from benchmark_vlm import DeviceSampler, command


def synthetic_fixture(output):
    path = output / "synthetic.pdf"
    with pymupdf.open() as doc:
        page = doc.new_page(width=960, height=540)
        page.insert_font(fontname="course", fontfile=str(REPO_ROOT / "backend/assets/fonts/Pretendard-Medium.otf"))
        math_font = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
        if not math_font.exists():
            raise RuntimeError("Synthetic Greek math fixture requires DejaVuSans.ttf")
        page.insert_font(fontname="math", fontfile=str(math_font))
        page.insert_text((55,65), "Gradient Descent / 경사하강법",fontname="course",fontsize=27)
        page.insert_text((55,135), "gradient gives the ascent direction",fontname="course",fontsize=18)
        page.insert_text((55,210), "θ = θ - α ∇L",fontname="math",fontsize=26)
        page.insert_text((55,275), "학습률 α controls the update size",fontname="course",fontsize=18)
        page.draw_line((570,440),(895,440))
        page.draw_line((570,440),(570,140))
        page.draw_bezier((600,185),(655,435),(770,435),(870,220))
        page.insert_text((675,480), "Loss curve",fontname="course",fontsize=20)
        doc.save(path)
    registry = build_registries(path)[0]
    formula = next(e for e in registry.elements if e.kind == "formula")
    alpha = next(e for e in registry.elements if e.text == "α" and formula.id in e.parent_ids)
    operator = next(e for e in registry.elements if e.text == "∇" and e.kind == "symbol")
    word = next(e for e in registry.elements if e.text == "gradient")
    figure = next(e for e in registry.elements if e.kind == "figure")
    block_id = lambda e: next(i for i in e.parent_ids if any(p.id == i and p.kind == "block" for p in registry.elements))
    specs = [
        ("word", "gradient gives the ascent direction", "word", [block_id(word)], word.id),
        ("symbol", "α controls the update size", "symbol", [block_id(alpha)], alpha.id),
        ("formula", "θ = θ - α ∇L is the update rule", "formula", [block_id(formula)], formula.id),
        ("operator", "∇ denotes the gradient operator", "symbol", [block_id(operator)], operator.id),
        ("figure", "이 그래프는 Loss curve를 보여줍니다", "figure", [figure.id], figure.id),
        ("absent", "β is not shown on this slide", "symbol", [], None),
        ("repeated", "θ is the parameter being updated", "symbol", [block_id(formula)], None),
    ]
    segments, items, expected = [], [], {}
    for index, (name, quote, kind, blocks, target) in enumerate(specs):
        start = index * 5.0
        segments.append(Segment(start=start, end=start+4, text=quote))
        items.append(GroundingItem(id=name,page=1,content=quote,source_quote=quote,start=start,end=start+4,
                                   segment_ids=[index],block_ids=blocks,target_kind=kind))
        expected[name] = target
    return path, registry, items, segments, [], expected


def preview(original_path, request, result, output):
    """Diagnostic overlay only: no writes to the PDF or production annotation artifacts."""
    from PIL import Image, ImageDraw, ImageFont

    with Image.open(original_path) as original:
        image = original.convert("RGB")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18)
    elements = {e.id:e for e in request.registry.elements}
    for row in result["grounding"]["results"]:
        accepted = row["status"] == "accepted"
        proposed = row["proposals"][0]["target_element_id"] if row["proposals"] else None
        element = elements.get(row["target_element_id"] if accepted else proposed)
        if element is not None and element.id in request.viewport.visible_element_ids:
            box = box_in_pixels(element.bbox,request.viewport)
            draw.rectangle(box,outline="green" if accepted else "red",width=4)
            draw.text((max(0,box[0]),max(0,box[1]-23)),element.id,fill="green" if accepted else "red",font=font)
    canvas = Image.new("RGB",(image.width,image.height+55),"white")
    canvas.paste(image,(0,0))
    draw = ImageDraw.Draw(canvas)
    label = "; ".join(f"{r['knowledge_id']}: {r['status']} ({r['reason']})" for r in result["grounding"]["results"])
    draw.text((10,image.height+10),label,fill="black",font=font)
    canvas.save(output)


def contact_sheet(paths, output):
    from PIL import Image, ImageDraw

    tiles=[]
    for label,path in paths:
        with Image.open(path) as source:
            tile=source.convert("RGB")
        tile.thumbnail((720,450))
        canvas=Image.new("RGB",(740,485),"white")
        canvas.paste(tile,(10,25))
        ImageDraw.Draw(canvas).text((10,7),label,fill="black")
        tiles.append(canvas)
    sheet=Image.new("RGB",(1480,485*((len(tiles)+1)//2)),"#dddddd")
    for index,tile in enumerate(tiles):
        sheet.paste(tile,((index%2)*740,(index//2)*485))
    sheet.save(output)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf",type=Path)
    parser.add_argument("--evidence",type=Path,help="JSON: items, segments, optional mentions/alignment_hash")
    parser.add_argument("--page",type=int,default=1)
    parser.add_argument("--output",type=Path,default=REPO_ROOT/"data/grounding-runs"/datetime.now().strftime("%Y%m%dT%H%M%S%f"))
    parser.add_argument("--model-cache",type=Path,default=REPO_ROOT/"models/huggingface")
    parser.add_argument("--mode",choices=["both","som","original"],default="both")
    parser.add_argument("--max-pixels",type=int,default=524288)
    parser.add_argument("--max-input-tokens",type=int,default=4096)
    parser.add_argument("--max-new-tokens",type=int,default=256)
    parser.add_argument("--crop",nargs=4,type=float,default=[0,0,1,1])
    parser.add_argument("--ocr",action="store_true")
    parser.add_argument("--cache",action="store_true",help="Reuse inference cache; default actual uncached A/B")
    args=parser.parse_args()
    if bool(args.pdf) != bool(args.evidence):
        parser.error("--pdf and --evidence must be supplied together; omit both for synthetic fixture")
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("Output is not empty; choose a new run directory")
    args.output.mkdir(parents=True,exist_ok=True)
    report={"status":"started","synthetic":args.pdf is None,"human_verified":False,
            "timestamp_utc":datetime.now(timezone.utc).isoformat(),"runs":[],
            "git":command("git","rev-parse","HEAD"),"git_status":command("git","status","--short")}
    sampler=DeviceSampler()
    exit_code=0
    try:
        if args.pdf is None:
            pdf,registry,items,segments,mentions,expected=synthetic_fixture(args.output)
            alignment_hash="synthetic-alignment-v1"
            mentions_registry_hash=""
        else:
            pdf=args.pdf
            registries=build_registries(pdf,ocr=args.ocr)
            registry=next(r for r in registries if r.page == args.page)
            evidence=json.loads(args.evidence.read_text())
            items=[]
            for row in evidence["items"]:
                if "short_note" in row:
                    from app.pipeline.rich_notes import ContentItem
                    item=GroundingItem.from_content_item(ContentItem.model_validate(row))
                else:
                    item=GroundingItem.model_validate(row)
                if item.page == args.page:
                    items.append(item)
            segments=[Segment.model_validate(s) for s in evidence["segments"]]
            mentions=[Mention.model_validate(m) for m in evidence.get("mentions",[])]
            expected={}
            alignment_hash=evidence.get("alignment_hash","")
            mentions_registry_hash=evidence.get("mentions_registry_hash","")
        original,marked,viewport=render_page(pdf,registry,args.output/"page",crop=tuple(args.crop))
        (args.output/"registry.json").write_text(registry.model_dump_json(indent=2))
        report.update(pdf_sha256=hash_file(pdf),registry_hash=registry_fingerprint(registry),
                      viewport=viewport.model_dump(mode="json"),diagnostics=registry.diagnostics,
                      expected_ids=expected)
        config=VLMConfig(model_cache_dir=args.model_cache.resolve(), max_pixels=args.max_pixels,
                         max_input_tokens=args.max_input_tokens,max_new_tokens=args.max_new_tokens)
        report["config"]=config.model_dump(mode="json")
        report["code_sha256"]={str(p.relative_to(REPO_ROOT)):hash_file(p) for p in
                                [Path(__file__),*sorted((REPO_ROOT/"backend/app/grounding").glob("*.py")),
                                 *sorted((REPO_ROOT/"backend/app/vlm").glob("*.py"))]}
        sampler.start()
        tiles=[("original",original),("SoM",marked)]
        with GPUWorkerProvider(config) as provider:
            for item in items:
                for mode in (["original","som"] if args.mode == "both" else [args.mode]):
                    request=TargetRequest(image_path=marked if mode == "som" else original,
                                          registry=registry,viewport=viewport,items=[item],segments=segments,
                                          mentions=mentions,mentions_registry_hash=mentions_registry_hash,
                                          image_mode=mode,alignment_hash=alignment_hash)
                    started=time.perf_counter()
                    try:
                        result=provider.select_targets(request,use_cache=args.cache)
                        row=result["grounding"]["results"][0]
                        result.update(knowledge_id=item.id,image_mode=mode,wall_seconds=time.perf_counter()-started)
                        if item.id in expected:
                            result["synthetic_expected_id"]=expected[item.id]
                            result["synthetic_effective_id_match"]=row["target_element_id"] == expected[item.id]
                            proposals = [p for p in result["selection"]["selections"] if p["knowledge_id"] == item.id]
                            result["synthetic_raw_id_match"] = len(proposals) == 1 and (
                                proposals[0]["target_element_id"] == expected[item.id] if expected[item.id] is not None
                                else proposals[0]["target_element_id"] is None and proposals[0]["status"] in {"ambiguous","unmatched"})
                        image_path=args.output/f"{item.id}-{mode}.png"
                        preview(original,request,result,image_path)
                        tiles.append((f"{item.id} / {mode}",image_path))
                    except VLMFailure as error:
                        result={"knowledge_id":item.id,"image_mode":mode,"error":str(error),
                                "failure_details":error.details,"wall_seconds":time.perf_counter()-started}
                        exit_code=1
                    report["runs"].append(result)
                    (args.output/"report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2))
        contact_sheet(tiles,args.output/"contact-sheet.png")
        report["status"]="completed_requires_visual_review" if not exit_code else "partial_failure"
        report["summary"]={mode:{"requests":len(rows:=[r for r in report["runs"] if r["image_mode"] == mode]),
                                 "schema_valid":sum("grounding" in r for r in rows),
                                 "accepted":sum(r.get("grounding",{}).get("results",[{}])[0].get("status") == "accepted" for r in rows),
                                 "synthetic_raw_matches":sum(r.get("synthetic_raw_id_match",False) for r in rows) if expected else None,
                                 "synthetic_effective_matches":sum(r.get("synthetic_effective_id_match",False) for r in rows) if expected else None}
                           for mode in ("original","som")}
    except Exception as error:
        report.update(status="failed",error_type=type(error).__name__)
        # Local inputs/diagnostics only; do not serialize remote auth errors.
        if isinstance(error,(ValueError,StopIteration)):
            report["reason"]=str(error)[:400]
        exit_code=1
    finally:
        if sampler.thread.is_alive():
            report["gpu_sampling"]=sampler.stop()
        (args.output/"report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps({"status":report["status"],"report":str(args.output/"report.json")}))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
