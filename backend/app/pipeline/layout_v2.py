"""CPU-only, bounded, deterministic placement. Geometry is shared by SVG and PDF export.

Text boxes obey hard content/annotation exclusions. Routes and marks that cannot be made
safe are explicitly omitted; selected text is retained in expandable margins / a note band.
"""

import heapq
import math
import random
from dataclasses import dataclass

import pymupdf

from app.models import Sketch, Word
from app.grounding.document import build_registries, display_box
from app.pipeline.layout import NOTE_FONTS, text_width, wrap_text,text_runs,load_note_font,fallback_font

VERSION = "layout-v3-slide-first"
STYLES = ("clean", "natural", "highlighter")


def enrich_geometry(pdf, extraction):
    """Reuse canonical native geometry without OCR imports, downloads or GPU work."""
    registries = build_registries(pdf)
    for page, registry in zip(extraction.pages, registries):
        page.rotation=registry.rotation
        page.words = [Word(word_id=e.id, text=e.text, bbox=display_box(registry,e.bbox),
                           line_id=e.parent_ids[0] if e.parent_ids else "",source=e.source)
                      for e in registry.elements if e.kind in {"word","symbol"}
                      and e.recognition != "native_pdf_character"]
    with pymupdf.open(pdf) as document:
        for page,source in zip(extraction.pages,document):
            # Semantic extraction intentionally removes recurring headers, page numbers,
            # bullets and tiny math fragments. They still occupy the original slide.
            # These are layout obstacles only; they never become selectable target IDs.
            page.protected_text_bboxes=[]
            for word in source.get_text("words"):
                area=(pymupdf.Rect(word[:4])*source.rotation_matrix)&source.rect
                if not area.is_empty:
                    page.protected_text_bboxes.append((area.x0/source.rect.width,area.y0/source.rect.height,
                                                       area.x1/source.rect.width,area.y1/source.rect.height))
            pix=source.get_pixmap(matrix=pymupdf.Matrix(64/source.rect.width,64/source.rect.height),
                                  colorspace=pymupdf.csRGB,alpha=False)
            page.background_luma=[[sum(weight*pix.samples[(y*pix.width+x)*3+i]/255
                                       for i,weight in enumerate((.2126,.7152,.0722)))
                                   for x in range(pix.width)] for y in range(pix.height)]


def rect(box,w,h):
    return pymupdf.Rect(box[0]*w,box[1]*h,box[2]*w,box[3]*h)


def expanded(box,padding):
    return pymupdf.Rect(box)+(-padding,-padding,padding,padding)


def intersects(a,b):
    return not (a[2]<=b[0] or b[2]<=a[0] or a[3]<=b[1] or b[3]<=a[1])


def segment_hits(a,b,box,padding=0):
    """Closed segment / padded rectangle, including stroke width, via slab clipping."""
    r=expanded(box,padding)
    if max(a[0],b[0])<r.x0 or min(a[0],b[0])>r.x1 or max(a[1],b[1])<r.y0 or min(a[1],b[1])>r.y1:
        return False
    lo,hi=0.,1.
    for axis,low,high in ((0,r.x0,r.x1),(1,r.y0,r.y1)):
        delta=b[axis]-a[axis]
        if abs(delta)<1e-9:
            if a[axis]<low or a[axis]>high: return False
        else:
            t0,t1=sorted(((low-a[axis])/delta,(high-a[axis])/delta))
            lo,hi=max(lo,t0),min(hi,t1)
            if lo>hi: return False
    return True


def crossing(a,b,c,d):
    def cross(p,q,r): return (q[0]-p[0])*(r[1]-p[1])-(q[1]-p[1])*(r[0]-p[0])
    return cross(a,b,c)*cross(a,b,d)<-1e-6 and cross(c,d,a)*cross(c,d,b)<-1e-6


def safe_path(points, obstacles, padding=1.):
    return all(not segment_hits(a,b,r,padding) for a,b in zip(points,points[1:]) for r in obstacles)


def route(start,end,obstacles,previous,bounds,step=10.,max_nodes=3500):
    """Eight-neighbour A*, bounded node budget; direct / routed / explicit safe fallback."""
    def crosses(a,b):
        return any(crossing(a,b,c,d) for path in previous for c,d in zip(path,path[1:]))
    def clear(a,b): return safe_path([a,b],obstacles) and not crosses(a,b)
    if clear(start,end): return [start,end],"direct"
    x0,y0,x1,y1=bounds
    key=lambda p:(round((p[0]-x0)/step),round((p[1]-y0)/step))
    point=lambda k:(x0+k[0]*step,y0+k[1]*step)
    origin=key(start); goal=key(end)
    if not clear(start,point(origin)) or not clear(point(goal),end): return [],"blocked_grid_entry"
    queue=[(math.dist(start,end),0.,origin)]
    costs={origin:0.}; parents={}; visited=set()
    for _ in range(max_nodes):
        if not queue: break
        _,cost,current=heapq.heappop(queue)
        if current in visited: continue
        if current==goal:
            keys=[current]
            while keys[-1]!=origin: keys.append(parents[keys[-1]])
            path=[start]+[point(k) for k in reversed(keys)]+[end]
            # Remove redundant bends only when all hard constraints remain valid.
            simplified=[path[0]]; i=0
            while i<len(path)-1:
                j=len(path)-1
                while j>i+1 and not clear(path[i],path[j]): j-=1
                simplified.append(path[j]); i=j
            return simplified,"routed"
        visited.add(current)
        a=point(current)
        for dx,dy in ((1,0),(-1,0),(0,1),(0,-1),(1,1),(1,-1),(-1,1),(-1,-1)):
            other=(current[0]+dx,current[1]+dy); b=point(other)
            if not (x0<=b[0]<=x1 and y0<=b[1]<=y1) or not clear(a,b): continue
            value=cost+math.dist(a,b)
            if value<costs.get(other,float("inf")):
                costs[other]=value;parents[other]=current
                heapq.heappush(queue,(value+math.dist(b,end),value,other))
    return [],"search_budget_or_no_safe_route"


def candidates(target,nw,nh,w,h,gutter,pad,free=()):
    cx,cy=(target.x0+target.x1)/2,(target.y0+target.y1)/2
    choices=[("right",target.x1+pad,cy-nh/2),("left",target.x0-pad-nw,cy-nh/2),
             ("above",cx-nw/2,target.y0-pad-nh),("below",cx-nw/2,target.y1+pad)]
    for side,x in (("left_margin",-gutter+pad),("right_margin",w+pad)):
        for offset in (0,-1,1,-2,2):
            choices.append((side,x,max(pad,min(h-pad-nh,cy-nh/2+offset*(nh+pad)))))
    for box in free:
        area=rect(box,w,h)
        if area.width>=nw+2*pad and area.height>=nh+2*pad:
            choices.append(("free_space",area.x0+pad,area.y0+pad))
    # The previous pipeline populated free_rects only in annotation-engine=v2. Always
    # inspect native blank space too, so slide-first works for ordinary saved jobs.
    for x in (pad,w*.18,w*.36,w*.54,w-nw-pad):
        for y in range(int(h*.12),max(int(h*.12)+1,int(h-nh-pad)),max(12,int(h/18))):
            choices.append(("slide_free_space",x,float(y)))
    return [(name,pymupdf.Rect(x,y,x+nw,y+nh)) for name,x,y in choices]


def make_mark(note,w,h,style,obstacles):
    if not note.blocks: return "none",[],None,"unanchored"
    target=_target(note,w,h)
    # A broad figure/block is not a verified point/vector/axis. Never draw a figure-wide circle.
    figure=(note.target_kind=="figure" or (note.target_kind is None and any(b.kind=="figure" for b in note.blocks))) and (target.width>w*.15 or target.height>h*.16)
    if figure or note.target_kind=="figure":
        return "none",[],target,"figure_detail_unverified"
    if note.mark=="none": return "none",[],target,"explicitly_unmarked"
    rng=random.Random(note.id+":"+style)
    jitter=.25 if style=="natural" else 0.
    kind="circle" if note.mark=="circle" and target.width<w*.15 else "bracket" if note.mark=="bracket" else "underline"
    if note.mark in {"highlight","box","check","arrow"}: kind=note.mark
    def underline_points():
        if note.rotation==90:
            return [(target.x0-3+rng.uniform(-jitter,jitter),target.y0+target.height*t/8) for t in range(9)]
        if note.rotation==180:
            return [(target.x0+target.width*t/8,target.y0-3+rng.uniform(-jitter,jitter)) for t in range(9)]
        if note.rotation==270:
            return [(target.x1+3+rng.uniform(-jitter,jitter),target.y0+target.height*t/8) for t in range(9)]
        return [(target.x0+target.width*t/8,target.y1+3+rng.uniform(-jitter,jitter)) for t in range(9)]
    if kind=="circle":
        rx=(target.width/2+2.5)*math.sqrt(2);ry=(target.height/2+2.5)*math.sqrt(2)
        cx,cy=(target.x0+target.x1)/2,(target.y0+target.y1)/2
        points=[(cx+rx*math.cos(t*math.tau/48)+rng.uniform(-jitter,jitter),
                 cy+ry*math.sin(t*math.tau/48)+rng.uniform(-jitter,jitter)) for t in range(49)]
    elif kind=="highlight":
        if note.rotation in {90,270}:
            inset=max(3,min(16,target.width*.65,target.height*.8))/2+.3
            points=[((target.x0+target.x1)/2,target.y0+inset),((target.x0+target.x1)/2,target.y1-inset)]
        else:
            inset=max(3,min(16,target.height*.65,target.width*.8))/2+.3
            points=[(target.x0+inset,(target.y0+target.y1)/2),(target.x1-inset,(target.y0+target.y1)/2)]
    elif kind=="box":
        box=expanded(target,2.5)
        points=[(box.x0,box.y0),(box.x1,box.y0),(box.x1,box.y1),(box.x0,box.y1),(box.x0,box.y0)]
    elif kind=="check":
        points=[(target.x1+4,target.y1-4),(target.x1+7,target.y1-1),(target.x1+13,target.y1-10)]
    elif kind=="arrow":
        y=(target.y0+target.y1)/2;x=target.x0-4
        points=[(x-15,y),(x,y),(x-4,y-3),(x,y),(x-4,y+3)]
    elif kind=="bracket":
        # Bracket stays on the original formula's leading edge after /Rotate.
        points={0:[(target.x0-1.5,target.y0-2),(target.x0-4,target.y0-2),(target.x0-4,target.y1+2),(target.x0-1.5,target.y1+2)],
                90:[(target.x0-2,target.y0-1.5),(target.x0-2,target.y0-4),(target.x1+2,target.y0-4),(target.x1+2,target.y0-1.5)],
                180:[(target.x1+1.5,target.y0-2),(target.x1+4,target.y0-2),(target.x1+4,target.y1+2),(target.x1+1.5,target.y1+2)],
                270:[(target.x0-2,target.y1+1.5),(target.x0-2,target.y1+4),(target.x1+2,target.y1+4),(target.x1+2,target.y1+1.5)]}[note.rotation]
    else:
        points=underline_points()
    pen=max(3,min(16,(target.width if note.rotation in {90,270} else target.height)*.65)) if kind=="highlight" else 5.0 if style=="highlighter" else 1.3
    mark_obstacles=[o for o in obstacles if not (kind=="highlight" and expanded(target,.1).contains(o))]
    reason="native_target_mark"
    if kind=="circle" and not safe_path(points,obstacles,pen/2+.3):
        # A tight symbol inside a formula may have no room for a circle. Its native
        # glyph underline is precise and safer than crossing the neighbouring term.
        kind="underline";reason="circle_replaced_by_native_underline"
        points=underline_points()
    if not safe_path(points,mark_obstacles,pen/2+.3) or any(not(pen/2<=x<=w-pen/2 and pen/2<=y<=h-pen/2) for x,y in points):
        return "none",[],target,"mark_suppressed_content_collision"
    return kind,[points],target,reason


def _target(note,w,h):
    if not note.blocks: return pymupdf.Rect(w*.5,0,w*.5,0)
    result=rect(note.blocks[0].bbox,w,h)
    for block in note.blocks[1:]: result |= rect(block.bbox,w,h)
    return result


@dataclass
class LayoutResult:
    sketches:list[Sketch]
    gutter:float
    output_height:float
    metrics:dict


def text_obstacles(page):
    boxes=page.protected_text_bboxes or [word.bbox for word in page.words] or [b.bbox for b in page.blocks if b.kind=="text"]
    return [rect(box,page.width,page.height) for box in boxes]


def layout_page_v2(notes,page,font_name="pretendard",style="natural",max_route_nodes=3500):
    if style not in STYLES: raise ValueError("unsupported ink style")
    w,h=page.width,page.height; gutter=w*.33;pad=max(6,w*.012)
    font=w*NOTE_FONTS[font_name].size_ratio
    line_height=font*NOTE_FONTS[font_name].line_spacing
    nw=w*.24
    words=text_obstacles(page)
    figures=[rect(b.bbox,w,h) for b in page.blocks if b.kind=="figure"]
    occupied=words+figures
    placed=[]; previous=[]; sketches=[];band_y=h+pad
    for note in notes:
        target=_target(note,w,h)
        fonts=[fallback_font() if fallback else load_note_font(font_name) for _,fallback in text_runs(note.text,font_name)] or [load_note_font(font_name)]
        ascent=max(f.ascender for f in fonts);descent=max(0.,-min(f.descender for f in fonts))
        baseline=max(font,ascent*font)
        line_height=max(font*NOTE_FONTS[font_name].line_spacing,(ascent+descent)*font+1)
        lines=wrap_text(note.text,nw,font,font_name)
        nh=baseline+max(0,len(lines)-1)*line_height+descent*font
        options=[]
        for name,box in candidates(target,nw,nh,w,h,gutter,pad,page.free_rects):
            if box.x0< -gutter+pad or box.x1>w+gutter-pad or box.y0<pad or box.y1>h-pad: continue
            if any(intersects(expanded(box,2),r) for r in occupied+placed): continue
            distance=math.dist((box.x0+box.width/2,box.y0+box.height/2),
                               (target.x0+target.width/2,target.y0+target.height/2))
            # Hard tier: use a safe original-slide candidate before ANY margin.
            internal=box.x0>=0 and box.x1<=w
            score=distance+box.height*.2
            options.append((0 if internal else 1,score,name,box))
        if options:
            _,_,name,box=min(options,key=lambda value:(value[0],value[1],value[2],tuple(value[3])))
        else:
            name="bottom_note_band"
            band_width=w+2*gutter-2*pad
            lines=wrap_text(note.text,band_width,font,font_name);nh=baseline+max(0,len(lines)-1)*line_height+descent*font
            box=pymupdf.Rect(-gutter+pad,band_y,w+gutter-pad,band_y+nh)
            band_y=box.y1+pad
        placed.append(box)
        mark,strokes,anchor,mark_reason=make_mark(note,w,h,style,occupied)
        arrow=[]; route_reason="unanchored"; control=[]
        if anchor is not None and mark_reason!="figure_detail_unverified":
            points=[(anchor.x0-4,(anchor.y0+anchor.y1)/2),(anchor.x1+4,(anchor.y0+anchor.y1)/2),
                    ((anchor.x0+anchor.x1)/2,anchor.y0-4),((anchor.x0+anchor.x1)/2,anchor.y1+4)]
            starts=[(box.x0-3,box.y0+line_height/2),(box.x1+3,box.y0+line_height/2)]
            pairs=sorted(((math.dist(s,e),s,e) for s in starts for e in points),key=lambda v:v[0])
            blockers=occupied+[b for b in placed[:-1]]
            route_gutter=0 if box.x0>=0 and box.x1<=w else gutter
            bounds=(-route_gutter+2,2,w+route_gutter-2,max(h,band_y)-2)
            for _,start,end in pairs:
                if not(bounds[0]<=end[0]<=bounds[2] and bounds[1]<=end[1]<=bounds[3]): continue
                shaft,reason=route(start,end,blockers+[box],previous,bounds,step=max(8,w/75),
                                   max_nodes=max_route_nodes)
                route_reason=reason
                if shaft:
                    control=shaft;arrow=[shaft];previous.append(shaft)
                    angle=math.atan2(shaft[-1][1]-shaft[-2][1],shaft[-1][0]-shaft[-2][0])
                    head=[(end[0]-4*math.cos(angle-.45),end[1]-4*math.sin(angle-.45)),end,
                          (end[0]-4*math.cos(angle+.45),end[1]-4*math.sin(angle+.45))]
                    if safe_path(head,blockers+[box],.7): arrow.append(head)
                    break
        elif anchor is not None:
            route_reason="figure_block_only_no_verified_subtarget"
        side="left" if box.x0<0 else "right"
        placement={"engine":VERSION,"status":"placed","reason":name,"fallback_mode":
                   "expanded_note_band" if name=="bottom_note_band" else "expanded_margin" if "margin" in name else None,
                   "text_box":list(box),"anchor":list(anchor) if anchor is not None else None,
                   "target_bbox":list(anchor) if anchor is not None else None,
                   "anchor_point":list(control[-1]) if control else None,
                   "connector_control_points":control,"connector_reason":route_reason,"mark_reason":mark_reason,
                   "collision_count":0,"overflow":name=="bottom_note_band","baseline_offset":baseline}
        def darkness(area):
            if not page.background_luma or area.x0<0 or area.x1>w or area.y0>=h: return False
            grid=page.background_luma;gy=min(len(grid)-1,max(0,int((area.y0+area.y1)/2/h*len(grid))))
            gx=min(len(grid[gy])-1,max(0,int((area.x0+area.x1)/2/w*len(grid[gy]))))
            cells=[grid[gy][gx]]
            for x,y in ((area.x0,area.y0),(area.x1,area.y0),(area.x0,area.y1),(area.x1,area.y1)):
                row=min(len(grid)-1,max(0,int(y/h*len(grid))))
                col=min(len(grid[row])-1,max(0,int(x/w*len(grid[row]))))
                cells.append(grid[row][col])
            return sorted(cells)[len(cells)//2]<.35
        text_color=[1.,.88,.42] if darkness(box) else [.11,.30,.65]
        mark_color=[1.,.88,.42] if darkness(target) else [.11,.30,.65]
        pen=max(3,min(16,(target.width if note.rotation in {90,270} else target.height)*.65)) if mark=="highlight" else 5 if style=="highlighter" else 1.3
        sketches.append(Sketch(mark=mark,strokes=strokes,arrow=arrow,side=side,x=box.x0,y=box.y0,
                               width=box.width,height=box.height,font_size=font,line_height=line_height,lines=lines,
                               stroke_width=pen,text_color=text_color,mark_color=mark_color,
                               stroke_opacity=.24 if mark=="highlight" else .28 if style=="highlighter" else 1.,placement=placement))
    output_height=max(h,band_y if band_y>h+pad else h)
    gutter=gutter if any(b.x0<0 or b.x1>w for b in placed) else 0.
    metrics=evaluate(sketches,page,gutter,output_height)
    return LayoutResult(sketches,gutter,output_height,metrics)


def evaluate(sketches,page,gutter=None,output_height=None):
    w,h=page.width,page.height;gutter=gutter if gutter is not None else w*.28
    output_height=output_height or h
    words=text_obstacles(page)
    figures=[rect(b.bbox,w,h) for b in page.blocks if b.kind=="figure"]
    boxes=[pymupdf.Rect(s.x,s.y,s.x+s.width,s.y+s.height) for s in sketches]
    paths=[s.arrow[0] if s.arrow else [] for s in sketches]
    overlap=sum(intersects(a,b) for i,a in enumerate(boxes) for b in boxes[i+1:])
    crossings=sum(any(crossing(a,b,c,d) for a,b in zip(p,p[1:]) for c,d in zip(q,q[1:]))
                  for i,p in enumerate(paths) for q in paths[i+1:])
    ink=[(s,stroke,s.stroke_width/2,False) for s in sketches for stroke in s.strokes]+[(s,stroke,.5,True) for s in sketches for stroke in s.arrow]
    def intended_highlight(s,r):
        target=s.placement.get("target_bbox")
        return s.mark=="highlight" and s.stroke_opacity<=.3 and target is not None and expanded(pymupdf.Rect(target),.1).contains(r)
    captions=[word for word in words if any(f.y1-h*.05<=word.y0<=f.y1+h*.08 and word.x1>=f.x0 and word.x0<=f.x1 for f in figures)]
    return {"annotations":len(sketches),"annotation_placement_rate":1. if sketches else None,
            "original_text_occlusion_area":sum((b&r).get_area() for b in boxes for r in words if intersects(b,r)),
            "formula_figure_occlusion_count":sum(any(segment_hits(a,b,f,radius) for a,b in zip(p,p[1:]))
                                                   for s,p,radius,connector in ink for f in figures),
            "annotation_box_overlap_count":overlap,"connector_intersection_count":crossings,
            "ink_text_interference_count":sum(any(segment_hits(a,b,r,radius) for a,b in zip(p,p[1:]))
                                                for s,p,radius,connector in ink for r in words if connector or not intended_highlight(s,r)),
            "connector_content_interference_count":sum(any(segment_hits(a,b,r,.5) for a,b in zip(p,p[1:]))
                                                        for p in paths for r in words),
            "figure_caption_interference_count":sum(any(segment_hits(a,b,r,radius) for a,b in zip(p,p[1:]))
                                                     for s,p,radius,connector in ink for r in captions),
            "out_of_page_count":sum(b.x0< -gutter or b.x1>w+gutter or b.y0<0 or b.y1>output_height for b in boxes)
                 +sum(any(x-radius< -gutter or x+radius>w+gutter or y-radius<0 or y+radius>output_height for x,y in p) for _,p,radius,_ in ink),
            "overflow_to_margin_count":sum(b.x0<0 or b.x1>w or b.y0<0 or b.y1>h for b in boxes),
            "intentional_translucent_highlight_contacts":sum(intended_highlight(s,r) for s,p,radius,connector in ink for r in words if not connector),
            "original_slide_annotation_count":sum(0<=b.x0<b.x1<=w and 0<=b.y0<b.y1<=h for b in boxes),
            "visual_action_count":sum(bool(s.strokes) for s in sketches),
            "connector_omitted_count":sum(bool(s.placement.get("anchor")) and not s.arrow for s in sketches)}
