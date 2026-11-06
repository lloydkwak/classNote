"""Private JSONL entrypoint. Only the backend starts it, with a trusted job directory."""

import argparse
import json
import os
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--root",type=Path,required=True)
    args=parser.parse_args()
    # Preserve a private protocol FD; Python, native libraries and spawned GPU children
    # all see stderr on fd 1. Model progress cannot corrupt the JSONL stream.
    protocol=os.fdopen(os.dup(sys.stdout.fileno()),"w",encoding="utf-8",buffering=1)
    os.dup2(sys.stderr.fileno(),sys.stdout.fileno())
    from app.grounding.ipc import inside, MAX_MESSAGE
    from app.grounding.schema import TargetRequest
    from app.vlm.provider import GPUWorkerProvider,VLMFailure
    from app.vlm.schema import VLMConfig
    root=args.root.resolve(strict=True)
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(SystemExit(143)))
    with GPUWorkerProvider(VLMConfig(max_pixels=524288,max_input_tokens=4096,max_new_tokens=512)) as provider:
        while True:
            line=sys.stdin.buffer.readline(MAX_MESSAGE+1)
            if not line:
                break
            try:
                if len(line)>MAX_MESSAGE or not line.endswith(b"\n"):
                    raise ValueError("invalid_message_size")
                message=json.loads(line)
                if message.get("op") == "close":
                    break
                if message.get("op") != "select_targets":
                    raise ValueError("unsupported_operation")
                request=TargetRequest.model_validate(message["request"])
                inside(request.image_path,root)
                value=provider.select_targets(request,use_cache=message.get("use_cache",True))
                value["worker_environment"]={"python":sys.executable,"prefix":sys.prefix}
                response={"ok":True,"value":value}
            except VLMFailure as error:
                response={"ok":False,"error":str(error),"details":error.details}
            except Exception as error:
                response={"ok":False,"error":type(error).__name__}
            protocol.write(json.dumps(response,ensure_ascii=False)+"\n")
    protocol.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
