import json
import sys
import threading
import time

import pytest

from app.grounding.ipc import IsolatedVLMProvider,WorkerFailure
from app.grounding.schema import ElementRegistry,Viewport,GroundingItem,TargetRequest
from app.models import Segment


@pytest.fixture
def request_and_root(tmp_path):
    image=tmp_path/"image.png";image.write_bytes(b"fixture")
    registry=ElementRegistry(pdf_sha256="0"*64,page=1,width=1,height=1,rotation=0,cropbox=[0,0,1,1],
                             rotation_matrix=[1,0,0,1,0,0],elements=[])
    viewport=Viewport(pdf_normalized_to_pixels=[1,0,0,1,0,0],width=1,height=1,visible_element_ids=[])
    item=GroundingItem(id="k",page=1,content="hello",source_quote="hello",start=0,end=1,segment_ids=[0])
    return TargetRequest(image_path=image,registry=registry,viewport=viewport,items=[item],
                         segments=[Segment(start=0,end=1,text="hello")]),tmp_path


def test_ipc_preserves_interpreter_and_reuses_process(request_and_root):
    request,root=request_and_root
    script=root/"fake.py"
    script.write_text('import sys,json\nfor line in sys.stdin:\n m=json.loads(line)\n if m["op"]=="close":break\n print(json.dumps({"ok":True,"value":{"prefix":sys.prefix}}),flush=True)\n')
    with IsolatedVLMProvider(root,python=sys.executable,entrypoint=script,timeout=5) as provider:
        assert provider.select_targets(request)["prefix"]==sys.prefix
        first=provider.process.pid
        assert provider.select_targets(request)["prefix"]==sys.prefix
        assert provider.process.pid==first
    assert provider.process is None


@pytest.mark.parametrize("mode",["timeout","protocol","exit","cancel"])
def test_ipc_timeout_protocol_exit_cancel_cleanup(request_and_root,mode):
    request,root=request_and_root
    script=root/"bad.py"
    body={"timeout":"import time;time.sleep(60)","cancel":"import time;time.sleep(60)",
          "protocol":"print('library banner',flush=True)","exit":"raise SystemExit(2)"}[mode]
    script.write_text(body)
    with IsolatedVLMProvider(root,python=sys.executable,entrypoint=script,timeout=0.7) as provider:
        if mode=="cancel":
            timer=threading.Timer(0.2,provider.cancel);timer.start()
        started=time.monotonic()
        with pytest.raises(WorkerFailure):
            provider.select_targets(request)
        assert provider.process is None and time.monotonic()-started<4
        if mode=="cancel": timer.join()


def test_ipc_rejects_outside_and_symlink_paths(request_and_root,tmp_path):
    request,root=request_and_root
    outside=tmp_path.parent/f"{tmp_path.name}-outside.png";outside.write_bytes(b"no")
    request.image_path=outside
    with IsolatedVLMProvider(root,python=sys.executable) as provider:
        with pytest.raises(WorkerFailure,match="unsafe"):
            provider.select_targets(request)
        assert provider.process is None
        link=root/"escape.png";link.symlink_to(outside)
        request.image_path=link
        with pytest.raises(WorkerFailure,match="unsafe"):
            provider.select_targets(request)
