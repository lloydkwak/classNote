"""Optional production v3 preparation + read-only cached render adapter."""
import copy
import json
from pathlib import Path

from app.annotation_agent.schema import Intent,VisionRequest,VisualPlan,Review
from app.annotation_agent.tools import AnnotationTools,ToolError
from app.annotation_agent.planning import plan_page
from app.annotation_agent.rendering import notes_actions,render_state
from app.cache import hash_file
from app.config import get_settings
from app.evidence import fingerprint
from app.grounding.document import build_registries,render_page,registry_fingerprint
from app.grounding.math_elements import enrich_math_registry
from app.grounding.figure_elements import enrich_figure_registry
from app.grounding.ipc import IsolatedVLMProvider,WorkerFailure
from app.models import Segment,Alignment
from app.pipeline.grounded_notes import write_json
from app.vlm.schema import MODEL_ID,MODEL_REVISION

VERSION='visual-annotation-agent-v3-1'
PROVIDER_FACTORY=IsolatedVLMProvider


def configured_mode(folder):
    file=Path(folder)/'job.json'
    return json.loads(file.read_text()).get('annotation_mode','legacy') if file.exists() else get_settings().annotation_mode


def source_signature(folder,content):
    root=Path(__file__).resolve().parents[1]
    policies=[*sorted(Path(__file__).parent.glob('*.py')),root/'grounding/math_elements.py',root/'grounding/figure_elements.py',root/'pipeline/layout_v2.py',
              root/'pipeline/consolidate.py',root/'pipeline/export_pdf.py',root/'vlm/runtime.py']
    s=get_settings()
    return {'version':VERSION,'files':{n:hash_file(Path(folder)/n) for n in ['input.pdf','segments.json','alignment.json','extraction.json']},
            'knowledge':fingerprint([i.model_dump(mode='json') for i in content.items]),
            'policy':fingerprint({p.name:hash_file(p) for p in policies}),
            'llm_provider':s.llm_provider,'llm_model':s.llm_model,'vlm_model':MODEL_ID,'vlm_revision':MODEL_REVISION,
            'review_profile':'detailed-natural','max_revisions':min(2,max(0,s.annotation_review_rounds))}


def _path(folder,mode):return Path(folder)/f'annotation-agent-{mode}.json'


def load_agent(folder,content,mode=None):
    mode=mode or configured_mode(folder);path=_path(folder,mode)
    if not path.exists():return None
    data=json.loads(path.read_text())
    if data.get('sources')!=source_signature(folder,content):return None
    if data.get('integrity')!=fingerprint(data.get('pages',{})):return None
    return data


def _candidate_rows(registry,intents):
    elements={e.id:e for e in registry.elements};ids={identity for i in intents for identity in i.target_ids}
    for identity in list(ids):
        e=elements.get(identity)
        if e:
            ids.update(e.parent_ids)
            if e.kind=='figure':ids.update(c.id for c in registry.elements if e.id in c.parent_ids and c.kind=='figure_part')
            if e.kind=='formula' and e.recognition!='native_subexpression':ids.update(c.id for c in registry.elements if e.id in c.parent_ids and c.recognition=='native_subexpression')
            ids.update(c.id for c in registry.elements if c.kind==e.kind and c.text==e.text)
    return [[e.id,e.kind,e.text,[p for p in e.parent_ids if p in ids]] for e in registry.elements if e.id in ids and e.kind!='block']


def _track(telemetry,response):
    telemetry['vlm_calls']+=1
    telemetry['new_inferences']+=bool(response.get('new_inference'))
    telemetry['cache_hits']+=bool(response.get('cache_hit'))
    telemetry['oom_count']+=response.get('oom_count',0)
    telemetry['gpu_metrics'].append(response.get('metrics',{}))


def prepare_agent(folder,content,segments,alignment,*,mode=None,llm=None,vlm=None):
    from app.llm import get_provider
    folder=Path(folder).resolve();mode=mode or configured_mode(folder)
    if mode not in {'ai_planner','visual_agent'}:raise ValueError('unsupported_agent_mode')
    saved=load_agent(folder,content,mode)
    if saved and saved.get('status')=='prepared':return {**saved,'cache_hit':True}
    data={'schema_version':3,'mode':mode,'sources':source_signature(folder,content),'pages':{},'status':'partial',
          'telemetry':{'llm_calls':0,'semantic_cache_hits':0,'vlm_calls':0,'new_inferences':0,'cache_hits':0,'oom_count':0,'gpu_metrics':[]},
          'review_scope':{'density':'detailed','style':'natural','max_revisions':min(2,max(0,get_settings().annotation_review_rounds))}}
    data['fingerprint']=fingerprint(data['sources'])
    owns_vlm=vlm is None
    try:
        llm=llm or get_provider()
        registries={};graphs={}
        for base in build_registries(folder/'input.pdf'):
            registry,graph=enrich_math_registry(folder/'input.pdf',base)
            registry,figure_graph=enrich_figure_registry(folder/'input.pdf',registry)
            registries[registry.page]=registry;graphs[registry.page]={'math':graph,'figures':figure_graph}
        for page in sorted({i.page for i in content.items}):
            registry=registries[page];items=[i for i in content.items if i.page==page]
            work=folder/'agent-previews'/f'p{page}'
            original,image,viewport=render_page(folder/'input.pdf',registry,work)
            semantic=plan_page(folder,registry,items,llm)
            data['telemetry']['llm_calls']+=0 if semantic.get('cache_hit') else semantic.get('calls',0)
            data['telemetry']['semantic_cache_hits']+=bool(semantic.get('cache_hit'))
            record={'registry':registry.model_dump(mode='json'),'math_graph':graphs[page],'semantic':semantic,
                    'visual':[],'reviews':[],'state_history':[],'tool_calls':[],'state':{},'status':'prepared'}
            data['pages'][str(page)]=record
            if semantic.get('status')!='planned':
                record.update(status='fallback',reason=semantic.get('reason','semantic_unavailable'));continue
            intents=[]
            for raw in semantic['plan']['intents']:
                intent=Intent.model_validate(raw)
                if intent.tool=='connect' and len(intent.target_ids)>2:
                    intents.extend(intent.model_copy(update={'intent_id':intent.intent_id+f'_{n}','target_ids':list(pair)}) for n,pair in enumerate(zip(intent.target_ids,intent.target_ids[1:])))
                elif intent.tool in {'highlight','underline','circle','box','check'} and len(intent.target_ids)>1:
                    intents.extend(intent.model_copy(update={'intent_id':intent.intent_id+f'_{n}','target_ids':[target]}) for n,target in enumerate(intent.target_ids))
                else:intents.append(intent)
            def renderer(page_id,state,export):
                return render_state(folder,content,registries,{str(page_id):state},page=page_id,include_overview=export)
            tools=AnnotationTools(folder,registry,items,segments,alignment,viewport,renderer=renderer)
            # B is independently valid even if C's model fails or emits an invalid edit.
            for intent in intents:tools.execute(intent)
            record['semantic_state']=copy.deepcopy(tools.state)
            if mode=='visual_agent':
                if vlm is None:vlm=PROVIDER_FACTORY(folder)
                for start in range(0,len(intents),2):
                    batch=intents[start:start+2]
                    rows=_candidate_rows(registry,batch)
                    payload={'intents':[i.model_dump(exclude={'evidence_quote'}) for i in batch],'evidence':{i.knowledge_id:tools.get_evidence(i.knowledge_id)['source_quote'] for i in batch},'learning_points':[l for l in semantic['plan'].get('learning_points',[]) if l['knowledge_id'] in {i.knowledge_id for i in batch}],'elements_columns':['id','type','text','parents'],
                             'elements':rows,'registry_fingerprint':registry_fingerprint(registry)}
                    try:
                        response=vlm.agent(VisionRequest(page=page,image_path=image,operation='agent_plan',payload=payload))
                        _track(data['telemetry'],response);record['visual'].append(response)
                        choices=VisualPlan.model_validate(response['agent_plan']).choices
                        if len({c.intent_id for c in choices})!=len(choices):raise ValueError('duplicate_visual_choice')
                        if {c.intent_id for c in choices}!={i.intent_id for i in batch}:raise ValueError('incomplete_visual_plan')
                        allowed_targets={r[0] for r in rows}
                        if any(set(c.target_ids)-allowed_targets for c in choices):raise ValueError('visual_target_not_supplied')
                        for choice in choices:
                            intent=next((i for i in batch if i.intent_id==choice.intent_id),None)
                            if intent is None:raise ValueError('unknown_visual_intent')
                            if choice.confidence<.8 or choice.tool=='skip' or choice.warning:
                                record.setdefault('visual_warnings',[]).append(choice.model_dump())
                                for aid in [aid for aid,r in tools.state.items() if r['intent']['intent_id']==intent.intent_id]:tools.remove_annotation(aid)
                                continue
                            proposed=intent.model_copy(update={'target_ids':choice.target_ids,'tool':choice.tool,
                                                             'text':intent.text if choice.tool=='write_near' else ''})
                            # Only remove the intent's previous state AFTER its replacement validates.
                            previous=[aid for aid,r in tools.state.items() if r['intent']['intent_id']==intent.intent_id]
                            result=tools.execute(proposed,visual=True)
                            if result['status'] in {'applied','reused'}:
                                for aid in previous:
                                    if aid!=result['action_id']:tools.remove_annotation(aid)
                    except (WorkerFailure,ValueError) as error:
                        record.setdefault('fallbacks',[]).append({'stage':'visual_plan','reason':str(error)[:180]})
                record['state_history'].append(copy.deepcopy(tools.state))
                # Each round sees a real exported page. At most two mutation rounds.
                maximum=data['review_scope']['max_revisions']
                for round_number in range(maximum+1):
                    if not tools.state:break
                    annotations,output,preview=tools.render_preview(page)
                    record['preview']=str(preview.relative_to(folder))
                    record['layout_metrics']={str(p.page):p.layout_metrics for p in annotations.pages}
                    by_id={n.id:n for p in annotations.pages for n in p.notes}
                    state_rows=list(tools.state.values())
                    edits=[]
                    for start in range(0,len(state_rows),2):
                        batch=state_rows[start:start+2];related=[Intent.model_validate(r['intent']) for r in batch]
                        evidence={i.knowledge_id:{k:v for k,v in tools.get_evidence(i.knowledge_id).items() if k in {'source_quote','content','title','start','end','segment_ids'}} for i in related}
                        payload={'page':page,'round':round_number,'actions':[{'action_id':r['action_id'],'intent':{k:v for k,v in r['intent'].items() if k!='evidence_quote'},
                                  'placement':by_id[r['action_id']].sketch.placement if r['action_id'] in by_id else {'status':'not_rendered'}} for r in batch],
                                 'evidence':evidence,'elements_columns':['id','type','text','parents'],'elements':_candidate_rows(registry,related),
                                 'instruction':'Other marks may be visible; edit only these supplied action IDs.'}
                        try:
                            response=vlm.agent(VisionRequest(page=page,image_path=preview,operation='agent_review',payload=payload))
                            _track(data['telemetry'],response)
                            review=Review.model_validate(response['agent_review'])
                            allowed={r['action_id'] for r in batch}
                            if any(e.action_id not in allowed for e in review.decisions):raise ValueError('unknown_review_action')
                            allowed_targets={r[0] for r in payload['elements']}
                            if any(set(e.target_ids)-allowed_targets for e in review.decisions):raise ValueError('review_target_not_supplied')
                            record['reviews'].append({'round':round_number,'response':response,'decision':review.model_dump()})
                            if review.verdict=='revise':edits.extend(review.decisions)
                        except (WorkerFailure,ValueError) as error:
                            record.setdefault('fallbacks',[]).append({'stage':'review','reason':str(error)[:180]})
                    if not edits or round_number==maximum:break
                    snapshot=copy.deepcopy(tools.state)
                    for edit in edits:
                        old=tools.state.get(edit.action_id)
                        if old is None or edit.operation=='accept':continue
                        try:
                            if edit.operation=='remove':tools.remove_annotation(edit.action_id);continue
                            proposed=Intent.model_validate(old['intent'])
                            updates={}
                            if edit.operation=='retarget':updates['target_ids']=edit.target_ids
                            if edit.operation=='replace':updates['tool']=edit.tool or proposed.tool;updates['text']='' if edit.tool!='write_near' else edit.text
                            if edit.operation=='shorten':
                                if edit.text not in proposed.text:raise ToolError('shortening_must_preserve_supported_phrase')
                                updates['text']=edit.text
                            if edit.operation in {'move','reroute'}:updates['placement']=edit.placement
                            if edit.operation=='reroute' and edit.placement==proposed.placement:
                                updates['placement']='above' if proposed.placement!='above' else 'below'
                            tools.replace_annotation(edit.action_id,proposed.model_copy(update=updates),visual=True)
                        except (ToolError,ValueError) as error:
                            record.setdefault('rejected_edits',[]).append({'action_id':edit.action_id,'reason':str(error)})
                    if fingerprint(snapshot)==fingerprint(tools.state):break
                    record.setdefault('revision_count',0);record['revision_count']+=1
                    try:
                        tools.validate_annotations(page)
                        annotations,_,_=render_state(folder,content,registries,{str(page):tools.state},page=page)
                        if any(p.layout_metrics.get('original_text_occlusion_area',0)>0 or p.layout_metrics.get('out_of_page_count',0)>0 for p in annotations.pages):
                            raise ToolError('revised_layout_unsafe')
                    except Exception as error:
                        tools.state=snapshot
                        record.setdefault('fallbacks',[]).append({'stage':'revision_rollback','reason':str(error)[:180]});break
                    record['state_history'].append(copy.deepcopy(tools.state))
            record['state']=tools.state;record['tool_calls']=tools.calls
            data['integrity']=fingerprint(data['pages'])
            write_json(_path(folder,mode),data)
        data['status']='prepared' if all(p['status']=='prepared' and not p.get('fallbacks') for p in data['pages'].values()) else 'partial'
    except Exception as error:
        data.update(status='fallback',reason=type(error).__name__)
    finally:
        if owns_vlm and vlm is not None:
            try:vlm.close()
            except Exception:pass
        data['integrity']=fingerprint(data['pages'])
        write_json(_path(folder,mode),data)
    return data


def cached_plan(folder,content,extraction,density,mode=None):
    from app.pipeline.rich_notes import plan_annotations
    folder=Path(folder).resolve();mode=mode or configured_mode(folder)
    data=load_agent(folder,content,mode)
    if data is None or not data.get('pages'):
        return plan_annotations(content,extraction,density,allow_overflow=True),[],None,{'mode':mode,'status':'fallback','reason':'agent_missing_or_stale'}
    from app.grounding.schema import ElementRegistry
    registries={int(p):ElementRegistry.model_validate(row['registry']) for p,row in data['pages'].items()}
    states={p:row['state'] for p,row in data['pages'].items()}
    notes,actions=notes_actions(folder,content,registries,states,density)
    fallback_pages={int(p) for p,row in data['pages'].items() if row['status']=='fallback'}
    if fallback_pages:
        notes.extend(n for n in plan_annotations(content,extraction,density,allow_overflow=True) if n.page in fallback_pages)
    sources={'input.pdf':hash_file(folder/'input.pdf'),'inference':data['fingerprint'],**data['sources']['files']}
    manifest={'sources':sources,'fingerprint':data['fingerprint'],'status':data['status'],'registries':{str(p):r.model_dump(mode='json') for p,r in registries.items()},
              'records':{},'action_records':{},'telemetry':data['telemetry']}
    for p,rows in states.items():
        for aid,row in rows.items():
            target=row['targets'][0]
            record={'decision':{'status':'accepted','reason':'validated_agent_tool','bbox':target['bbox']},
                    'request':{'intent':row['intent']},'targets':row['targets'],'provider_model':MODEL_ID if row['visual_confirmed'] else next((u.get('model') for u in reversed(data['pages'][p]['semantic'].get('usage',[])) if u and u.get('model')),data['sources'].get('llm_model') or 'configured-llm')}
            manifest['action_records'][aid]=record;manifest['records'].setdefault(row['intent']['knowledge_id'],record)
    report={'mode':mode,'status':data['status'],'fingerprint':data['fingerprint'],'telemetry':data['telemetry'],
            'review_scope':data['review_scope'],'pages':{p:{'action_count':len(row['state']),'reviews':len(row['reviews']),
                'fallbacks':row.get('fallbacks',[]),'tool_rejections':sum(not c.get('ok') for c in row['tool_calls']),
                'semantic_intents':len(row['semantic'].get('plan',{}).get('intents',[])),
                'tool_attempts':len([c for c in row['tool_calls'] if c.get('knowledge_id')]),
                'rejection_reasons':[c.get('error') for c in row['tool_calls'] if not c.get('ok')],
                'distinct_knowledge':len({s['intent']['knowledge_id'] for s in row['state'].values()}),
                'revision_count':row.get('revision_count',0),'semantic_status':row['semantic'].get('status'),
                'semantic_reason':row['semantic'].get('reason')} for p,row in data['pages'].items()}}
    for item in content.items:
        if item.id not in manifest['records']:
            page=data['pages'].get(str(item.page),{})
            failures=[c for c in page.get('tool_calls',[]) if c.get('knowledge_id')==item.id and not c.get('ok')]
            manifest['records'][item.id]={'decision':{'status':'rejected' if failures else 'unmatched','reason':failures[0]['error'] if failures else 'no_supported_action','bbox':None}}
    return notes,actions,manifest,report
