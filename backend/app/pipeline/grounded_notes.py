"""Production integration: content → bounded IPC → validated IDs → existing note/layout path."""

import inspect
import re
import json
import time
import uuid
from collections import Counter
from pathlib import Path

import pymupdf

from app.cache import hash_file
from app.evidence import AnnotationAction, EvidenceGraph, EvidenceNode, EvidenceEdge, fingerprint, stable_id
from app.grounding.document import build_registries, render_page, registry_fingerprint
from app.grounding.ipc import IsolatedVLMProvider, WorkerFailure
from app.grounding.schema import ElementRegistry, GroundingItem, TargetRequest, TargetSelections
from app.grounding.selection import (SELECTION_VERSION, SELECTION_SYSTEM, VALIDATION_VERSION,
                                     selection_prompt, speech_error, validate_selection, literal_contains,formula_supported,spoken_math_contains)
from app.models import AnnotationProvenance, Mention, Segment
from app.pipeline.rich_notes import plan_annotations
from app.vlm.schema import MODEL_ID, MODEL_REVISION

GROUNDING_NAME="grounding_results.json"
ACTION_POLICY="grounded-actions-v4-safe-figure-detail-link"


def choose_action(item,element):
    """Use existing semantic extraction, never a new summarization/coordinate call."""
    if element.kind=="figure": return "none","figure_block_without_verified_subtarget"
    if element.kind=="symbol": return "circle","native_symbol_role"
    if item.type=="definition": return ("highlight" if item.importance>=4 else "underline"),"definition_term"
    if item.type=="worked_example": return "check","spoken_worked_example_result"
    if item.type in {"warning","exception","exam_hint","emphasis"}: return "highlight","source_emphasis_or_caution"
    if item.type=="derivation": return "bracket","sequential_derivation_step"
    if item.type=="reasoning" and element.kind=="formula": return "arrow","reasoning_formula_relation"
    if element.kind=="formula": return "box","formula_meaning_explanation"
    return "underline","source_explains_native_text"
PROVIDER_FACTORY=IsolatedVLMProvider


def write_json(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding="utf-8")
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def inference_revision():
    root=Path(__file__).resolve().parents[1]
    return fingerprint({"model":MODEL_ID,"revision":MODEL_REVISION,"prompt":SELECTION_VERSION,
                        "system":SELECTION_SYSTEM,"builder":inspect.getsource(selection_prompt),"candidate_support":inspect.getsource(formula_supported),
                        "math_aliases":inspect.getsource(spoken_math_contains),
                        "target_policy":inspect.getsource(target_kind),"focus_policy":inspect.getsource(local_request),
                        "runtime":hash_file(root/"vlm/runtime.py"),
                        "schema":hash_file(root/"grounding/schema.py"),
                        "profile":"nf4-sdpa-524288-4096-512"})


def sources(folder,content):
    source={name:hash_file(folder/name) for name in
            ("input.pdf","segments.json","alignment.json","extraction.json")}
    source["knowledge"]=fingerprint([i.model_dump(mode="json") for i in content.items])
    source["registry_builder"]=fingerprint([hash_file(Path(__file__).resolve().parents[1]/"grounding/document.py"),
                                            hash_file(Path(__file__).with_name("pdf_extract.py"))])
    source["inference"]=inference_revision()
    source["validation"]=fingerprint([VALIDATION_VERSION,inspect.getsource(validate_selection),inspect.getsource(speech_error),
                                          inspect.getsource(formula_supported),inspect.getsource(spoken_math_contains)])
    source["mentions"]=hash_file(folder/"mentions.json") if (folder/"mentions.json").exists() else "none"
    return source


def local_request(item,registry,viewport,image,segments,alignment,mentions=(),mentions_hash=""):
    ids=item.segment_ids
    if not ids or any(i < 0 or i >= len(segments) for i in ids):
        raise ValueError("invalid_segment_id")
    if ids != list(range(min(ids),max(ids)+1)):
        raise ValueError("noncontiguous_segment_ids")
    # Keep absolute source IDs on ContentItem/graph; remap only the compact worker input.
    compact=[segments[i] for i in ids]
    grounding=GroundingItem.from_content_item(item,target_kind=target_kind(item,registry))
    if item.type=="derivation" and grounding.target_kind=="formula":
        # A multi-step proof is not one ambiguous target. Focus on its verbatim final
        # statement, without inventing a new claim or changing the original evidence.
        conclusion=re.split(r"(?<=[.!?])\s+(?=[A-Z가-힣])",item.source_quote.strip())[-1]
        focus=grounding.model_copy(update={"selection_quote":conclusion})
        eligible=[e for e in registry.elements if e.kind=="formula" and set(e.parent_ids)&set(item.block_ids)]
        if conclusion!=item.source_quote and any(formula_supported(e,registry.elements,focus) for e in eligible):
            grounding.selection_quote=conclusion
    grounding.segment_ids=list(range(len(ids)))
    windows=[w.model_dump(mode="json") for w in alignment.windows
             if w.start < item.end and w.end > item.start]
    if not any(w["page"] == item.page for w in windows):
        raise ValueError("alignment_does_not_support_page")
    # Inference cache changes only for relevant windows/evidence, not another page's alignment.
    alignment_ref=fingerprint(windows)
    return TargetRequest(image_path=image,registry=registry,viewport=viewport,items=[grounding],segments=compact,
                         mentions=list(mentions),mentions_registry_hash=mentions_hash,alignment_hash=alignment_ref,
                         inference_revision=inference_revision())


def target_kind(item,registry):
    elements={e.id:e for e in registry.elements}
    blocks=[elements[i] for i in item.block_ids if i in elements]
    if any(e.kind == "figure" for e in blocks):
        return "figure"
    if item.type in {"derivation","reasoning"} and any(
        e.kind == "formula" and set(e.parent_ids)&set(item.block_ids) for e in elements.values()):
        return "formula"
    matches={e.text for e in elements.values() if e.kind == "symbol" and (e.text.isalpha() or e.text in {"∇","∂","∑","∫","√"})
             and literal_contains(e.text,item.source_quote)}
    if len(matches)==1:
        return "symbol"
    if item.type == "worked_example" and any(
        e.kind=="formula" and set(e.parent_ids)&set(item.block_ids) for e in elements.values()):
        return "formula"
    if item.type == "definition":
        return "word"
    return "any"


def unavailable(item,reason):
    return {"knowledge_id":item.id,"page":item.page,"status":"unavailable","reason":reason,
            "target_element_id":None,"bbox":None,"candidate_element_ids":[],"proposals":[]}


def revalidate(record):
    request=TargetRequest.model_validate(record["request"])
    result=validate_selection(request,TargetSelections.model_validate(record["response"]["selection"]))
    return result["results"][0]


def load_grounding(folder,content):
    expected=sources(folder,content)
    path=folder/GROUNDING_NAME
    if not path.exists():
        return {"sources":expected,"fingerprint":fingerprint(expected),"records":{},"registries":{},
                "status":"missing","fallback_reason":"grounding_missing"}
    saved=json.loads(path.read_text(encoding="utf-8"))
    compare=lambda data:{k:v for k,v in data.items() if k!="validation"}
    if compare(saved["sources"]) != compare(expected):
        return {"sources":expected,"fingerprint":fingerprint(expected),"records":{},"registries":{},
                "status":"stale","fallback_reason":"grounding_stale"}
    saved["sources"]=expected
    saved["fingerprint"]=fingerprint(expected)
    for record in saved["records"].values():
        if "response" in record:
            try:
                record["decision"]=revalidate(record)
            except Exception:
                record["decision"]={**record["decision"],"status":"rejected","reason":"cached_result_invalid",
                                    "target_element_id":None,"bbox":None}
    return saved


def prepare_grounding(folder,content,alignment,segments):
    current=sources(folder,content)
    previous=load_grounding(folder,content)
    if previous.get("status") == "complete" and all("response" in r for r in previous["records"].values()):
        # Validation is repeated even on a no-inference resume.
        write_json(folder/GROUNDING_NAME,previous)
        return previous
    started=time.perf_counter()
    manifest={"schema_version":1,"sources":current,"fingerprint":fingerprint(current),
              "status":"partial","records":{},"registries":{},"telemetry":{"provider_requests":0,"cache_hits":0,
                                                                               "new_inferences":0,"oom_count":0}}
    work=folder/"grounding"/fingerprint({k:v for k,v in current.items() if k in {"input.pdf","registry_builder"}})[:20]
    provider=None
    try:
        registries={r.page:r for r in build_registries(folder/"input.pdf")}
        for page in sorted({i.page for i in content.items}):
            registry=registries.get(page)
            page_items=[i for i in content.items if i.page==page]
            if registry is None:
                for item in page_items:
                    manifest["records"][item.id]={"decision":unavailable(item,"registry_page_missing")}
                continue
            manifest["registries"][str(page)]=registry.model_dump(mode="json")
            original,image,viewport=render_page(folder/"input.pdf",registry,work/f"p{page}")
            mentions=[]; mentions_hash=""
            mention_path=folder/"mentions.json"
            if mention_path.exists():
                stored=json.loads(mention_path.read_text(encoding="utf-8"))
                if isinstance(stored,dict) and stored.get("registry_hashes",{}).get(str(page))==registry_fingerprint(registry):
                    mentions=[Mention.model_validate(m) for m in stored.get("mentions",[]) if m["page"]==page]
                    mentions_hash=registry_fingerprint(registry)
                else:
                    manifest.setdefault("warnings",[]).append(f"p{page}:stale_or_unversioned_mentions_ignored")
            for item in page_items:
                record={}
                try:
                    request=local_request(item,registry,viewport,image,segments,alignment,mentions,mentions_hash)
                    error=speech_error(request.items[0],request.segments)
                    if error:
                        raise ValueError(error)
                    record["request"]=request.model_dump(mode="json")
                    prior=previous.get("records",{}).get(item.id,{})
                    if "response" in prior and prior.get("request")==record["request"]:
                        record["response"]=prior["response"]
                        record["decision"]=revalidate(record)
                        manifest["records"][item.id]=record
                        manifest["telemetry"].setdefault("checkpoint_hits",0)
                        manifest["telemetry"]["checkpoint_hits"]+=1
                        write_json(folder/GROUNDING_NAME,manifest)
                        continue
                    if provider is None:
                        provider=PROVIDER_FACTORY(folder)
                    manifest["telemetry"]["provider_requests"]+=1
                    response=provider.select_targets(request,use_cache=True)
                    record["response"]=response
                    record["decision"]=revalidate(record)  # Never trust a child's accepted flag.
                    telemetry=manifest["telemetry"]
                    telemetry["cache_hits"]+=bool(response.get("cache_hit"))
                    telemetry["new_inferences"]+=bool(response.get("new_inference"))
                    telemetry["oom_count"]+=response.get("oom_count",0)
                except (ValueError,WorkerFailure) as error:
                    reason=str(error) if isinstance(error,WorkerFailure) else "invalid_source_or_request"
                    record["decision"]=unavailable(item,reason)
                    record["error_type"]=type(error).__name__
                    if isinstance(error,WorkerFailure):
                        record["failure_details"]=error.details
                manifest["records"][item.id]=record
                # A killed job can reuse already validated per-item records on resume.
                write_json(folder/GROUNDING_NAME,manifest)
        manifest["status"]="complete"
    except Exception as error:
        manifest["status"]="fallback"
        manifest["fallback_reason"]=type(error).__name__
        for item in content.items:
            manifest["records"].setdefault(item.id,{"decision":unavailable(item,"grounding_stage_unavailable")})
    finally:
        if provider is not None:
            try:
                provider.close()
            except Exception as error:
                manifest.setdefault("warnings",[]).append(f"worker_cleanup:{type(error).__name__}")
        manifest["telemetry"]["seconds"]=time.perf_counter()-started
        write_json(folder/GROUNDING_NAME,manifest)
    return manifest


def plan_grounded(folder,content,extraction,density,allow_overflow=False):
    notes=plan_annotations(content,extraction,density,allow_overflow=allow_overflow)
    selected={note.content_id:note for note in notes}
    manifest=load_grounding(folder,content)
    registries={int(page):ElementRegistry.model_validate(r) for page,r in manifest["registries"].items()}
    actions=[]
    for item in content.items:
        record=manifest["records"].get(item.id,{})
        decision=record.get("decision",unavailable(item,manifest.get("fallback_reason","grounding_unavailable")))
        accepted=decision["status"]=="accepted"
        registry=registries.get(item.page)
        element=next((e for e in registry.elements if e.id==decision.get("target_element_id")),None) if registry else None
        if accepted and (element is None or registry_fingerprint(registry)!=record.get("response",{}).get("grounding",{}).get("registry_hash")):
            accepted=False
            decision=unavailable(item,"registry_hash_mismatch")
        mark="none"
        selection_reason="unconfirmed_target_no_visual_mark"
        bbox=None
        if accepted:
            mark,selection_reason=choose_action(item,element)
            native=pymupdf.Rect(element.bbox)*pymupdf.Matrix(registry.width,registry.height)
            display=native*pymupdf.Matrix(registry.rotation_matrix)
            w,h=(registry.height,registry.width) if registry.rotation in {90,270} else (registry.width,registry.height)
            bbox=[display.x0/w,display.y0/h,display.x1/w,display.y1/h]
        # A figure block association cannot verify its internal z/w/Greek labels.
        # Keep the full claim in Detailed Notes; do not print an unverified formula
        # next to a graph that may depict a different variable.
        display_text=("그림 설명 → 상세 노트" if item.language=="ko" else "Figure explanation → Detailed Notes") if accepted and element.kind=="figure" else item.short_note
        evidence_id=stable_id("knowledge",folder.name,item.model_dump(mode="json"))
        decision_id=stable_id("grounding",evidence_id,manifest["fingerprint"],decision)
        action_id=stable_id("action",evidence_id,decision_id,ACTION_POLICY,density,item.short_note)
        provenance=AnnotationProvenance(action_id=action_id,knowledge_id=item.id,evidence_id=evidence_id,
                                        source_segment_ids=item.segment_ids,timestamp_range=(item.start,item.end),
                                        pdf_sha256=manifest["sources"]["input.pdf"],
                                        registry_fingerprint=registry_fingerprint(registry) if registry else "",
                                        target_element_id=element.id if accepted else None,
                                        target_kind=element.kind if accepted else None,grounding_status=decision["status"],
                                        fallback_reason=None if accepted else decision["reason"],decision_id=decision_id)
        action=AnnotationAction(action_id=action_id,knowledge_id=item.id,evidence_id=evidence_id,
                                source_segment_ids=item.segment_ids,source_quote=item.source_quote,
                                timestamp_range=(item.start,item.end),page=item.page,
                                target_element_id=provenance.target_element_id,target_kind=provenance.target_kind,
                                grounding_status=decision["status"],annotation_type=mark,annotation_text=display_text,
                                selection_reason=selection_reason,
                                priority=item.importance,density=density,registry_fingerprint=provenance.registry_fingerprint,
                                selected=item.id in selected,reason="planned" if item.id in selected else "density_threshold" if allow_overflow else "density_space_budget",
                                provenance=provenance)
        actions.append(action)
        if item.id in selected:
            note=selected[item.id]
            note.note=display_text
            note.provenance=provenance
            note.action_reason=selection_reason
            if accepted:
                note.target_bboxes=[tuple(bbox)]
                note.target_mark=mark
            elif decision["status"] in {"ambiguous","rejected","unmatched"}:
                note.block_ids=[]
                note.target_bboxes=[]
                note.target_mark="none"
    return notes,actions,manifest


def save_graph(folder,content,segments,alignment,actions,manifest,annotations,density,export=None):
    root=Path(__file__).parent
    revision=fingerprint([manifest["fingerprint"],ACTION_POLICY,density,annotations.note_font,
                          [a.action_id for a in actions],
                          [n.sketch.model_dump(mode="json") for p in annotations.pages for n in p.notes],
                          hash_file(root/"layout.py"),hash_file(root/"export_pdf.py")])
    revision=fingerprint([revision,annotations.layout_engine,annotations.ink_style,annotations.overview,
                          hash_file(root/"layout_v2.py") if annotations.layout_engine=="v2" else None])
    saved_path=folder/f"evidence_graph-{density}.json"
    if export is None and saved_path.exists():
        old=json.loads(saved_path.read_text())
        prior=old.get("export")
        if old.get("revision")==revision and prior and Path(prior["file"]).name==prior["file"]:
            artifact=folder/prior["file"]
            if artifact.exists() and hash_file(artifact)==prior["sha256"]:
                export=prior
    nodes={}; edges=[]
    def node(kind,key,data):
        nodes[key]=EvidenceNode(id=key,kind=kind,data=data)
        return key
    def edge(source,target,relation):
        edges.append(EvidenceEdge(source=source,target=target,relation=relation))
    audio=next(iter(sorted(folder.glob("audio_source.*"))),None)
    audio_id=node("recording",stable_id("recording",folder.name,hash_file(audio) if audio else None),
                  {"job_id":folder.name,"sha256":hash_file(audio) if audio else None,"available":audio is not None,
                   "job_options":json.loads((folder/"job.json").read_text()) if (folder/"job.json").exists() else {}})
    segment_nodes={}
    overview_id=None
    if annotations.overview:
        overview_id=node("lecture_overview",stable_id("overview",annotations.overview["fingerprint"]),annotations.overview)
    for index,segment in enumerate(segments):
        sid=node("speech_segment",stable_id("segment",folder.name,index,segment.model_dump(mode="json")),
                 {"source_segment_id":index,**segment.model_dump(mode="json")})
        segment_nodes[index]=sid
        edge(audio_id,sid,"transcribed_as")
    rendered={note.content_id:(page,note) for page in annotations.pages for note in page.notes}
    for action in actions:
        item=next(i for i in content.items if i.id==action.knowledge_id)
        kid=node("knowledge",action.evidence_id,{"knowledge_id":item.id,**item.model_dump(mode="json")})
        if overview_id and item.id in annotations.overview["validated_knowledge_ids"]:
            edge(kid,overview_id,"summarized_in")
        for index in item.segment_ids:
            if index not in segment_nodes:
                raise ValueError("invalid graph segment reference")
            edge(segment_nodes[index],kid,"supports")
        windows=[w.model_dump(mode="json") for w in alignment.windows if w.start<item.end and w.end>item.start]
        alignment_id=node("page_alignment",stable_id("alignment",item.page,windows),{"page":item.page,"windows":windows})
        edge(alignment_id,kid,"assigned_to_page")
        record=manifest["records"].get(item.id,{})
        did=node("grounding_decision",action.provenance.decision_id,
                 {"status":action.grounding_status,"reason":action.provenance.fallback_reason or record.get("decision",{}).get("reason",action.reason),
                  "request_fingerprint":fingerprint(record.get("request",{})),
                  "model":MODEL_ID,"revision":MODEL_REVISION,"inference_revision":manifest["sources"]["inference"]})
        edge(kid,did,"grounding_requested")
        if action.target_element_id:
            eid=node("slide_element",stable_id("element",action.registry_fingerprint,action.target_element_id),
                     {"canonical_id":action.target_element_id,"registry_fingerprint":action.registry_fingerprint,
                      "pdf_sha256":manifest["sources"]["input.pdf"],"page":item.page,
                      "bbox":record["decision"]["bbox"]})
            edge(did,eid,"validated_target")
        aid=node("annotation_action",action.action_id,action.model_dump(mode="json"))
        edge(did,aid,"planned_as")
        if item.id in rendered:
            page,note=rendered[item.id]
            rid=node("rendered_annotation",stable_id("render",action.action_id,note.sketch.model_dump(mode="json")),
                     {"annotation_id":note.id,"pdf_page":page.page+1,"source_page":page.page,
                      "source_slide_index":page.page-1,"export_page_index":page.page,"density":density,
                      "sketch_sha256":fingerprint(note.sketch.model_dump(mode="json")),"export":export})
            edge(aid,rid,"rendered_as")
    counts=Counter(a.grounding_status for a in actions)
    rejected_count=sum(v.status=="rejected" for v in content.validation)
    observed=len(content.items)+rejected_count
    denominator_complete=not any(v.status=="unavailable" for v in content.validation)
    metrics={"knowledge_extraction_count":len(content.items)+sum(v.status=="rejected" for v in content.validation),
             "evidence_validated_count":len(content.items),"extraction_rejection_count":sum(v.status=="rejected" for v in content.validation),
             "evidence_validation_pass_rate":len(content.items)/observed if observed and denominator_complete else None,
             "evidence_candidate_denominator_complete":denominator_complete,
             "grounding_status_counts":dict(counts),"grounding_attempted":manifest.get("telemetry",{}).get("provider_requests",0),
             "actions_generated":len(actions),"actions_selected":sum(a.selected for a in actions),
             "annotations_rendered":len(rendered),"distinct_knowledge_rendered":len(rendered),
             "extracted_item_render_retention":len(rendered)/len(content.items) if content.items else None,
             "lecture_semantic_coverage":None,"human_target_accuracy":None,
             "fallback_reasons":dict(Counter(a.provenance.fallback_reason for a in actions if a.provenance.fallback_reason)),
             "telemetry":manifest.get("telemetry",{})}
    graph=EvidenceGraph(job_id=folder.name,revision=revision,
                        sources=manifest["sources"],nodes=list(nodes.values()),edges=edges,metrics=metrics,export=export)
    write_json(folder/f"annotation_actions-{density}.json",[a.model_dump(mode="json") for a in actions])
    write_json(folder/f"evidence_graph-{density}.json",graph.model_dump(mode="json"))
    if density=="balanced":
        write_json(folder/"annotation_actions.json",[a.model_dump(mode="json") for a in actions])
        write_json(folder/"evidence_graph.json",graph.model_dump(mode="json"))
    annotations.grounding={"schema_version":1,"revision":graph.revision,"status":manifest.get("status"),
                           "metrics":metrics,"sources":manifest["sources"]}
    return graph
