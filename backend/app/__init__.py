import os

# PyTorch (embeddings) and CTranslate2 (speech) share this process. By default, idle
# OpenMP threads keep spinning and starve each other: this took transcribing 60s of
# audio from 122s to 46s. It must be set before either library loads.
os.environ.setdefault("KMP_BLOCKTIME", "0")
os.environ.setdefault("OMP_WAIT_POLICY", "PASSIVE")
