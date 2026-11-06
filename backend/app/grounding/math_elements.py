"""Native-glyph math groups. No OCR boxes, model coordinates or expression invention."""
import re
from pathlib import Path

import pymupdf

from app.grounding.schema import Element

VERSION='native-math-graph-v1'
SLOTS=str.maketrans({'a':'α','b':'β','g':'γ','d':'δ','q':'θ','l':'λ','m':'μ','s':'σ','w':'ω'})


def enrich_math_registry(pdf:Path,registry):
    result=registry.model_copy(deep=True)
    added=[];graph=[]
    with pymupdf.open(pdf) as doc:
        page=doc[registry.page-1];page.set_rotation(0)
        chars=[]
        for block in page.get_text('rawdict')['blocks']:
            for line in block.get('lines',[]):
                for span in line['spans']:
                    for char in span['chars']:
                        raw=char['c'];text=raw.translate(SLOTS) if span['font'].split('+')[-1] in {'SymbolMT','Symbol'} else raw
                        if text.isspace():continue
                        box=pymupdf.Rect(char['bbox'])&page.rect
                        if box.is_empty:continue
                        normalized=(box.x0/registry.width,box.y0/registry.height,box.x1/registry.width,box.y1/registry.height)
                        chars.append((normalized,text,raw))
        for formula in [e for e in registry.elements if e.kind=='formula' and e.source=='pdf']:
            native=[];excluded=[]
            for box,text,raw in chars:
                r=pymupdf.Rect(box);center=pymupdf.Point((r.x0+r.x1)/2,(r.y0+r.y1)/2)
                if not pymupdf.Rect(formula.bbox).contains(center):continue
                words=[e for e in registry.elements if e.kind in {'word','symbol'} and formula.id in e.parent_ids and pymupdf.Rect(e.bbox).contains(center)]
                if not words:
                    if text.isalpha() or text in {',',';',':'}:excluded.append((r.x0+r.x1)/2)
                    continue
                parent=min(words,key=lambda e:pymupdf.Rect(e.bbox).get_area())
                token=parent.text.strip('.,:;()[]')
                if len(token)>1 and token.isascii() and token.isalpha():
                    excluded.append((r.x0+r.x1)/2);continue
                if not re.fullmatch(r'[α-ωΑ-Ω0-9a-zA-Z=+−–\-*/().]',text):
                    if text in {',',';',':'}:excluded.append((r.x0+r.x1)/2)
                    continue
                native.append((box,text,raw))
            native.sort(key=lambda x:(round((x[0][1]+x[0][3])/2/.02),x[0][0]))
            # A true baseline must overlap; don't flatten a fraction/matrix into a fake row.
            if native and max(b[0][1] for b in native)>=min(b[0][3] for b in native):continue
            native.sort(key=lambda x:x[0][0])
            barriers={n for n in range(1,len(native)) if any(native[n-1][0][2]<x<native[n][0][0] for x in excluded)}
            glyphs=[]
            for n,(box,text,raw) in enumerate(native):
                identity=f'{formula.id}_glyph{n}'
                glyph=Element(id=identity,page=registry.page,kind='symbol',bbox=box,text=text,source='pdf',
                              parent_ids=[formula.id,*formula.parent_ids],recognition='native_math_glyph',raw_pdf_text=raw)
                glyphs.append(glyph);added.append(glyph)
                graph.append({'id':identity,'role':'glyph','formula_id':formula.id,'children':[]})
            def group(start,end,role):
                if any(start<b<end for b in barriers):return
                children=glyphs[start:end]
                if len(children)<2:return
                box=pymupdf.Rect(children[0].bbox)
                for child in children[1:]:box|=pymupdf.Rect(child.bbox)
                identity=f'{formula.id}_{role}_{start}_{end}'
                text=''.join(c.text for c in children)
                depth=0
                for char in text:
                    depth+=int(char=='(')-int(char==')')
                    if depth<0:return
                if depth:return
                element=Element(id=identity,page=registry.page,kind='formula',bbox=tuple(box),text=text,source='pdf',
                                parent_ids=[formula.id,*formula.parent_ids],recognition='native_subexpression')
                if any(e.id==identity for e in added):return
                added.append(element)
                for child in children:child.parent_ids.append(identity)
                for candidate in added:
                    if candidate.id!=identity and candidate.recognition=='native_subexpression' and box.contains(pymupdf.Rect(candidate.bbox)) and candidate.text!=text:
                        candidate.parent_ids.append(identity)
                graph.append({'id':identity,'role':role,'formula_id':formula.id,'children':[c.id for c in children]})
            stack=[];begin=0;pairs=[]
            for n,glyph in enumerate(glyphs):
                if n in barriers:group(begin,n,'term');begin=n;stack=[]
                if glyph.text=='(':stack.append(n)
                elif glyph.text==')' and stack:
                    start=stack.pop();group(start,n+1,'group');group(start+1,n,'inside');pairs.append((start,n+1))
                elif not stack and glyph.text in {'=','+','−','–','-'} and n>begin:
                    group(begin,n,'term');begin=n if glyph.text in {'−','–','-'} else n+1
            group(begin,len(glyphs),'term')
            side_start=0
            for n,glyph in enumerate(glyphs):
                if n in barriers:group(side_start,n,'side');side_start=n
                if glyph.text=='=':group(side_start,n,'side');side_start=n+1
            group(side_start,len(glyphs),'side')
            for start,end in pairs:
                following=next((p for p in pairs if p[0]==end and p[0] not in barriers),None)
                if following:group(start,following[1],'product')
            # Native adjacent unary minus/variable and imaginary coefficient groups.
            for n in range(len(glyphs)-1):
                if glyphs[n+1].text=='j' or (glyphs[n].text in {'−','–','-'} and glyphs[n+1].text.isalpha()):group(n,n+2,'pair')
    result.elements.extend(added)
    if added:result.schema_version=VERSION
    # Revalidate parents, unique IDs and all canonical bbox bounds.
    return type(registry).model_validate(result.model_dump()),{'version':VERSION,'nodes':graph}
