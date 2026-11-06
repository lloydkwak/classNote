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

VERSION="lecture-overview-v2-extractive-readable-math"


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


def draw_overview(document,data):
    page=document.new_page(width=595,height=842)
    page.draw_rect(pymupdf.Rect(0,0,595,126),color=None,fill=(.08,.085,.12))
    write_line(page,"classNote  /  LECTURE OVERVIEW",38,35,10,(.78,.72,1))
    title_lines=wrap_text(data["title"],515,21,"pretendard")[:2]
    for i,line in enumerate(title_lines):write_line(page,line,38,70+i*27,21,(1,1,1))
    write_line(page,f"Source slides {data['source_page_count']}  ·  Validated knowledge {len(data['validated_knowledge_ids'])}",38,150,10)
    flow=" → ".join(str(n) for n in data["flow_source_pages"][:14])+(" → …" if len(data["flow_source_pages"])>14 else "")
    write_line(page,"LECTURE FLOW  /  "+(flow or "확인된 발화 근거가 없습니다"),38,175,11)
    entries=data["entries"]
    y=207
    if not entries:
        write_line(page,"요약을 확정할 강의 근거가 충분하지 않습니다.",38,y+15,13)
        for i,line in enumerate(wrap_text("원본 슬라이드는 다음 페이지부터 그대로 보존됩니다. 전사와 상세 노트를 생성한 뒤 다시 export하면 발화에 근거한 개요가 추가됩니다.",515,11,"pretendard")):
            write_line(page,line,38,y+47+i*18,11)
    else:
        presentation=data.get("presentation") or presentation_plan(data)
        size=presentation["font_size"]
        for number,(entry,row) in enumerate(zip(entries,presentation["rows"])):
            y=207+number*row["row_budget"]
            for index,line in enumerate(row["title_lines"]):
                write_line(page,(f"{number+1:02d}  " if index==0 else "")+line,38,y,12);y+=17
            label=f"원본 슬라이드 {entry['source_page']}"
            if entry["audio_verified"]:
                label+=f"  ·  {int(entry['start'])//60}:{int(entry['start'])%60:02d}–{int(entry['end'])//60}:{int(entry['end'])%60:02d}"
            else: label+="  ·  오디오/페이지 연결 확인 필요"
            write_line(page,label,38,y,9,(.39,.32,.62));y+=16
            for line in row["text_lines"]:
                write_line(page,line,38,y,size);y+=size*1.4
    write_line(page,"발화 근거를 통과한 설명의 개요입니다. 세부 설명과 불확실성은 상세 노트에서 확인하세요.",38,807,8,(.35,.36,.42))
    write_line(page,f"Overview  ·  {data['status']}  ·  Source slide indices remain unchanged",38,824,8,(.35,.36,.42))
    return page


def link_overview(document,data):
    # Source labels point to the real exported slide. Audio seek controls live in the UI;
    # an offline PDF cannot assume a public server URL or a registered custom protocol.
    for entry in data["entries"]:
        needle=f"원본 슬라이드 {entry['source_page']}  ·"
        for area in document[0].search_for(needle):
            document[0].insert_link({"kind":pymupdf.LINK_GOTO,"from":area,"page":entry["export_page_index"]})
