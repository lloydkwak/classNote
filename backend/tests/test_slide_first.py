"""Slide-first production contracts. Model proposals here are deterministic mocks."""
import json
from types import SimpleNamespace

import pymupdf
import pytest

from app.models import Alignment,AlignedWindow,Annotations,Block,Page,Segment
from app.pipeline import run as pipeline
from app.pipeline.block_align import has_unsupported_number
from app.pipeline.grounded_notes import choose_action
from app.pipeline.layout import LayoutNote,text_runs
from app.pipeline.layout_v2 import layout_page_v2,make_mark
from app.pipeline.overview import build_overview,page_mapping
from app.pipeline.rich_notes import ContentItem,DetailedNotes
from app.grounding.document import build_registries
from app.grounding.selection import spoken_math_contains
from test_evidence_pipeline import grounded_job


@pytest.mark.parametrize('kind,semantic,importance,expected',[
    ('word','definition',5,'highlight'),('word','definition',3,'underline'),
    ('symbol','explanation',5,'circle'),('formula','worked_example',4,'check'),
    ('formula','derivation',5,'bracket'),('formula','reasoning',4,'arrow'),
    ('formula','explanation',4,'box'),('word','warning',4,'highlight'),
    ('figure','worked_example',5,'none')])
def test_semantic_action_policy(kind,semantic,importance,expected):
    mark,reason=choose_action(SimpleNamespace(type=semantic,importance=importance),SimpleNamespace(kind=kind))
    assert mark==expected and reason


@pytest.mark.parametrize('mark',['underline','highlight','box','circle','arrow','bracket','check'])
def test_actual_visual_actions_export_and_keep_native_geometry(mark,tmp_path):
    from app.models import Annotation,PageAnnotations
    from app.pipeline.export_pdf import export_annotated_pdf
    source=tmp_path/'source.pdf'
    with pymupdf.open() as doc:
        p=doc.new_page(width=800,height=500);p.insert_text((300,220),'alpha',fontsize=16);doc.save(source)
    registry=build_registries(source)[0]
    target=next(e for e in registry.elements if e.text=='alpha')
    native=Block(block_id=target.id,page=1,text=target.text,bbox=target.bbox)
    page=Page(page=1,title='Math',width=800,height=500,blocks=[native])
    layout=layout_page_v2([LayoutNote('action-'+mark,'Source-backed role',False,[native],mark,'word')],page)
    sketch=layout.sketches[0]
    assert sketch.mark==mark and sketch.strokes
    assert sketch.placement['target_bbox']==pytest.approx([v*s for v,s in zip(target.bbox,[800,500,800,500])])
    assert layout.metrics['original_text_occlusion_area']==0
    assert layout.metrics['ink_text_interference_count']==0
    annotation=Annotation(id='a',block_ids=[target.id],bboxes=[target.bbox],type='explanation',note='Source-backed role',quote='alpha',language='en',start=0,end=3,sketch=sketch)
    result=Annotations(job_id='j',pages=[PageAnnotations(page=1,width=800,height=500,gutter=layout.gutter,output_height=layout.output_height,notes=[annotation])],layout_engine='v2')
    output=tmp_path/'after.pdf';export_annotated_pdf(source,result,output)
    with pymupdf.open(output) as doc:
        assert len(doc)==2
        assert 'alpha' in doc[1].get_text()
        ink=list(doc[1].annots());assert ink
        if mark=='highlight':assert ink[0].opacity==pytest.approx(.24)


def test_slide_candidates_win_before_margin_and_dark_background_contrast():
    target=Block(block_id='target',page=1,text='alpha',bbox=(.4,.4,.48,.44))
    page=Page(page=1,title='Math',width=800,height=500,blocks=[target],background_luma=[[.05]*16 for _ in range(16)])
    result=layout_page_v2([LayoutNote('note','Alpha role',False,[target],'underline')],page)
    assert result.gutter==0 and result.sketches[0].placement['fallback_mode'] is None
    assert result.sketches[0].text_color==[1.,.88,.42]
    assert result.metrics['original_slide_annotation_count']==1
    # Entire original slide occupied: original text wins the hard exclusion.
    page.blocks=[Block(block_id='full',page=1,text='dense slide',bbox=(0,0,1,1))]
    result=layout_page_v2([LayoutNote('note','Alpha role',False,[target],'underline')],page)
    assert result.gutter>0 and result.sketches[0].placement['fallback_mode']=='expanded_margin'
    assert result.metrics['original_text_occlusion_area']==0


def test_symbol_font_decode_is_font_specific_and_keeps_raw_native_data(tmp_path):
    source=tmp_path/'symbol.pdf'
    with pymupdf.open() as doc:
        page=doc.new_page();page.insert_text((50,70),'a+b',fontname='Symbol')
        page.insert_text((50,140),'a+b',fontname='helv');doc.save(source)
    before=source.read_bytes();registry=build_registries(source)[0]
    mapped=[e for e in registry.elements if e.raw_pdf_text=='a+b' and e.kind=='word']
    assert mapped and mapped[0].text=='α+β' and mapped[0].font_encoding=='Adobe-Symbol'
    assert any(e.text=='a+b' and e.raw_pdf_text is None for e in registry.elements)
    assert {'α','β'}<={e.text for e in registry.elements if e.kind=='symbol'}
    assert source.read_bytes()==before


def test_explicit_spoken_math_does_not_authorize_case_changes_or_quantity_components():
    assert spoken_math_contains('α','negative alpha plus beta')
    assert not spoken_math_contains('α','alphabet')
    assert not spoken_math_contains('θ','Θ is different')
    assert not has_unsupported_number('equals 0 and 1','equals zero and one')
    for quote in ['one hundred','one thousand','one point five','point zero five']:
        assert has_unsupported_number('equals 1 and 0',quote)
    assert has_unsupported_number('equals 2','equals one')


def evidence_fixture(tmp_path):
    path=tmp_path/'source.pdf'
    with pymupdf.open() as doc:
        for i in range(3):doc.new_page().insert_text((40,60),f'Original slide {i+1}')
        doc.save(path)
    item=ContentItem(id='k',page=2,start=0,end=4,segment_ids=[0],confidence=1,type='definition',title='Inverse',content='An inverse sums to zero.',source_quote='An inverse sums to zero.',short_note='Sum = 0',importance=5)
    content=DetailedNotes(items=[item])
    segments=[Segment(start=0,end=4,text=item.source_quote)]
    alignment=Alignment(pages=[2],windows=[AlignedWindow(window_id=0,start=0,end=4,text=item.source_quote,page=2,raw_page=2,confidence=1,similarities=[])])
    return path,content,segments,alignment


def test_overview_validates_sources_and_reports_uncertain_audio(tmp_path):
    path,content,segments,alignment=evidence_fixture(tmp_path)
    overview,mapping=build_overview(path,Annotations(job_id='j',pages=[]),content,segments,alignment)
    assert overview['validated_knowledge_ids']==['k'] and overview['entries'][0]['audio_verified']
    assert mapping[0]['source_slide_index'] is None and mapping[2]['export_page_index']==2
    alignment.windows[0].confidence=.1
    assert not build_overview(path,Annotations(job_id='j',pages=[]),content,segments,alignment)[0]['entries'][0]['audio_verified']
    content.items[0].segment_ids=[9]
    invalid=build_overview(path,Annotations(job_id='j',pages=[]),content,segments,alignment)[0]
    assert invalid['status']=='insufficient_evidence' and invalid['rejected'][0]['reason']=='invalid_segment_reference'


def test_overview_export_is_idempotent_and_preserves_all_source_pages(tmp_path):
    from app.pipeline.export_pdf import export_annotated_pdf
    path,content,segments,alignment=evidence_fixture(tmp_path)
    annotations=Annotations(job_id='j',pages=[],layout_engine='v2')
    annotations.overview,annotations.page_mapping=build_overview(path,annotations,content,segments,alignment)
    for name in ('first.pdf','again.pdf'):
        export_annotated_pdf(path,annotations,tmp_path/name)
        with pymupdf.open(tmp_path/name) as doc:
            assert len(doc)==4 and 'LECTURE OVERVIEW' in ' '.join(doc[0].get_text().split())
            assert [f'Original slide {i}' in doc[i].get_text() for i in range(1,4)]==[True]*3
            assert json.loads(doc.embfile_get('classnote-page-map.json'))['pages']==page_mapping(3)
            links=doc[0].get_links();assert links and links[0]['page']==2


def test_density_keeps_overview_knowledge_and_source_audio_without_vlm(grounded_job):
    job,folder,provider=grounded_job;pipeline.run_pipeline(job)
    calls=provider.calls;raw=(folder/'page_audio.json').read_bytes() if (folder/'page_audio.json').exists() else None
    first=pipeline.density_annotations(job,'minimal','v2')
    second=pipeline.density_annotations(job,'detailed','v2')
    assert first.overview==second.overview
    assert first.page_mapping==second.page_mapping
    assert len(first.overview['validated_knowledge_ids'])==len(pipeline.read_detailed_notes(job).items)
    assert calls==provider.calls
    if raw is not None:assert raw==(folder/'page_audio.json').read_bytes()
    assert all(n.start==n.provenance.timestamp_range[0] for p in second.pages for n in p.notes)
    assert all(row['visual_action_count']>=0 for row in second.loss_audit)


def test_common_words_do_not_ground_an_unrelated_formula():
    from app.grounding.schema import Element
    from app.grounding.selection import formula_supported
    formula=Element(id='f',page=1,kind='formula',text='Given z, we found -z so that z + (-z) = 0',bbox=(.1,.1,.8,.2),source='pdf')
    children=[Element(id=f'w{i}',page=1,kind='word',text=t,bbox=(.1,.1,.2,.2),source='pdf',parent_ids=['f']) for i,t in enumerate(['Given','z,','we','found','so','that','0'])]
    unrelated=SimpleNamespace(source_quote='We also saw multiplying by negative one, that is the additive inverse of one.')
    assert not formula_supported(formula,[formula,*children],unrelated)


def test_highlight_round_caps_do_not_touch_adjacent_words():
    from app.models import Word
    page=Page(page=1,title='Math',width=800,height=500,blocks=[],words=[
        Word(word_id='l',line_id='line',text='additive',bbox=(.2,.4,.29,.45)),
        Word(word_id='t',line_id='line',text='inverse',bbox=(.296,.4,.38,.45)),
        Word(word_id='r',line_id='line',text='is',bbox=(.386,.4,.405,.45))])
    target=Block(block_id='t',page=1,text='inverse',bbox=page.words[1].bbox)
    result=layout_page_v2([LayoutNote('h','Meaning',False,[target],'highlight','word')],page)
    assert result.sketches[0].mark=='highlight'
    assert result.metrics['ink_text_interference_count']==0
    assert result.metrics['intentional_translucent_highlight_contacts']>0


def test_focus_excerpt_is_verbatim_and_does_not_replace_original_evidence(tmp_path):
    from app.grounding.schema import GroundingItem,TargetRequest
    from app.grounding.document import render_page
    from app.pipeline.grounded_notes import local_request
    path=tmp_path/'source.pdf'
    with pymupdf.open() as doc:
        page=doc.new_page();page.insert_text((40,60),'First x = 1');page.insert_text((40,130),'Finally x = 0');doc.save(path)
    registry=build_registries(path)[0]
    _,image,viewport=render_page(path,registry,tmp_path/'images')
    quote='First x equals one. Finally x equals 0.'
    item=ContentItem(id='k',page=1,start=0,end=4,segment_ids=[0],confidence=1,type='derivation',title='Proof',content='Derive x = 0',source_quote=quote,short_note='x = 0',block_ids=[e.id for e in registry.elements if e.kind=='block'])
    segments=[Segment(start=0,end=4,text=quote)]
    alignment=Alignment(pages=[1],windows=[AlignedWindow(window_id=0,start=0,end=4,text=quote,page=1,raw_page=1,confidence=1,similarities=[])])
    request=local_request(item,registry,viewport,image,segments,alignment)
    assert request.items[0].selection_quote=='Finally x equals 0.'
    assert request.items[0].source_quote==quote and item.source_quote==quote
    assert request.items[0].segment_ids==[0] and request.items[0].start==0
    from app.grounding.selection import speech_error
    invalid=request.items[0].model_copy(update={'selection_quote':'An invented conclusion'})
    assert speech_error(invalid,segments)=='focus_quote_not_in_source'


def test_unverified_figure_labels_remain_in_details_instead_of_printing_a_formula(grounded_job):
    job,folder,_=grounded_job;pipeline.run_pipeline(job)
    content=pipeline.read_detailed_notes(job)
    result=pipeline.density_annotations(job,'balanced','v2')
    figure=next(n for p in result.pages for n in p.notes if n.provenance.target_kind=='figure')
    assert figure.note=='Figure explanation → Detailed Notes'
    assert not figure.sketch.strokes and not figure.sketch.arrow
    item=next(i for i in content.items if i.id==figure.content_id)
    assert item.short_note=='The Loss curve' and item.content and figure.quote==item.source_quote
    action=next(a for a in json.loads((folder/'annotation_actions.json').read_text()) if a['knowledge_id']==item.id)
    assert action['annotation_text']==figure.note and action['selection_reason']=='figure_block_without_verified_subtarget'


def test_long_lecture_overview_is_one_page_with_explicit_compression(tmp_path):
    from app.pipeline.overview import draw_overview
    path,content,segments,alignment=evidence_fixture(tmp_path)
    with pymupdf.open(path) as doc:
        for _ in range(4):doc.new_page().insert_text((40,60),'Additional source slide')
        doc.save(tmp_path/'long.pdf')
    base=content.items[0]
    content.items=[base.model_copy(update={'id':f'k{i}','page':i+1,'title':'A detailed explanation of the additive inverse '*10,'short_note':'An inverse sums to zero. '*100}) for i in range(7)]
    overview,_=build_overview(tmp_path/'long.pdf',Annotations(job_id='j',pages=[]),content,segments,alignment)
    assert len(overview['entries'])==7
    assert len(overview['presentation']['compressed_knowledge_ids'])==7
    assert len(overview['records'])==7 and overview['detailed_notes_preserved']
    with pymupdf.open() as doc:
        page=draw_overview(doc,overview)
        assert len(doc)==1
        # v3 replaces page-number inventories with a study page. Compression
        # remains explicit internally; no processing counts leak into the PDF.
        assert overview['presentation']['compressed_knowledge_ids']
        assert 'Validated knowledge' not in page.get_text().replace('\xa0',' ')
        assert all(pymupdf.Rect(word[:4]).y1<=842 for word in page.get_text('words'))


def test_layout_protects_native_math_even_when_semantic_extractor_omits_it(tmp_path):
    from app.models import PdfExtraction,Sketch
    from app.pipeline.layout_v2 import enrich_geometry,evaluate
    path=tmp_path/'source.pdf'
    with pymupdf.open() as doc:
        page=doc.new_page(width=800,height=500)
        page.insert_text((300,440),'= 0 + 0 = 0',fontsize=20)
        doc.save(path)
    # Deliberately no semantic blocks/words: a filtered mathematical fragment still
    # occupies the PDF and must be protected without inventing a canonical target.
    extraction=PdfExtraction(pages=[Page(page=1,title='Math',width=800,height=500,blocks=[])],key_terms=[])
    enrich_geometry(path,extraction);page=extraction.pages[0]
    assert page.protected_text_bboxes
    colliding=Sketch(mark='none',strokes=[],arrow=[],side='right',x=290,y=415,width=180,height=50,font_size=16,line_height=20,lines=['A note'])
    assert evaluate([colliding],page)['original_text_occlusion_area']>0
    layout=layout_page_v2([LayoutNote('safe','A note',False,[])],page)
    assert layout.metrics['original_text_occlusion_area']==0
    assert layout.metrics['out_of_page_count']==0


def test_pdf_glyph_bounds_fit_mixed_script_note_box(tmp_path):
    from app.models import Annotation,PageAnnotations
    from app.pipeline.export_pdf import export_annotated_pdf
    source=tmp_path/'source.pdf'
    with pymupdf.open() as doc:
        page=doc.new_page(width=800,height=500);page.insert_text((350,240),'native',fontsize=20);doc.save(source)
    page=Page(page=1,title='Math',width=800,height=500,blocks=[])
    text='정의 α + β = 0. Mixed script calculation (-1)z → -z. '*3
    result=layout_page_v2([LayoutNote('math-note',text,False,[])],page,'nanum-pen')
    sketch=result.sketches[0]
    annotation=Annotation(id='a',block_ids=[],bboxes=[],type='explanation',note=text,quote=text,language='ko',start=0,end=3,sketch=sketch)
    output=tmp_path/'after.pdf'
    export_annotated_pdf(source,Annotations(job_id='j',note_font='nanum-pen',layout_engine='v2',pages=[PageAnnotations(page=1,width=800,height=500,gutter=result.gutter,output_height=result.output_height,notes=[annotation])]),output)
    with pymupdf.open(output) as doc:
        note_box=pymupdf.Rect(sketch.x+result.gutter,sketch.y,sketch.x+result.gutter+sketch.width,sketch.y+sketch.height)+(-.1,-.1,.1,.1)
        words=[w for w in doc[1].get_text('words') if w[4]!='native']
        assert words and all(note_box.contains(pymupdf.Rect(w[:4])) for w in words)
