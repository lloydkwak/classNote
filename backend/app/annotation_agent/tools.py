"""Capability-scoped PDF tools. Models never get filesystem paths or generated geometry writes."""
import copy
import json
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

import pymupdf

from app.cache import hash_file
from app.evidence import fingerprint,stable_id
from app.grounding.document import registry_fingerprint,render_page
from app.grounding.schema import GroundingItem,TargetRequest,TargetProposal,TargetSelections
from app.grounding.selection import validate_selection,speech_error,SPOKEN_GREEK
from app.pipeline.block_align import has_unsupported_number
from app.annotation_agent.schema import Intent

POLICY='annotation-tools-v3-1'
WRITE_TOOLS={'highlight','underline','circle','box','connect','write_near','bracket','check'}


class ToolError(ValueError):
    pass


def math_normalize(text):
    text=unicodedata.normalize('NFKC',text).replace('−','-').replace('–','-')
    for symbol,name in SPOKEN_GREEK.items():text=re.sub(r'\b'+name+r'\b',symbol,text,flags=re.I)
    for word,sign in [('negative','-'),('minus','-'),('plus','+'),('zero','0'),('one','1'),('equals','=')]:
        text=re.sub(r'\b'+word+r'\b',sign,text,flags=re.I)
    text=re.sub(r'\bis equal to\b','=',text,flags=re.I)
    return re.sub(r'\s+','',text)


def native_text_rows(pdf,page_number):
    rows=[]
    with pymupdf.open(pdf) as doc:
        page=doc[page_number-1];page.set_rotation(0)
        for block in page.get_text('rawdict')['blocks']:
            for line in block.get('lines',[]):
                for span in line['spans']:
                    for char in span['chars']:
                        if char['c'].isspace():continue
                        x,y=char['origin'];size=span['size']
                        row=next((r for r in rows if abs(r[0]-y)<size*.23),None)
                        if row is None:rows.append([y,[(x,char['c'])]])
                        else:row[1].append((x,char['c']))
    return [''.join(c for x,c in sorted(row[1])) for row in sorted(rows)]


def evidence_id(job,item):return stable_id('knowledge',job,item.model_dump(mode='json'))


class AnnotationTools:
    def __init__(self,folder,registry,items,segments,alignment,viewport,*,store=None,renderer=None):
        self.folder=Path(folder).resolve(strict=True)
        self.pdf=self.folder/'input.pdf'
        self.registry=registry
        if hash_file(self.pdf)!=registry.pdf_sha256:raise ToolError('pdf_hash_mismatch')
        self.registry_hash=registry_fingerprint(registry)
        self.items={i.id:i for i in items};self.elements={e.id:e for e in registry.elements}
        self.segments=segments;self.alignment=alignment;self.viewport=viewport
        self.state=copy.deepcopy(store or {})
        self.calls=[]
        self._renderer=renderer
        self.native_rows=native_text_rows(self.pdf,self.registry.page)

    def _current(self):
        if hash_file(self.pdf)!=self.registry.pdf_sha256 or registry_fingerprint(self.registry)!=self.registry_hash:
            raise ToolError('stale_registry')

    def inspect_page(self,page_id):
        if page_id!=self.registry.page:raise ToolError('wrong_page')
        self._current()
        original,_,viewport=render_page(self.pdf,self.registry,self.folder/'agent-previews'/f'p{page_id}')
        return {'page':page_id,'image':original.name,'registry_fingerprint':self.registry_hash,
                'elements':self.get_element_registry(page_id),'viewport':viewport.model_dump()}

    def get_element_registry(self,page_id):
        self._current()
        if page_id!=self.registry.page:raise ToolError('wrong_page')
        return [e.model_dump() for e in self.registry.elements]

    def inspect_region(self,element_id):
        self._current()
        element=self.elements.get(element_id)
        if element is None:raise ToolError('invalid_element_id')
        original,_,viewport=render_page(self.pdf,self.registry,self.folder/'agent-previews'/stable_id('region',element_id),crop=element.bbox,zoom=3)
        return {'element_id':element_id,'image':original.name,'viewport':viewport.model_dump()}

    def get_evidence(self,knowledge_id):
        if knowledge_id not in self.items:raise ToolError('unknown_knowledge_id')
        item=self.items[knowledge_id]
        return {'evidence_id':evidence_id(self.folder.name,item),**item.model_dump()}

    def get_annotation_state(self,page_id):
        if page_id!=self.registry.page:raise ToolError('wrong_page')
        return copy.deepcopy(list(self.state.values()))

    def _qualified_native_math(self,item,target,quote):
        if target.recognition not in {'native_math_glyph','native_subexpression'}:return None
        normalized=math_normalize(quote)
        content_math=math_normalize(item.content).replace('*','')
        factoring=bool(re.search(r'factor|공통.?인수|인수분해',quote,re.I))
        def occurrence(element):
            glyphs=[e for e in self.registry.elements if e.recognition=='native_math_glyph' and element.id in e.parent_ids and e.text not in {'(',')'}]
            if not glyphs:return tuple(round(v,5) for v in element.bbox)
            box=pymupdf.Rect(glyphs[0].bbox)
            for glyph in glyphs[1:]:box|=pymupdf.Rect(glyph.bbox)
            return tuple(round(v,5) for v in box)
        scopes=[e for e in self.registry.elements if e.recognition=='native_subexpression'
                and (e.id==target.id or e.id in target.parent_ids)]
        for scope in scopes:
            token=math_normalize(scope.text)
            if token.startswith('(') and token.endswith(')') and not re.search(r'[+=]',token):token=token[1:-1]
            structural=factoring and '_product_' in scope.id and token in content_math
            if len(token)<2 or (token not in normalized and not structural):continue
            positions={occurrence(e) for e in self.registry.elements
                       if e.recognition=='native_subexpression' and math_normalize(e.text).strip('()')==token
                       and (not item.block_ids or set(e.parent_ids)&set(item.block_ids))}
            children=[e for e in self.registry.elements if scope.id in e.parent_ids and e.recognition=='native_math_glyph' and e.text==target.text]
            subgroups=[e for e in self.registry.elements if e.recognition=='native_subexpression' and scope.id in e.parent_ids and e.text==target.text]
            subgroup_positions={occurrence(e) for e in subgroups}
            if len(positions)==1 and (target.id==scope.id or len(children)==1 or (structural and len(subgroup_positions)==1)):return scope.id
        return None

    def _relation_context(self,intent,item):
        # A relation explicitly compares TWO native occurrences. This is different
        # from silently guessing which occurrence a standalone symbol means.
        if intent.tool not in {'connect','bracket'} or len(intent.target_ids)!=2:return None
        first,last=(self.elements.get(t) for t in intent.target_ids)
        if first is None or last is None:return None
        if any(e.recognition!='native_subexpression' for e in (first,last)):return None
        left=math_normalize(first.text).strip('()');right=math_normalize(last.text).strip('()')
        if left!=right:return None
        utterance=math_normalize(intent.evidence_quote)
        source=math_normalize(item.content).replace('*','')
        parents=set(first.parent_ids)&set(last.parent_ids)&set(item.block_ids)
        factored=bool(re.search(r'common factor|공통.?인수',intent.evidence_quote,re.I))
        component=bool(re.search(r'real|imaginary|component|실수|허수|성분',item.source_quote,re.I))
        if not ((left in utterance and len(left)>1) or (factored and left in source)):return None
        if not (factored or component):return None
        for parent in parents:
            context=self.elements.get(parent)
            if context is None:continue
            if math_normalize(context.text).replace('*','') not in source:continue
            peers={tuple(round(v,5) for v in e.bbox) for e in self.registry.elements
                   if e.recognition=='native_subexpression' and parent in e.parent_ids
                   and math_normalize(e.text).strip('()')==left}
            if peers=={tuple(round(v,5) for v in e.bbox) for e in (first,last)}:return parent
        return None

    def validate(self,intent,*,evidence=None,visual=False):
        self._current()
        intent=Intent.model_validate(intent)
        item=self.items.get(intent.knowledge_id)
        if item is None:raise ToolError('unknown_knowledge_id')
        if item.page!=self.registry.page:raise ToolError('knowledge_page_mismatch')
        if evidence is not None and evidence!=evidence_id(self.folder.name,item):raise ToolError('evidence_id_mismatch')
        if intent.evidence_quote not in item.source_quote:raise ToolError('quote_not_in_evidence')
        grounding=GroundingItem.from_content_item(item)
        if error:=speech_error(grounding,self.segments):raise ToolError(error)
        windows=[w for w in self.alignment.windows if w.page==item.page and w.start<item.end and w.end>item.start]
        if not windows:raise ToolError('alignment_does_not_support_page')
        if intent.tool=='skip':return intent,[]
        if intent.tool not in WRITE_TOOLS:raise ToolError('unsupported_tool')
        if not intent.target_ids:raise ToolError('missing_target')
        if len(intent.target_ids)!=len(set(intent.target_ids)):raise ToolError('duplicate_target')
        if intent.tool=='connect' and len(intent.target_ids)!=2:raise ToolError('connect_requires_two_targets')
        if intent.tool not in {'connect','bracket'} and len(intent.target_ids)!=1:raise ToolError('single_target_required')
        if has_unsupported_number(intent.text,intent.evidence_quote):raise ToolError('unsupported_numeric_claim')
        if intent.tool=='write_near':
            if re.search(r'\bexam|시험|출제',intent.text,re.I) and not re.search(r'\bexam|시험|출제',intent.evidence_quote,re.I):raise ToolError('unsupported_exam_claim')
            if '=' in intent.text and math_normalize(intent.text) not in math_normalize(item.content):raise ToolError('unsupported_note_formula')
            if not intent.text.strip():raise ToolError('empty_note')
            if intent.novelty!='additional':raise ToolError('slide_restatement')
            slide=' '.join(self.native_rows)+' '+' '.join(e.text for e in self.registry.elements if e.kind in {'line','formula'})
            words=re.findall(r'\w+',intent.text.casefold())
            if (math_normalize(intent.text) in math_normalize(slide) or
                any(SequenceMatcher(None,intent.text.casefold(),e.text.casefold()).ratio()>.78 for e in self.registry.elements if e.kind in {'line','formula'}) or
                (len(words)>5 and sum(w in slide.casefold().split() for w in words)/len(words)>.88)):
                raise ToolError('slide_restatement')
        elif intent.text:raise ToolError('mark_must_not_rewrite_text')
        validated=[]
        relation_context=self._relation_context(intent,item)
        request=TargetRequest(image_path=self.folder/'input.pdf',registry=self.registry,viewport=self.viewport,
                              items=[grounding],segments=self.segments)
        for identity in intent.target_ids:
            element=self.elements.get(identity)
            if element is None:raise ToolError('invalid_element_id')
            if element.source!='pdf' and element.kind=='symbol':raise ToolError('ocr_symbol_geometry_unverified')
            if element.kind=='figure' and intent.tool!='write_near':raise ToolError('figure_detail_unverified')
            if element.kind=='block':raise ToolError('coarse_block_target')
            if intent.tool in {'highlight','circle','box'} and (element.bbox[2]-element.bbox[0]>.55 or (element.bbox[2]-element.bbox[0])*(element.bbox[3]-element.bbox[1])>.09):raise ToolError('target_too_coarse')
            if has_unsupported_number(element.text,intent.evidence_quote):raise ToolError('unsupported_numeric_target')
            if element.kind=='figure_part':
                if not visual:raise ToolError('figure_semantics_require_visual_confirmation')
                if not element.recognition.startswith('pixel_geometry_candidate_') or not set(element.parent_ids)&set(item.block_ids):raise ToolError('figure_part_without_source_association')
                if intent.tool not in {'circle','box','highlight','connect','check','underline'}:raise ToolError('unsupported_figure_detail_action')
                validated.append({'element_id':identity,'bbox':list(element.bbox),'checks':['native_pdf_pixel_geometry','existing_figure_block_association','vlm_visual_confirmation'],'qualified_context':element.parent_ids[0]})
                continue
            proposal=TargetProposal(knowledge_id=item.id,page=item.page,status='selected',target_element_id=identity,
                                    target_text=element.text,confidence=.99)
            decision=validate_selection(request,TargetSelections(selections=[proposal]))['results'][0]
            qualifier=self._qualified_native_math(item,element,intent.evidence_quote) or relation_context
            if decision['status']!='accepted' and qualifier is None:raise ToolError(decision['reason'])
            if element.recognition=='native_subexpression' and qualifier is None:
                # Formula-level lexical support alone cannot disambiguate repeated factors.
                positions={tuple(round(v,5) for v in e.bbox) for e in self.registry.elements
                           if e.recognition=='native_subexpression' and e.text==element.text}
                if len(positions)>1:raise ToolError('ambiguous_repeated_subexpression')
            validated.append({'element_id':identity,'bbox':list(element.bbox),
                              'checks':decision['checks'] if decision['status']=='accepted' else ['qualified_native_math_context'],
                              'qualified_context':qualifier})
        return intent,validated

    def execute(self,intent,*,evidence=None,registry_hash=None,visual=False):
        before=fingerprint(self.state)
        call={'tool':getattr(intent,'tool',None),'knowledge_id':getattr(intent,'knowledge_id',None),'intent_id':getattr(intent,'intent_id',None),'before':before,'registry_fingerprint':self.registry_hash}
        try:
            if registry_hash is not None and registry_hash!=self.registry_hash:raise ToolError('registry_hash_mismatch')
            intent,validated=self.validate(intent,evidence=evidence,visual=visual)
            if intent.tool=='skip':
                result={'status':'skipped','reason':intent.reason,'knowledge_id':intent.knowledge_id}
            else:
                item=self.items[intent.knowledge_id]
                signature={'knowledge_id':item.id,'tool':intent.tool,'targets':[(self.elements[t].text,tuple(round(v,6) for v in self.elements[t].bbox)) for t in intent.target_ids],'text':intent.text,
                           'registry':self.registry_hash,'policy':POLICY,'placement':intent.placement}
                action_id=stable_id('agent_action',self.folder.name,signature)
                row={'action_id':action_id,'intent':intent.model_dump(),'targets':validated,
                     'evidence_id':evidence_id(self.folder.name,item),'registry_fingerprint':self.registry_hash,
                     'source_segment_ids':item.segment_ids,'timestamp_range':[item.start,item.end],
                     'visual_confirmed':visual,'revision':0}
                existing=action_id in self.state
                self.state.setdefault(action_id,row)
                if visual:self.state[action_id]['visual_confirmed']=True
                result={'status':'reused' if existing else 'applied','action_id':action_id}
            call.update(ok=True,result=result)
        except (ToolError,ValueError) as error:
            call.update(ok=False,error=str(error));result={'status':'rejected','reason':str(error)}
        call['after']=fingerprint(self.state);self.calls.append(call)
        return result

    def remove_annotation(self,annotation_id):
        self._current()
        before=fingerprint(self.state)
        if annotation_id not in self.state:raise ToolError('unknown_action_id')
        old=self.state.pop(annotation_id)
        self.calls.append({'tool':'remove_annotation','action_id':annotation_id,'ok':True,'before':before,'after':fingerprint(self.state)})
        return old

    def replace_annotation(self,annotation_id,new_action,*,visual=False):
        if annotation_id not in self.state:raise ToolError('unknown_action_id')
        if Intent.model_validate(new_action).knowledge_id!=self.state[annotation_id]['intent']['knowledge_id']:raise ToolError('knowledge_owner_mismatch')
        snapshot=copy.deepcopy(self.state)
        result=self.execute(new_action,visual=visual)
        if result['status'] not in {'applied','reused'}:
            self.state=snapshot;return result
        if result['action_id']!=annotation_id:self.remove_annotation(annotation_id)
        self.state[result['action_id']]['revision']=snapshot[annotation_id].get('revision',0)+1
        return result

    # Capability methods share a single validated executor. IDs and evidence are mandatory.
    def _write(self,tool,ids,evidence_id,knowledge_id,quote,text='',novelty='already_present'):
        return self.execute(Intent(intent_id='tool',knowledge_id=knowledge_id,tool=tool,target_ids=ids,
            evidence_quote=quote,text=text,novelty=novelty,reason='Explicit annotation tool call'),evidence=evidence_id)
    def highlight_element(self,element_id,evidence_id,knowledge_id,quote):return self._write('highlight',[element_id],evidence_id,knowledge_id,quote)
    def underline_element(self,element_id,evidence_id,knowledge_id,quote):return self._write('underline',[element_id],evidence_id,knowledge_id,quote)
    def circle_element(self,element_id,evidence_id,knowledge_id,quote):return self._write('circle',[element_id],evidence_id,knowledge_id,quote)
    def box_element(self,element_id,evidence_id,knowledge_id,quote):return self._write('box',[element_id],evidence_id,knowledge_id,quote)
    def connect_elements(self,from_id,to_id,evidence_id,knowledge_id,quote):return self._write('connect',[from_id,to_id],evidence_id,knowledge_id,quote,novelty='relation')
    def write_near(self,element_id,text,evidence_id,knowledge_id,quote):return self._write('write_near',[element_id],evidence_id,knowledge_id,quote,text,'additional')
    def bracket_elements(self,element_ids,evidence_id,knowledge_id,quote):return self._write('bracket',element_ids,evidence_id,knowledge_id,quote,novelty='relation')
    def check_element(self,element_id,evidence_id,knowledge_id,quote):return self._write('check',[element_id],evidence_id,knowledge_id,quote)

    def validate_annotations(self,page_id):
        if page_id!=self.registry.page:raise ToolError('wrong_page')
        return [{'action_id':identity,'valid':bool(self.validate(row['intent'],visual=row.get('visual_confirmed',False))[1])} for identity,row in self.state.items()]

    def render_preview(self,page_id):
        self.validate_annotations(page_id)
        if self._renderer is None:raise ToolError('render_capability_unavailable')
        return self._renderer(page_id,copy.deepcopy(self.state),False)

    def export_annotated_pdf(self,job_id):
        if job_id!=self.folder.name:raise ToolError('wrong_job')
        self.validate_annotations(self.registry.page)
        if self._renderer is None:raise ToolError('export_capability_unavailable')
        return self._renderer(self.registry.page,copy.deepcopy(self.state),True)
