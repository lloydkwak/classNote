"""Same saved source evidence, three independent planners; source job is read-only.

Paid providers / 8B require --prepare and --gpu explicitly. Without these switches,
this command renders validated checkpoints or fails, never labels mock outputs real.
"""
import argparse,json,os,shutil,sys,time
from pathlib import Path
from collections import Counter
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pymupdf
from app.annotation_agent.pipeline import prepare_agent,load_agent
from app.pipeline import run as pipeline
from app.pipeline.rich_notes import DetailedNotes
from app.pipeline.overview_synthesis import prepare_synthesis
from app.grounding.document import build_registries
from app.models import Segment,Alignment
from app.pipeline.grounded_notes import write_json
from app.cache import hash_file


def summarize(annotations):
    notes=[n for p in annotations.pages for n in p.notes]
    visible=[n for n in notes if n.sketch.lines or n.sketch.strokes or n.sketch.arrow]
    text=[n for n in notes if n.note]
    source_words=''
    repeated=0
    from app.annotation_agent.tools import math_normalize
    for n in text:
        if n.agent_action and n.agent_action.get('novelty')=='already_present':repeated+=1
    metrics={'planned_actions':len(notes),'rendered_actions':len(visible),
      'direct_marks':sum(bool(n.sketch.strokes or n.sketch.arrow) for n in visible),
      'relation_actions':sum(bool(n.agent_action and n.agent_action['tool']=='connect') for n in visible),
      'text_annotations':len(text),'distinct_knowledge_rendered':len({n.content_id for n in visible}),
      'provenance_retention':sum(n.provenance is not None for n in visible)/len(visible) if visible else None,
      'rendered_marks_by_type':dict(Counter(n.sketch.mark for n in visible if n.sketch.strokes)),
      'rendered_arrows':sum(bool(n.sketch.arrow) for n in visible),
      'human_target_accuracy':None,'human_readability':None,'semantic_coverage':None,
      'reviewer_is_human':False,'text_duplication_metric':'exact normalized native text containment',
      'overview_synthesis_status':annotations.overview.get('synthesis_status') if annotations.overview else None,
      'layout':{str(p.page):p.layout_metrics for p in annotations.pages},
      'loss_audit':annotations.loss_audit,'agent':annotations.agent_report}
    return metrics


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--prepare',action='store_true');parser.add_argument('--gpu',action='store_true')
    parser.add_argument('--baseline',type=Path)
    args=parser.parse_args();source=args.job.resolve();root=args.output.resolve()
    if root.exists():raise SystemExit('Use a new output directory; existing artifacts are preserved.')
    folder=root/'state'/'jobs'/source.name;folder.mkdir(parents=True)
    for name in ['input.pdf','segments.json','alignment.json','extraction.json','detailed_notes.json','page_audio.json','audio.mp3','job.json','grounding_results.json','overview_synthesis.json','annotation-agent-ai_planner.json','annotation-agent-visual_agent.json']:
        if (source/name).exists():shutil.copyfile(source/name,folder/name)
    for audio_source in source.glob('audio_source.*'):
        if audio_source.is_file():shutil.copyfile(audio_source,folder/audio_source.name)
    if (source/'agent-cache').exists():shutil.copytree(source/'agent-cache',folder/'agent-cache')
    original_hash=hash_file(source/'input.pdf')
    os.environ['DATA_DIR']=str(root/'state');os.environ['OVERVIEW_MODE']='synthesis'
    content=DetailedNotes.model_validate_json((folder/'detailed_notes.json').read_text())
    segments=[Segment.model_validate(s) for s in json.loads((folder/'segments.json').read_text())]
    alignment=Alignment.model_validate_json((folder/'alignment.json').read_text())
    if args.prepare:prepare_synthesis(folder,content,segments,build_registries(folder/'input.pdf'))
    options=json.loads((folder/'job.json').read_text());all_annotations={};metrics={}
    for mode,filename in [('legacy','baseline.pdf'),('ai_planner','ai-planner.pdf'),('visual_agent','visual-agent.pdf')]:
        options['annotation_mode']=mode;write_json(folder/'job.json',options)
        if mode!='legacy':
            if load_agent(folder,content,mode) is None and args.prepare and (mode!='visual_agent' or args.gpu):prepare_agent(folder,content,segments,alignment,mode=mode)
            if load_agent(folder,content,mode) is None:raise SystemExit(f'{mode}: validated current checkpoint missing; rerun with --prepare and --gpu')
        ann=pipeline.density_annotations(folder.name,'detailed','v2','natural')
        target=root/filename;pipeline.export_density(folder.name,'detailed',target,'v2','natural')
        # Capture the API contract from the same production render route.
        all_annotations[mode]=ann.model_dump(mode='json');metrics[mode]=summarize(ann)
        with pymupdf.open(target) as doc:
            assert len(doc)==len(ann.page_mapping)
            assert len(doc)==len(build_registries(folder/'input.pdf'))+1
            for p in [0,3,4,5,6]:
                if p>=len(doc):continue
                label='overview' if p==0 else f'slide{p}'
                doc[p].get_pixmap(matrix=pymupdf.Matrix(1.4,1.4),alpha=False).save(root/f'{mode}-{label}.png')
            metrics[mode]['export_page_count']=len(doc)
            metrics[mode]['source_text_retained']=all(set(p.get_text().split())<=set(doc[n+1].get_text().split()) for n,p in enumerate(pymupdf.open(folder/'input.pdf')))
        from app.annotation_agent.tools import native_text_rows,math_normalize
        printable={p:math_normalize(' '.join(native_text_rows(folder/'input.pdf',p))) for p in {n.page for n in content.items}}
        text=[(p.page,n) for p in ann.pages for n in p.notes if n.note]
        metrics[mode]['source_text_duplication_rate']=sum(math_normalize(n.note) in printable.get(p,'') for p,n in text)/len(text) if text else 0
        metrics[mode]['source_text_duplication_denominator']=len(text)
        if mode!='legacy':
            state=json.loads((folder/f'annotation-agent-{mode}.json').read_text())
            write_json(root/f'{mode}-tool-calls.json',{p:r['tool_calls'] for p,r in state['pages'].items()})
            write_json(root/f'{mode}-reviewer-decisions.json',{p:r['reviews'] for p,r in state['pages'].items()})
            write_json(root/f'{mode}-gpu-metrics.json',state['telemetry'])
    if args.baseline:shutil.copyfile(args.baseline,root/'overview-before.pdf')
    write_json(root/'annotations.json',all_annotations);write_json(root/'quality-metrics.json',metrics)
    write_json(root/'page-mapping-tests.json',{'source_pages_unchanged':hash_file(source/'input.pdf')==original_hash,
        'export_count_correct':all(m['export_page_count']==len(build_registries(source/'input.pdf'))+1 for m in metrics.values()),
        'source_to_export':all_annotations['visual_agent']['page_mapping'],
        'audio_ranges_unchanged':not(source/'page_audio.json').exists() or hash_file(source/'page_audio.json')==hash_file(folder/'page_audio.json'),
        'audio_alignment_accuracy':None})
    write_json(folder/'status.json',{'job_id':folder.name,'status':'done','error':None})
    write_json(root/'report.json',{'input_kind':'saved_real_lecture' if len(segments)>0 else 'unknown',
        'source_job':source.name,'same_transcript_alignment_knowledge':True,'stt_reexecuted':False,'human_gold':False,
        'modes':list(metrics),'notes':'AI review is model assessment, not human correctness. Inspect PNGs and report failures.'})
    (root/'report.md').write_text('# Annotation v3 comparison\n\nSame stored transcript, alignment and Rich Notes for A/B/C. See quality-metrics.json, tool/review/GPU JSON and rendered PNGs. Human accuracy and semantic coverage are unmeasured.\n')
    print(json.dumps({'output':str(root),'metrics':{mode:{k:v for k,v in m.items() if k in {'planned_actions','rendered_actions','direct_marks','relation_actions','overview_synthesis_status'}} for mode,m in metrics.items()}},ensure_ascii=False))

if __name__=='__main__':main()
