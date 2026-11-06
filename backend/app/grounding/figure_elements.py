"""Conservative pixel geometry candidates for simple colored point/axis plots.

These are geometric candidates, not recognized mathematical labels. A VLM must confirm
roles against lecture evidence before a tool can use them. No SAM/model download.
"""
import hashlib

import numpy as np
import pymupdf

from app.grounding.schema import Element

VERSION='figure-pixel-candidates-v1'


def enrich_figure_registry(pdf,registry):
    result=registry.model_copy(deep=True);graph=[]
    with pymupdf.open(pdf) as document:
        page=document[registry.page-1];page.set_rotation(0)
        for figure in [e for e in registry.elements if e.kind=='figure']:
            area=pymupdf.Rect(figure.bbox)*pymupdf.Matrix(registry.width,registry.height)
            if area.width<60 or area.height<60:continue
            zoom=min(3.,900/max(area.width,area.height))
            pix=page.get_pixmap(matrix=pymupdf.Matrix(zoom,zoom),clip=area,alpha=False,colorspace=pymupdf.csRGB)
            rgb=np.frombuffer(pix.samples,dtype=np.uint8).reshape(pix.height,pix.width,3).astype(np.int16)
            if (rgb.mean(axis=2)>235).mean()<.55:continue
            masks={'red':(rgb[:,:,0]>140)&(rgb[:,:,0]>rgb[:,:,1]+70)&(rgb[:,:,0]>rgb[:,:,2]+70),
                   'blue':(rgb[:,:,2]>140)&(rgb[:,:,2]>rgb[:,:,0]+70)&(rgb[:,:,1]>rgb[:,:,0]+35)}
            found=[]
            for color,mask in masks.items():
                # A filled dot is denser than its thin vector shaft. Require a clear
                # local density peak, not a box inferred from a verbal description.
                k=max(7,int(4*zoom));half=k//2
                padded=np.pad(mask.astype(np.int32),((half,half),(half,half)))
                integral=np.pad(padded,((1,0),(1,0))).cumsum(0).cumsum(1)
                density=integral[k:,k:]-integral[:-k,k:]-integral[k:,:-k]+integral[:-k,:-k]
                peak=int(density.max(initial=0))
                if peak<k*k*.32:continue
                ys,xs=np.where(density>=peak*.98)
                if not len(xs) or xs.max()-xs.min()>3*k or ys.max()-ys.min()>3*k:continue
                cx,cy=float(xs.mean()),float(ys.mean())
                x0,x1=max(0,int(cx-k)),min(pix.width,int(cx+k+1));y0,y1=max(0,int(cy-k)),min(pix.height,int(cy+k+1))
                yy,xx=np.where(mask[y0:y1,x0:x1])
                if not len(xx):continue
                # Keep the dense center, not the whole connected line.
                radius=max(2,zoom*2.8)
                native=pymupdf.Rect((pix.x+cx-radius)/zoom,(pix.y+cy-radius)/zoom,(pix.x+cx+radius)/zoom,(pix.y+cy+radius)/zoom)
                native &=area
                found.append((color,native,cx,cy))
            # Two separated filled points are required for this specialized detector.
            if len(found)!=2 or np.hypot(found[0][2]-found[1][2],found[0][3]-found[1][3])<k*5:continue
            gray=(rgb.max(2)-rgb.min(2)<25)&(rgb.mean(2)>50)&(rgb.mean(2)<220)
            column=gray.sum(0);row=gray.sum(1)
            mx,my=max(1,int(pix.width*.1)),max(1,int(pix.height*.1))
            x=int(column[mx:-mx].argmax())+mx;y=int(row[my:-my].argmax())+my
            axes=column[x]>pix.height*.35 and row[y]>pix.width*.35
            a=np.array(found[0][2:]);b=np.array(found[1][2:]);o=np.array([x,y]);delta=b-a
            t=float(np.dot(o-a,delta)/np.dot(delta,delta))
            axes=axes and .1<t<.9 and np.linalg.norm(o-(a+t*delta))<max(4,zoom*2)
            checksum=hashlib.sha256(pix.samples).hexdigest()
            def add(identity,text,box,role):
                normalized=(box.x0/registry.width,box.y0/registry.height,box.x1/registry.width,box.y1/registry.height)
                result.elements.append(Element(id=identity,page=registry.page,kind='figure_part',bbox=normalized,text=text,source='pdf',
                    parent_ids=[figure.id],recognition='pixel_geometry_candidate_'+role))
                graph.append({'id':identity,'role_candidate':role,'parent_figure':figure.id,'pixel_sha256':checksum,
                              'geometry_source':'rendered_pdf_pixels','semantic_verified':False})
            for color,box,cx,cy in found:add(figure.id+'_'+color+'_point','candidate '+color+' colored point',box,'colored_point')
            if axes:
                pad=2.2
                native=pymupdf.Rect((pix.x+x)/zoom-pad,(pix.y+y)/zoom-pad,(pix.x+x)/zoom+pad,(pix.y+y)/zoom+pad)&area
                add(figure.id+'_axis_intersection','candidate gray axis intersection',native,'axis_intersection')
    if graph:result.schema_version=result.schema_version+'+'+VERSION
    return type(registry).model_validate(result.model_dump()),{'version':VERSION,'nodes':graph}
