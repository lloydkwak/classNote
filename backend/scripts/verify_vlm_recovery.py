"""Opt-in real GPU timeout/cancel/OOM recovery verification using an existing synthetic job.

Never part of production: the OOM probe explicitly requests total VRAM with the 8B loaded.
It reuses the existing model cache and shared GPU lease, and only kills its own process group.
"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.grounding.ipc import IsolatedVLMProvider,WorkerFailure
from app.grounding.schema import TargetRequest


PROBE = '''import sys
sys.path[:0]=BACKEND_PATHS
from app.vlm.runtime import QwenRuntime
import app.vlm.provider as providers
from vlm_worker import main

class OOMSelectionRuntime(QwenRuntime):
    def select_targets(self,request,reduced=False):
        if not reduced:
            import torch
            self.load()
            self.phase="intentional_oom_probe"
            torch.empty(torch.cuda.get_device_properties(0).total_memory,dtype=torch.uint8,device="cuda:0")
            raise RuntimeError("probe unexpectedly allocated total VRAM")
        return super().select_targets(request,reduced=True)

class ProbeProvider(providers.GPUWorkerProvider):
    def __init__(self,config):
        super().__init__(config,runtime_factory=OOMSelectionRuntime)

providers.GPUWorkerProvider=ProbeProvider
if __name__=="__main__":
    raise SystemExit(main())
'''


def memory():
    result=subprocess.run(["nvidia-smi","--query-gpu=memory.used","--format=csv,noheader,nounits"],
                          capture_output=True,text=True,check=True,timeout=10)
    return [int(value) for value in result.stdout.splitlines()]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run",type=Path,required=True,help="Completed synthetic verify_phase_d output")
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    report=json.loads((args.run/"report.json").read_text())
    if not report.get("synthetic") or not report.get("real_vlm"):
        parser.error("Requires a completed synthetic real-VLM run")
    output=args.output.resolve()
    output.mkdir(parents=True,exist_ok=False)
    folder=Path(report["job_folder"])
    manifest=json.loads((folder/"grounding_results.json").read_text())
    request=TargetRequest.model_validate(next(r["request"] for r in manifest["records"].values()
                                             if r["decision"]["status"]=="accepted"))
    results={"synthetic":True,"memory_before_mib":memory(),"cases":{},"new_inferences":0}
    started=time.perf_counter()
    try:
        for mode in ("timeout","cancel"):
            with IsolatedVLMProvider(folder,timeout=180) as provider:
                warm=provider.select_targets(request,use_cache=False)
                results["new_inferences"]+=int(warm["new_inference"])
                pid=provider.process.pid
                if mode=="timeout":
                    provider.timeout=0.05
                else:
                    timer=threading.Timer(0.05,provider.cancel)
                    timer.start()
                try:
                    provider.select_targets(request,use_cache=False)
                    raise AssertionError(f"{mode} did not interrupt the real warm worker")
                except WorkerFailure as error:
                    assert str(error)==f"worker_{'timeout' if mode=='timeout' else 'cancelled'}",str(error)
                    results["cases"][mode]={"error":str(error),"process_cleaned":provider.process is None,
                                              "warm_metrics":warm["metrics"]}
                finally:
                    if mode=="cancel": timer.join()
                try:
                    os.kill(pid,0)
                    raise AssertionError("IPC worker survived cleanup")
                except ProcessLookupError:
                    pass
            results["cases"][mode]["memory_after_close_mib"]=memory()
        paths=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).resolve().parent)]
        probe=output/"oom_worker.py"
        probe.write_text(PROBE.replace("BACKEND_PATHS",repr(paths)))
        with IsolatedVLMProvider(folder,timeout=180,entrypoint=probe) as provider:
            recovered=provider.select_targets(request,use_cache=False)
            results["new_inferences"]+=int(recovered["new_inference"])
            assert recovered["oom_count"]==1 and recovered["metrics"]["reduced_budget"]
            results["cases"]["oom"]={"oom_count":recovered["oom_count"],"recovered":True,
                                        "metrics":recovered["metrics"]}
        results.update(status="passed",memory_after_mib=memory())
    finally:
        results["seconds"]=time.perf_counter()-started
        (output/"report.json").write_text(json.dumps(results,indent=2))
    print(json.dumps({"status":results["status"],"report":str(output/"report.json")}))


if __name__=="__main__":
    main()
