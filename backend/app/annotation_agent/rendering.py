"""Translate validated action states to the existing Note/layout/export contracts."""
import json
from pathlib import Path

import pymupdf

from app.evidence import AnnotationAction,stable_id,fingerprint
from app.models import Note,AnnotationProvenance
from app.grounding.document import display_box,registry_fingerprint

MARK={'highlight':'highlight','underline':'underline','circle':'circle','box':'box','connect':'arrow',
      'write_near':'none','bracket':'bracket','check':'check'}


def notes_actions(folder,content,registries,states,density='balanced'):
    items={i.id:i for i in content.items};notes=[];actions=[]
    thresholds={'minimal':5,'balanced':3,'detailed':1}
    for page,rows in states.items():
        registry=registries[int(page)];elements={e.id:e for e in registry.elements}
        for row in rows.values():
            intent=row['intent'];item=items[intent['knowledge_id']]
            # Density filters actions without changing source knowledge/plan/review caches.
            selected=intent['priority']>=thresholds[density]
            if density=='minimal' and intent['tool'] in {'write_near','connect'}:selected=False
            target_ids=intent['target_ids'];primary=elements[target_ids[0]]
            did=stable_id('agent_decision',row['action_id'],row['registry_fingerprint'],row['targets'])
            provenance=AnnotationProvenance(action_id=row['action_id'],knowledge_id=item.id,evidence_id=row['evidence_id'],
                source_segment_ids=item.segment_ids,timestamp_range=(item.start,item.end),pdf_sha256=registry.pdf_sha256,
                registry_fingerprint=row['registry_fingerprint'],target_element_id=primary.id,target_kind=primary.kind,
                grounding_status='accepted',fallback_reason=None,decision_id=did)
            actions.append(AnnotationAction(action_id=row['action_id'],knowledge_id=item.id,evidence_id=row['evidence_id'],
                source_segment_ids=item.segment_ids,source_quote=item.source_quote,timestamp_range=(item.start,item.end),
                page=item.page,target_element_id=primary.id,target_element_ids=target_ids,tool=intent['tool'],target_kind=primary.kind,grounding_status='accepted',
                annotation_type=MARK[intent['tool']],selection_reason=intent['reason'],annotation_text=intent['text'],
                priority=intent['priority'],density=density,registry_fingerprint=row['registry_fingerprint'],selected=selected,
                reason='ai_plan' if selected else 'not_selected_by_density',provenance=provenance))
            if selected:
                notes.append(Note(content_id=item.id,page=item.page,block_ids=item.block_ids,type='explanation',
                    note=intent['text'],quote=item.source_quote,language=item.language,importance=intent['priority'],start=item.start,end=item.end,
                    target_bboxes=[display_box(registry,elements[i].bbox) for i in target_ids],target_mark=MARK[intent['tool']],
                    action_reason=intent['reason'],provenance=provenance,
                    agent_action={'tool':intent['tool'],'placement':intent.get('placement','near_target'),'revision':row.get('revision',0),
                                  'target_ids':target_ids,'novelty':intent['novelty']}))
    return notes,actions


def render_state(folder,content,registries,states,*,page=None,density='detailed',style='natural',include_overview=False):
    from app.models import PdfExtraction
    from app.pipeline.layout_v2 import enrich_geometry
    from app.pipeline.consolidate import consolidate
    from app.pipeline.export_pdf import export_annotated_pdf
    folder=Path(folder)
    extraction=PdfExtraction.model_validate_json((folder/'extraction.json').read_text())
    enrich_geometry(folder/'input.pdf',extraction)
    notes,actions=notes_actions(folder,content,registries,states,density)
    annotations=consolidate(notes,extraction,folder.name,max_notes_per_page=len(notes),layout_engine='v2',ink_style=style)
    if include_overview:
        from app.pipeline.overview import attach_overview
        attach_overview(folder,annotations,content)
    key=fingerprint([states,density,style,page,annotations.model_dump(mode='json')])[:24]
    work=folder/'agent-previews';work.mkdir(exist_ok=True)
    output=work/f'preview-{key}.pdf'
    export_annotated_pdf(folder/'input.pdf',annotations,output,include_overview=include_overview)
    image=None
    if page is not None:
        image=work/f'p{page}-{key}.png'
        with pymupdf.open(output) as document:
            sheet=document[page-1+int(include_overview)]
            sheet.get_pixmap(matrix=pymupdf.Matrix(1.5,1.5),alpha=False).save(image)
    return annotations,output,image
