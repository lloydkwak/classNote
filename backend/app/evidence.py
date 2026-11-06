"""Versioned provenance, stable identities and a JSON evidence graph (no graph database)."""

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from app.models import AnnotationProvenance


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(",",":"),
                                     allow_nan=False).encode()).hexdigest()


def stable_id(kind,*parts):
    return f"{kind}_{fingerprint(parts)[:24]}"


class AnnotationAction(BaseModel):
    schema_version: int = 1
    action_id: str
    knowledge_id: str
    evidence_id: str
    source_segment_ids: list[int]
    source_quote: str
    timestamp_range: tuple[float,float]
    page: int
    target_element_id: str | None
    target_kind: str | None
    grounding_status: str
    annotation_type: Literal["underline","circle","bracket","none"]
    annotation_text: str
    priority: int
    density: str
    registry_fingerprint: str
    selected: bool
    reason: str
    provenance: AnnotationProvenance


class EvidenceNode(BaseModel):
    id: str
    kind: str
    data: dict = {}


class EvidenceEdge(BaseModel):
    source: str
    target: str
    relation: str


class EvidenceGraph(BaseModel):
    model_config=ConfigDict(extra="forbid")
    schema_version: int = 1
    job_id: str
    revision: str
    sources: dict
    nodes: list[EvidenceNode]
    edges: list[EvidenceEdge]
    metrics: dict
    export: dict | None = None

    @model_validator(mode="after")
    def references_exist(self):
        ids={n.id for n in self.nodes}
        if len(ids)!=len(self.nodes):
            raise ValueError("duplicate evidence node")
        if any(e.source not in ids or e.target not in ids for e in self.edges):
            raise ValueError("dangling evidence reference")
        return self
