"""Extractive, source-validated lecture overview and explicit source/export index mapping.

No model call and no density filtering. The public source page numbers stay 1-based;
source_slide_index is always 0-based, and only exported PDF pages get an extra offset.
"""
import json
import re
from pathlib import Path

import pymupdf

from app.cache import hash_file
from app.evidence import fingerprint
from app.pipeline.block_align import quote_in_transcript,has_unsupported_number
from app.grounding.schema import GroundingItem
from app.grounding.selection import speech_error
from app.pipeline.layout import wrap_text,text_runs,load_note_font,fallback_font,run_width

VERSION="lecture-overview-v3-study"


def page_mapping(count):
    return [{"kind":"overview","source_slide_index":None,"source_page":None,"export_page_index":0}]+[
        {"kind":"slide","source_slide_index":i,"source_page":i+1,"export_page_index":i+1} for i in range(count)]


def build_overview(pdf,annotations,content=None,segments=(),alignment=None):
    with pymupdf.open(pdf) as document:
        count=len(document)
        native_title=next((line.strip() for line in document[0].get_text().splitlines() if len(line.strip())>8),"Lecture") if count else "Lecture"
    title=annotations.title or native_title
    records=[];rejected=[]
    if content is not None:
        for item in content.items:
            ids=item.segment_ids
            reason=None
            if not ids or ids!=list(range(min(ids),max(ids)+1)) or any(i<0 or i>=len(segments) for i in ids):
                reason="invalid_segment_reference"
            elif item.page<1 or item.page>count:
                reason="invalid_source_slide"
            elif speech_error(GroundingItem.from_content_item(item),segments):
                reason=speech_error(GroundingItem.from_content_item(item),segments)
            elif has_unsupported_number(item.title+" "+item.content+" "+item.short_note,item.source_quote):
                reason="unsupported_number"
            if reason:
                rejected.append({"knowledge_id":item.id,"reason":reason});continue
            windows=[w for w in alignment.windows if w.page==item.page and w.start<item.end and w.end>item.start] if alignment else []
            reliable=bool(windows) and min(w.confidence for w in windows)>=.3
            reliable=reliable and item.confidence>=.3 and 0<=item.start<item.end
            records.append({"knowledge_id":item.id,"source_page":item.page,"source_slide_index":item.page-1,
                            "title":item.title,"text":item.short_note,"type":item.type,"importance":item.importance,
                            "start":item.start,"end":item.end,"audio_verified":reliable,
                            "source_segment_ids":ids,"checks":["source_quote","segment_ids","numeric_inventory"]})
    groups=[]
    for number in sorted({r["source_page"] for r in records},key=lambda p:min(r["start"] for r in records if r["source_page"]==p)):
        rows=[r for r in records if r["source_page"]==number]
        representative=max(rows,key=lambda r:r["importance"])
        groups.append({"source_page":number,"source_slide_index":number-1,"export_page_index":number,
                       "title":representative["title"],"text":"; ".join(dict.fromkeys(r["text"] for r in rows)),
                       "knowledge_ids":[r["knowledge_id"] for r in rows],"types":sorted({r["type"] for r in rows}),
                       "start":min(r["start"] for r in rows),"end":max(r["end"] for r in rows),
                       "audio_verified":all(r["audio_verified"] for r in rows),"importance":max(r["importance"] for r in rows)})
    selected=sorted(sorted(groups,key=lambda g:-g["importance"])[:7],key=lambda g:g["start"])
    represented={k for g in selected for k in g["knowledge_ids"]}
    data={"schema_version":1,"version":VERSION,"title":title,"status":"grounded" if records and not rejected and content.complete else "partial" if records else "insufficient_evidence",
          "pdf_sha256":hash_file(pdf),"source_page_count":count,"export_page_index":0,"source_slide_index":None,
          "entries":selected,"flow_source_pages":[g["source_page"] for g in groups],
          "validated_knowledge_ids":[r["knowledge_id"] for r in records],"records":records,"rejected":rejected,
          "omitted_overview_knowledge_ids":[r["knowledge_id"] for r in records if r["knowledge_id"] not in represented],
          "detailed_notes_preserved":True,"source_pages_unchanged":True}
    data["presentation"]=presentation_plan(data)
    data["fingerprint"]=fingerprint(data)
    return data,page_mapping(count)


def attach_overview(folder,annotations,content=None):
    from app.models import Segment,Alignment
    segments=[Segment.model_validate(s) for s in json.loads((folder/"segments.json").read_text())] if (folder/"segments.json").exists() else []
    alignment=Alignment.model_validate_json((folder/"alignment.json").read_text()) if (folder/"alignment.json").exists() else None
    annotations.overview,annotations.page_mapping=build_overview(folder/"input.pdf",annotations,content,segments,alignment)
    from app.pipeline.overview_synthesis import load_synthesis
    synthesis=load_synthesis(folder,content,segments)
    if synthesis:
        annotations.overview['study']=synthesis['study']
        annotations.overview['synthesis_fingerprint']=synthesis['fingerprint']
        annotations.overview['synthesis_status']='validated'
    else:
        annotations.overview['synthesis_status']='source_excerpt_fallback'
    annotations.overview['fingerprint']=fingerprint({k:v for k,v in annotations.overview.items() if k!='fingerprint'})
    return annotations


def write_line(page,text,x,y,size,color=(.13,.17,.25)):
    writer=pymupdf.TextWriter(page.rect)
    for run,fallback in text_runs(text,"pretendard"):
        font=fallback_font() if fallback else load_note_font("pretendard")
        writer.append((x,y),run,font=font,fontsize=size)
        x+=run_width(run,fallback,"pretendard",size)
    writer.write_text(page,color=color)


def presentation_plan(data):
    """Bound a one-page overview; never truncate a mathematical claim mid-sentence.

    Full records remain in the JSON and Detailed Notes; compressed IDs are explicit.
    """
    entries=data["entries"]
    budget=(770-207)/max(1,len(entries));result=[]
    for entry in entries:
        titles=wrap_text(entry["title"],515,12,"pretendard")
        title_lines=titles[:2]
        if len(titles)>2:title_lines[-1]+="…"
        maximum=max(1,int((budget-len(title_lines)*17-16-13)/(11*1.4)))
        lines=wrap_text(entry["text"],515,11,"pretendard")
        compressed=len(lines)>maximum
        if compressed:
            # Whole existing short notes only: a clipped equation changes its meaning.
            selected=[]
            for record in data["records"]:
                if record["knowledge_id"] not in entry["knowledge_ids"]:continue
                candidate=wrap_text(record["text"],515,11,"pretendard")
                if len(selected)+len(candidate)<=maximum-1:selected+=candidate
            lines=selected+["… 상세 노트와 연결된 원본 슬라이드 참조"]
        result.append({"source_page":entry["source_page"],"title_lines":title_lines,"text_lines":lines,
                       "compressed":compressed or len(titles)>2,"knowledge_ids":entry["knowledge_ids"],
                       "row_budget":budget})
    return {"font_size":11,"rows":result,"compressed_knowledge_ids":list(dict.fromkeys(
        k for row in result if row["compressed"] for k in row["knowledge_ids"]))}


def _study_sections(data):
    study=data.get('study')
    if not study:
        # Safe legacy/no-provider fallback: selected source explanations, no invented
        # whole-lecture synthesis. Full claims stay intact in Detailed Notes.
        records=sorted(data.get('records',[]),key=lambda r:-r['importance'])
        sections=[]
        seen=set()
        for record in records:
            identity=record['title'].casefold()
            if identity in seen:continue
            seen.add(identity)
            sections.append((record['title'],record['text']))
        return data['title'], '', [('SOURCE EXPLANATIONS',sections[:6])]
    def displayed_math(expression):
        from app.grounding.selection import SPOKEN_GREEK
        for symbol,name in SPOKEN_GREEK.items():expression=re.sub(r'\b'+name+r'\b',symbol,expression,flags=re.I)
        return expression
    sections=[('LEARNING GOALS',[(p.get('title',''),p['text']) for p in study['objectives']]),
              ('CORE CONCEPTS',[(p['title'],p['text']) for p in study['concepts']]),
              ('FORMULAS / PRINCIPLES',[(displayed_math(p['expression']),p['meaning']) for p in study['formulas']]),
              ('WORKED EXAMPLES',[(p['title'],displayed_math(p['given'])+'  →  '+displayed_math(p['result'])+'\n'+p['explanation']) for p in study['examples']]),
              ('CONNECT / REMEMBER',[(p.get('title',''),p['text']) for p in study['connections']+study['review_points']])]
    return study['title'],study['introduction']['text'],[(title,rows) for title,rows in sections if rows]


def study_presentation(data):
    title,introduction,sections=_study_sections(data)
    # Fixed lower bound. Whole cards are prioritized if a very long legacy job does
    # not fit; nothing is clipped inside a mathematical expression.
    for size in (11.5,11,10.5):
        cards=[]
        for heading,rows in sections:
            cards.append(('section',heading,11,18))
            for label,text in rows:
                if label:cards.extend(('label',line,size+1,(size+1)*1.35) for line in wrap_text(label,507,size+1,'pretendard'))
                for paragraph in text.split('\n'):
                    cards.extend(('body',line,size,size*1.38) for line in wrap_text(paragraph,507,size,'pretendard'))
                cards.append(('space','',size,7))
        intro=wrap_text(introduction,515,size,'pretendard') if introduction else []
        total=len(intro)*size*1.4+sum(c[3] for c in cards)
        if total<=628:return intro,cards,size,False
    # Compress structure, not equations or sentences: keep the introduction,
    # all core concepts/formulas/examples, and prioritize optional review cards.
    if data.get('study'):
        sections=[(heading,rows[:2] if heading=='LEARNING GOALS' else rows[:1] if heading=='CONNECT / REMEMBER' else rows)
                  for heading,rows in sections]
        sections=[(heading,[(label,'') for label,text in rows] if heading=='FORMULAS / PRINCIPLES' else rows)
                  for heading,rows in sections]
        sections=[(heading,[('',text) if heading=='WORKED EXAMPLES' and '=' in label else (label,text) for label,text in rows]) for heading,rows in sections]
        intro=wrap_text(introduction,515,10.5,'pretendard')
        cards=[]
        for heading,rows in sections:
            cards.append(('section',heading,10.5,18))
            for label,text in rows:
                if label:cards.extend(('label',line,11.5,15.5) for line in wrap_text(label,507,11.5,'pretendard'))
                for paragraph in text.split('\n'):
                    if paragraph:cards.extend(('body',line,10.5,14.5) for line in wrap_text(paragraph,507,10.5,'pretendard'))
                cards.append(('space','',10.5,4))
        if len(intro)*14.7+sum(c[3] for c in cards)<=620:
            return intro,cards,10.5,True
    # Very long legacy/source-only fallback. Keep one heading and one complete short
    # claim per distinct concept; the full source remains available in Detailed Notes.
    intro=wrap_text(introduction,515,10.5,'pretendard')
    cards=[];used=len(intro)*14.7
    for heading,rows in sections:
        included=False
        for label,text in rows:
            lines=wrap_text((label+' — ' if label else '')+text.replace('\n',' '),507,10.5,'pretendard')
            height=(20 if not included else 6)+len(lines)*14.5
            if used+height>590:continue
            if not included:cards.append(('section',heading,10,20));included=True
            cards.extend(('body',line,10.5,14.5) for line in lines);used+=height
    cards.append(('body','Further explanations are preserved in Detailed Notes.',10.5,20))
    return intro,cards,10.5,True



def draw_overview(document,data):
    page=document.new_page(width=595,height=842)
    page.draw_rect(pymupdf.Rect(0,0,595,126),color=None,fill=(.08,.085,.12))
    write_line(page,'classNote  /  LECTURE OVERVIEW',38,35,10,(.78,.72,1))
    title,_,_=_study_sections(data)
    for i,line in enumerate(wrap_text(title,515,21,'pretendard')[:2]):
        write_line(page,line,38,70+i*27,21,(1,1,1))
    intro,cards,size,overflow=study_presentation(data)
    y=153
    for line in intro:write_line(page,line,38,y,size);y+=size*1.4
    if intro:y+=12
    for kind,text,font,height in cards:
        if kind=='section':
            y+=5
            page.draw_line((38,y-9),(557,y-9),color=(.86,.85,.92),width=.5)
            write_line(page,text,38,y+4,font,(.38,.30,.62));y+=height
        elif kind=='space':y+=height
        else:write_line(page,text,42,y,font);y+=height
    data['study_presentation']={'font_size':size,'overflow':overflow,'bottom':y}
    return page


def link_overview(document,data):
    # Links are attached to meaningful concept titles. No diagnostics or page/time
    # inventory is printed. Mapping always uses source slide -> export index.
    for entry in data['entries']:
        for area in document[0].search_for(entry['title']):
            document[0].insert_link({'kind':pymupdf.LINK_GOTO,'from':area,'page':entry['export_page_index']})
    study=data.get('study',{})
    sources={r['knowledge_id']:r['source_page'] for r in data.get('records',[])}
    for point in study.get('concepts',[]):
        target=next((sources[c['source_id']] for c in point['citations'] if c['source_id'] in sources),None)
        if target is not None:
            for area in document[0].search_for(point.get('title','')):
                document[0].insert_link({'kind':pymupdf.LINK_GOTO,'from':area,'page':target})
