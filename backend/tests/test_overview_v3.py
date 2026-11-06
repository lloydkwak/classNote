"""Source validation, synthesis/review separation, readable PDF and old index contract."""
import json
import pymupdf
import pytest
from app.pipeline import overview_synthesis as synth
from app.pipeline.overview import attach_overview,draw_overview,page_mapping
from app.models import Annotations
from test_annotation_agent_v3 import lesson


def guide():
    point={'text':'alpha controls the update size','citations':[{'source_id':'k1','quote':'alpha controls the update size'}]}
    return {'title':'Learning rate','introduction':point,'objectives':[point],
        'concepts':[{**point,'title':'Step size'}],'formulas':[],'examples':[],'connections':[],'review_points':[point]}

class SynthesisProvider:
    name='fixture';model='study-mock';last_usage=None
    def __init__(self,supported=True):self.calls=0;self.supported=supported
    def complete_json(self,prompt):
        self.calls+=1
        if prompt.startswith('Independently audit'):
            keys=['introduction.0','objectives.0','concepts.0','review_points.0']
            return json.dumps({'title_supported':True,'checks':[{'path':k,'supported':self.supported,'reason':'fixture review'} for k in keys]})
        return json.dumps(guide())


def test_whole_synthesis_has_separate_support_review_and_cache(lesson,monkeypatch):
    f,r,c,s,*_=lesson;p=SynthesisProvider()
    result=synth.prepare_synthesis(f,c,s,[r],p)
    assert result['status']=='validated' and p.calls==2
    assert result['support_reviews'][0]['checks']
    assert synth.prepare_synthesis(f,c,s,[r],p)['cache_hit'] and p.calls==2
    monkeypatch.setattr('app.llm.get_provider',lambda:p)
    assert synth.load_synthesis(f,c,s)['study']['title']=='Learning rate'
    (f/'alignment.json').write_bytes((f/'alignment.json').read_bytes()+b' ')
    assert synth.load_synthesis(f,c,s) is None and p.calls==2


def test_unsupported_synthesis_not_promoted(lesson):
    f,r,c,s,*_=lesson;p=SynthesisProvider(False)
    result=synth.prepare_synthesis(f,c,s,[r],p)
    assert result['status']=='fallback' and p.calls==6 and 'unsupported_study_claims' in result['reason']


@pytest.mark.parametrize('field,value,reason',[
    ('quote','not said','overview_quote_not_found'),('source_id','invented','unknown_overview_source'),
    ('text','alpha produces 72 updates','overview_unsupported_number'),
    ('text','Validated knowledge count','overview_diagnostic_leak'),
    ('text','It is useful in linear algebra','overview_unsupported_domain'),
    ('text','alpha = beta','equation_requires_formula_field')])
def test_overview_rejects_unfounded_content(lesson,field,value,reason):
    f,r,c,s,*_=lesson;data=guide()
    if field in {'source_id','quote'}:data['introduction']['citations'][0][field]=value
    else:data['introduction'][field]=value
    with pytest.raises(ValueError,match=reason):synth.validate_guide(data,synth.source_bank(c,s),[])


def test_exact_formula_signs_required(lesson):
    f,r,c,s,*_=lesson;data=guide()
    data['formulas']=[{'expression':'z = -alpha','meaning':'definition','citations':data['introduction']['citations']}]
    with pytest.raises(ValueError,match='formula_not_in_source'):synth.validate_guide(data,synth.source_bank(c,s),['z = alpha'])


def test_study_pdf_no_diagnostics_and_source_mapping(lesson):
    f,r,c,s,*_=lesson
    ann=Annotations(job_id=f.name,pages=[])
    attach_overview(f,ann,c);ann.overview['study']=guide()
    before=(f/'input.pdf').read_bytes()
    with pymupdf.open() as doc:
        draw_overview(doc,ann.overview)
        text=doc[0].get_text().replace('\xa0',' ')
        assert 'CORE CONCEPTS' in text and 'Step size' in text
        for word in ['Source slides','Validated knowledge','confidence','timestamp','registry','alignment']:assert word not in text
        assert ann.overview['study_presentation']['font_size']>=10.5
        assert ann.overview['study_presentation']['bottom']<825
    assert ann.page_mapping==page_mapping(1) and ann.page_mapping[1]['source_slide_index']==0 and ann.page_mapping[1]['export_page_index']==1
    assert (f/'input.pdf').read_bytes()==before


def test_continuous_speech_citation_keeps_source_ids(lesson):
    f,r,c,s,*_=lesson
    from app.models import Segment
    s=[Segment(start=0,end=2,text='Consequently, in any'),Segment(start=2,end=4,text='field this property holds.')]
    bank=synth.source_bank(c,s)
    assert bank['speech_0_1']['text']=='Consequently, in any field this property holds.'
    assert bank['speech_0_1']['segment_ids']==[0,1]
