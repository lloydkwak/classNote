"""Counts and uncertainty, never a replacement for human alignment/coverage labels."""

import json
from collections import Counter

from app.models import Alignment, PdfExtraction, Segment


def build_loss_audit(folder,content,annotations):
    alignment=Alignment.model_validate_json((folder/"alignment.json").read_text())
    segments=[Segment.model_validate(s) for s in json.loads((folder/"segments.json").read_text())]
    extraction=PdfExtraction.model_validate_json((folder/"extraction.json").read_text())
    result=[]
    for page in extraction.pages:
        windows=[w for w in alignment.windows if w.page==page.page]
        spans=[s for s in content.spans if s.get("page")==page.page]
        ids=[s["span_id"] for s in spans]
        validation=[v for v in content.validation if any(span in v.item_id for span in ids)]
        items=[i for i in content.items if i.page==page.page]
        rendered=next((p.notes for p in annotations.pages if p.page==page.page),[])
        planning=[p for p in annotations.planning if p.get("page")==page.page or p.get("item_id") in {i.id for i in items}]
        grounding=[p["grounding"] for p in planning if p.get("grounding")]
        confidence=min((w.confidence for w in windows),default=None)
        reasons=Counter(v.reason for v in validation if v.status!="accepted")
        if windows and not items and not validation: reasons["not_extracted"]+=1
        reasons.update("not_selected_by_density" for p in planning if p.get("status")=="omitted")
        result.append({"page":page.page,"title":page.title,
            "audio_start":min((w.start for w in windows),default=None),"audio_end":max((w.end for w in windows),default=None),
            "audio_ranges":[{"start":w.start,"end":w.end,"confidence":w.confidence} for w in windows],
            "alignment_confidence_min":confidence,"alignment_uncertain":confidence is not None and confidence<.3,
            "stt_segments_total":len(segments),"aligned_segments":sum(any(s.start<w.end and s.end>w.start for w in windows) for s in segments),
            "knowledge_candidates":len(items)+sum(v.status=="rejected" for v in validation),
            "evidence_valid_knowledge":len(items),"grounded_knowledge":sum(g["grounding_status"]=="accepted" for g in grounding),
            "planned_actions":sum(p.get("status")=="selected" for p in planning) if planning else len(rendered),
            "placed_actions":len(rendered),"rendered_annotations":len(rendered),
            "reasons":dict(reasons),"extraction_unavailable":any(v.status=="unavailable" for v in validation),
            "grounding_reasons":dict(Counter(n.provenance.fallback_reason for n in rendered if n.provenance and n.provenance.fallback_reason)),
            "layout_statuses":dict(Counter(n.sketch.placement.get("status","legacy") for n in rendered))})
        result[-1]["stage_losses"]={
            "not_extracted":int(bool(windows) and not items and not validation),
            "failed_evidence_validation":sum(v.status=="rejected" for v in validation),
            "extraction_unavailable":sum(v.status=="unavailable" for v in validation),
            "grounding_rejected":sum(g["grounding_status"]=="rejected" for g in grounding),
            "not_selected_by_density":sum(p.get("status")=="omitted" for p in planning),
            "unplaced_by_layout":0,"render_failed":None,
        }
        result[-1]["render_state"]="layout_ready_pdf_verified_separately"
    return result
