"""Mock providers only; actual GPU runs are separate artifacts."""
import copy,json
import pymupdf
import pytest
from pydantic import ValidationError
from app.annotation_agent.schema import Intent,SemanticPlan,Review
from app.annotation_agent.tools import AnnotationTools,ToolError,evidence_id
from app.annotation_agent import pipeline as agent
from app.annotation_agent.rendering import render_state,notes_actions
from app.grounding.document import build_registries,render_page,registry_fingerprint
from app.grounding.math_elements import enrich_math_registry
from app.models import Alignment,AlignedWindow,Segment
from app.pipeline.rich_notes import ContentItem,DetailedNotes
from app.pipeline.pdf_extract import extract_pdf,add_geometry
from app.pipeline.grounded_notes import write_json
from test_grounding import make_slide

@pytest.fixture
def lesson(tmp_path):
    f=tmp_path/'jobs'/'agentfixture';f.mkdir(parents=True);make_slide(f/'input.pdf')
    r=build_registries(f/'input.pdf')[0];_,_,v=render_page(f/'input.pdf',r,f/'renders')
    alpha=next(e for e in r.elements if e.text=='alpha' and 'p1_l1' in e.parent_ids)
    block=next(e.id for e in r.elements if e.kind=='block' and e.id in alpha.parent_ids)
    q='alpha controls the update size'
    item=ContentItem(id='k1',page=1,type='explanation',title='Step size',content=q,source_quote=q,short_note='Controls update size',block_ids=[block],importance=5,start=0,end=4,segment_ids=[0],confidence=1)
    s=[Segment(start=0,end=4,text=q)]
    a=Alignment(pages=[1],windows=[AlignedWindow(window_id=0,start=0,end=4,text=q,page=1,raw_page=1,confidence=1,similarities=[1])])
    ex=extract_pdf(f/'input.pdf');add_geometry(f/'input.pdf',ex);c=DetailedNotes(items=[item])
    for n,value in [('segments.json',[x.model_dump() for x in s]),('alignment.json',a.model_dump()),('extraction.json',ex.model_dump()),('detailed_notes.json',c.model_dump()),('job.json',{'annotation_mode':'ai_planner'})]:write_json(f/n,value)
    t=AnnotationTools(f,r,[item],s,a,v)
    i=Intent(intent_id='first',knowledge_id='k1',tool='underline',target_ids=[alpha.id],evidence_quote=q,novelty='already_present',reason='Mark update parameter',priority=5)
    return f,r,c,s,a,t,i

class Planner:
    name='fixture';model='mock';last_usage=None
    def __init__(self,intent):self.intent=intent;self.calls=0
    def complete_json(self,prompt):
        self.calls+=1
        return SemanticPlan(intents=[self.intent,self.intent.model_copy(update={'intent_id':'second','tool':'check'})]).model_dump_json()

class Vision:
    def __init__(self,revision=None):self.requests=[];self.revision=revision
    def agent(self,r):
        self.requests.append(r);assert r.image_path.is_file()
        if r.operation=='agent_plan':return {'agent_plan':{'choices':[{'intent_id':i['intent_id'],'tool':i['tool'],'target_ids':i['target_ids'],'confidence':.95} for i in r.payload['intents']]}}
        review={'verdict':'accept','decisions':[]}
        if self.revision and r.payload['actions']:
            e={'action_id':r.payload['actions'][0]['action_id'],'operation':self.revision,'reason':'Fixture edit'}
            if self.revision=='retarget':e['target_ids']=['invented']
            review={'verdict':'revise','decisions':[e]}
        return {'agent_review':review}
    def close(self):pass

def prepare(lesson,mode='ai_planner',vision=None):
    f,r,c,s,a,t,i=lesson;p=Planner(i)
    return agent.prepare_agent(f,c,s,a,mode=mode,llm=p,vlm=vision),p

def test_idempotency_permissions_and_original(lesson):
    f,r,c,s,a,t,i=lesson;before=(f/'input.pdf').read_bytes()
    assert t.execute(i,evidence=evidence_id(f.name,c.items[0]),registry_hash=registry_fingerprint(r))['status']=='applied'
    assert t.execute(i)['status']=='reused' and len(t.state)==1 and t.validate_annotations(1)[0]['valid']
    with pytest.raises(ToolError):t.inspect_region('../../input.pdf')
    with pytest.raises(ToolError):t.export_annotated_pdf('../other')
    with pytest.raises(ToolError):t.render_preview(1)
    assert (f/'input.pdf').read_bytes()==before and t.calls[0]['before']!=t.calls[0]['after']

@pytest.mark.parametrize('change,reason',[
    ({'knowledge_id':'none'},'unknown_knowledge_id'),({'target_ids':['none']},'invalid_element_id'),({'evidence_quote':'fake'},'quote_not_in_evidence'),
    ({'tool':'write_near','text':'Exam question 99','novelty':'additional'},'unsupported_numeric_claim'),
    ({'tool':'write_near','text':'alpha','novelty':'additional'},'slide_restatement'),({'tool':'write_near','text':'step','novelty':'already_present'},'slide_restatement'),
    ({'tool':'connect'},'connect_requires_two_targets'),({'text':'new text'},'mark_must_not_rewrite_text')])
def test_invalid_edits_atomic(lesson,change,reason):
    *_,t,i=lesson;t.execute(i);snapshot=copy.deepcopy(t.state)
    assert t.execute(i.model_copy(update=change))=={'status':'rejected','reason':reason} and t.state==snapshot

def test_hash_segment_and_replace_rollback(lesson):
    f,r,c,s,a,t,i=lesson
    assert t.execute(i,registry_hash='old')['reason']=='registry_hash_mismatch'
    assert t.execute(i,evidence='other')['reason']=='evidence_id_mismatch'
    c.items[0].segment_ids=[99];assert t.execute(i)['status']=='rejected';c.items[0].segment_ids=[0]
    aid=t.execute(i)['action_id'];before=copy.deepcopy(t.state)
    assert t.replace_annotation(aid,i.model_copy(update={'target_ids':['fake']}))['status']=='rejected' and t.state==before
    (f/'input.pdf').write_bytes((f/'input.pdf').read_bytes()+b'\n%new')
    assert t.execute(i)['reason']=='stale_registry'

def test_schema_bounds(lesson):
    i=lesson[-1].model_dump()
    for updates in ({'bbox':[0,0,1,1]},{'tool':'shell'}):
        with pytest.raises(ValidationError):Intent.model_validate({**i,**updates})
    with pytest.raises(ValidationError):Review.model_validate({'verdict':'accept','decisions':[{'action_id':'a','operation':'remove'}]})
    with pytest.raises(ValidationError):SemanticPlan(intents=[lesson[-1],lesson[-1]])

def test_many_actions_pdf_and_provenance(lesson):
    f,r,c,*_=lesson;d,p=prepare(lesson)
    assert d['status']=='prepared' and len(d['pages']['1']['state'])==2
    r=enrich_math_registry(f/'input.pdf',r)[0];states={'1':d['pages']['1']['state']}
    notes,actions=notes_actions(f,c,{1:r},states)
    assert len(actions)==2 and len({x.action_id for x in actions})==2 and {x.knowledge_id for x in actions}=={'k1'}
    ann,out,png=render_state(f,c,{1:r},states,page=1,include_overview=True)
    with pymupdf.open(out) as doc:assert len(doc)==2 and 'LECTURE OVERVIEW' in doc[0].get_text().replace('\xa0',' ') and 'Learning rate' in doc[1].get_text()
    assert png.is_file() and len(ann.pages[0].notes)==2

@pytest.mark.parametrize('source',['input.pdf','alignment.json','segments.json','extraction.json'])
def test_cache_density_and_invalidation(lesson,source):
    f,r,c,*_=lesson;d,p=prepare(lesson)
    for density in ('minimal','balanced','detailed'):
        notes,actions,manifest,report=agent.cached_plan(f,c,extract_pdf(f/'input.pdf'),density)
        assert report['status']=='prepared' and len(actions)==2
    assert p.calls==1 and agent.load_agent(f,c) is not None
    path=f/source;path.write_bytes(path.read_bytes()+b' ');assert agent.load_agent(f,c) is None

def test_knowledge_and_state_integrity(lesson):
    f,r,c,*_=lesson;prepare(lesson);changed=c.model_copy(deep=True);changed.items[0].content+=' revised'
    assert agent.load_agent(f,changed) is None
    path=f/'annotation-agent-ai_planner.json';saved=json.loads(path.read_text());saved['pages']['1']['state'].clear();write_json(path,saved)
    assert agent.load_agent(f,c) is None

def test_visual_image_actual_preview_bounded_remove(lesson):
    v=Vision('remove');d,_=prepare(lesson,'visual_agent',v);page=d['pages']['1']
    assert any(r.operation=='agent_plan' for r in v.requests)
    reviews=[r for r in v.requests if r.operation=='agent_review']
    assert reviews and all(r.image_path.name.startswith('p1-') for r in reviews)
    assert page.get('revision_count',0)<=2 and len(page['state'])<2
    assert any(c['tool']=='remove_annotation' for c in page['tool_calls'])

def test_bad_reviewer_keeps_valid_state(lesson):
    d,_=prepare(lesson,'visual_agent',Vision('retarget'));page=d['pages']['1']
    assert len(page['state'])==2 and any(r['stage']=='review' for r in page.get('fallbacks',[]))

def test_offline_keeps_semantic_state(lesson):
    from app.grounding.ipc import WorkerFailure
    class Offline(Vision):
        def agent(self,r):raise WorkerFailure('worker_timeout')
    d,_=prepare(lesson,'visual_agent',Offline());page=d['pages']['1']
    assert len(page['state'])==2 and any(r['reason']=='worker_timeout' for r in page['fallbacks'])

def test_math_groups_stable_no_prose_join(tmp_path):
    pdf=tmp_path/'native.pdf'
    with pymupdf.open() as doc:
        p=doc.new_page(width=800,height=500);p.insert_text((50,100),'z = 0.7 - 8.4j and -z = -0.7 + 8.4j',fontsize=20);p.insert_text((50,180),'-z = (-1)(a+bj)',fontsize=20);doc.save(pdf)
    r,g=enrich_math_registry(pdf,build_registries(pdf)[0]);again,_=enrich_math_registry(pdf,build_registries(pdf)[0])
    groups=[e for e in r.elements if e.recognition=='native_subexpression']
    assert r==again and any(e.text=='(-1)' for e in groups)
    assert not any('8.4j-z' in e.text or 'and' in e.text for e in groups)
    assert all(0<=e.bbox[0]<e.bbox[2]<=1 and 0<=e.bbox[1]<e.bbox[3]<=1 for e in groups)

def test_production_api_export_density_multiple_chains(lesson,monkeypatch):
    from app.cache import hash_file
    from app.pipeline import run as production
    from app.main import app
    from fastapi.testclient import TestClient
    from app.evidence import EvidenceGraph
    f,r,c,s,a,t,i=lesson
    c.alignment_hash=hash_file(f/'alignment.json');c.extraction_hash=hash_file(f/'extraction.json')
    c.transcript_hash=hash_file(f/'segments.json');c.pdf_hash=hash_file(f/'input.pdf')
    write_json(f/'detailed_notes.json',c.model_dump());monkeypatch.setenv('DATA_DIR',str(f.parents[1]));prepare(lesson)
    with TestClient(app) as client:
        response=client.get(f'/api/jobs/{f.name}/annotations?density=detailed&layout=v2')
        assert response.status_code==200
        ann=response.json();assert ann['agent_report']['mode']=='ai_planner' and len(ann['pages'][0]['notes'])==2
        for n in ann['pages'][0]['notes']:assert n['provenance']['knowledge_id']=='k1' and n['provenance']['source_segment_ids']==[0]
        assert client.get(f'/api/jobs/{f.name}/annotated.pdf?density=detailed&layout=v2').status_code==200
    graph=EvidenceGraph.model_validate_json((f/'evidence_graph-detailed.json').read_text())
    assert len([n for n in graph.nodes if n.kind=='annotation_action'])==2 and graph.metrics['distinct_knowledge_rendered']==1
    monkeypatch.setattr(agent,'prepare_agent',lambda *a,**kw:pytest.fail('unexpected inference'))
    production.export_density(f.name,'minimal',layout='v2',ink_style='clean')


def test_revision_render_failure_rolls_back(lesson,monkeypatch):
    real=agent.render_state;count=0
    def fail_after_preview(*args,**kwargs):
        nonlocal count
        count+=1
        if count>1:raise ValueError('unsafe revised layout')
        return real(*args,**kwargs)
    monkeypatch.setattr(agent,'render_state',fail_after_preview)
    d,_=prepare(lesson,'visual_agent',Vision('remove'));page=d['pages']['1']
    assert len(page['state'])==2 and any(r['stage']=='revision_rollback' for r in page['fallbacks'])


def test_figure_candidates_are_not_semantic_targets(tmp_path):
    from app.grounding.figure_elements import enrich_figure_registry
    pdf=tmp_path/'plot.pdf'
    with pymupdf.open() as drawing:
        p=drawing.new_page(width=300,height=300)
        p.draw_line((25,150),(275,150),color=(.5,.5,.5));p.draw_line((150,25),(150,275),color=(.5,.5,.5))
        p.draw_line((150,150),(95,70),color=(1,0,0),width=1);p.draw_circle((95,70),5,color=(1,0,0),fill=(1,0,0))
        p.draw_line((150,150),(205,230),color=(0,.5,1),width=1);p.draw_circle((205,230),5,color=(0,.5,1),fill=(0,.5,1))
        pix=p.get_pixmap(matrix=pymupdf.Matrix(2,2))
        with pymupdf.open() as doc:
            page=doc.new_page(width=500,height=500);page.insert_image(pymupdf.Rect(100,100,400,400),pixmap=pix);doc.save(pdf)
    r,g=enrich_figure_registry(pdf,build_registries(pdf)[0]);parts=[e for e in r.elements if e.kind=='figure_part']
    assert len(parts)==3 and not any(n['semantic_verified'] for n in g['nodes'])
    again,_=enrich_figure_registry(pdf,build_registries(pdf)[0]);assert again==r
    assert all(e.parent_ids and e.recognition.startswith('pixel_geometry_candidate') for e in parts)
