"""Cached semantic planning. No inference is allowed in load/render/density paths."""
import json
import time
from pathlib import Path

from app.annotation_agent.schema import SemanticPlan
from app.annotation_agent.tools import native_text_rows
from app.annotation_agent.prompts import SEMANTIC_VERSION,SEMANTIC_PROMPT
from app.evidence import fingerprint
from app.grounding.document import registry_fingerprint
from app.pipeline.grounded_notes import write_json


def plan_page(folder,registry,items,provider):
    # Hide irrelevant page elements and duplicate character aliases from planning.
    # This narrows IDs, not evidence: full source content and native slide rows stay.
    blocks={identity for item in items for identity in item.block_ids}
    eligible=[];seen=set()
    for e in registry.elements:
        if e.kind=='block' or (blocks and e.id not in blocks and not set(e.parent_ids)&blocks):continue
        if e.recognition=='native_pdf_character':continue
        if e.recognition=='native_math_glyph' and e.text.isascii() and e.text.isalpha():continue
        key=(e.text,tuple(round(v,5) for v in e.bbox))
        if key in seen:continue
        seen.add(key);eligible.append(e)
    target_ids={item.id:[e.id for e in eligible if not item.block_ids or e.id in item.block_ids or set(e.parent_ids)&set(item.block_ids)] for item in items}
    payload={'page':registry.page,'registry_fingerprint':registry_fingerprint(registry),
             'knowledge':[i.model_dump(mode='json') for i in items],
             'elements':[[e.id,e.kind,e.text,[p for p in e.parent_ids if p in blocks],e.recognition] for e in eligible],
             'knowledge_target_ids':target_ids,
             'native_slide_text':[e.text for e in registry.elements if e.kind=='block'],
             'all_printed_rows':native_text_rows(Path(folder)/'input.pdf',registry.page),
             'preferred_math_parts':[[e.id,e.text,e.parent_ids] for e in eligible if e.recognition=='native_subexpression']}
    key=fingerprint({'version':SEMANTIC_VERSION,'prompt':SEMANTIC_PROMPT,'schema':SemanticPlan.model_json_schema(),
                     'provider':provider.name,'model':provider.model,'payload':payload})
    path=Path(folder)/'agent-cache'/f'semantic-{key}.json'
    if path.exists():
        result=json.loads(path.read_text());SemanticPlan.model_validate(result['plan'])
        return {**result,'cache_hit':True}
    result={'fingerprint':key,'registry_fingerprint':registry_fingerprint(registry),'provider':provider.name,
            'model':provider.model,'calls':0,'usage':[],'cache_hit':False,'status':'unavailable'}
    started=time.perf_counter()
    try:
        serialized=json.dumps(payload,ensure_ascii=False,separators=(',',':'))
        if len(serialized)>140000:raise ValueError('semantic_page_budget_exceeded_no_truncation')
        feedback=''
        for attempt in range(2):
            result['calls']+=1
            try:
                raw=provider.complete_json(SEMANTIC_PROMPT+serialized+('\nRepair the following validation error, then return the full plan: '+feedback if attempt else ''))
                result['usage'].append(provider.last_usage)
                write_json(Path(folder)/'agent-attempts'/f'{key}-{attempt}.json',json.loads(raw))
                decoded=json.loads(raw)
                by_id={i.id:i for i in items}
                for row in decoded.get('intents',[]):
                    if 'evidence_quote' not in row and row.get('knowledge_id') in by_id:
                        row['evidence_quote']=by_id[row['knowledge_id']].source_quote
                plan=SemanticPlan.model_validate(decoded)
                known={i.id for i in items}
                if any(i.knowledge_id not in known for i in plan.intents):raise ValueError('unknown_knowledge_id')
                if any(set(i.target_ids)-set(target_ids[i.knowledge_id]) for i in plan.intents):raise ValueError('target_outside_knowledge_scope')
                result.update(status='planned',plan=plan.model_dump(mode='json'))
                break
            except ValueError as error:
                feedback=str(error)[:1800]
                if attempt:raise
    except Exception as error:result['reason']=type(error).__name__
    result['seconds']=time.perf_counter()-started
    if result['status']=='planned':write_json(path,result)
    return result
