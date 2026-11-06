from pathlib import Path
from typing import Literal

from pydantic import BaseModel,ConfigDict,Field,model_validator

Tool=Literal['highlight','underline','circle','box','connect','write_near','bracket','check','skip']
Mode=Literal['legacy','ai_planner','visual_agent']


class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)


class Intent(Strict):
    intent_id:str=Field(min_length=1,max_length=80)
    knowledge_id:str=Field(min_length=1)
    tool:Tool
    target_ids:list[str]=Field(default_factory=list,max_length=6)
    evidence_quote:str=Field(min_length=1,max_length=2000)
    novelty:Literal['already_present','additional','relation']
    text:str=Field(default='',max_length=100)
    reason:str=Field(min_length=1,max_length=300)
    priority:int=Field(default=3,ge=1,le=5)
    placement:Literal['near_target','above','below','left','right','margin']='near_target'


class LearningPoint(Strict):
    knowledge_id:str
    already_printed:str=Field(max_length=500)
    additional_teaching:str=Field(default='',max_length=500)
    visual_learning_goal:str=Field(max_length=500)


class SemanticPlan(Strict):
    learning_points:list[LearningPoint]=Field(default_factory=list,max_length=20)
    intents:list[Intent]=Field(default_factory=list,max_length=16)
    omissions:list[dict]=Field(default_factory=list,max_length=30)

    @model_validator(mode='after')
    def unique_ids(self):
        if len({i.intent_id for i in self.intents})!=len(self.intents):raise ValueError('duplicate_intent_id')
        return self


class VisualChoice(Strict):
    intent_id:str
    tool:Tool
    target_ids:list[str]=Field(default_factory=list,max_length=6)
    confidence:float=Field(ge=0,le=1)
    warning:str=Field(default='',max_length=160)


class VisualPlan(Strict):
    choices:list[VisualChoice]=Field(default_factory=list,max_length=4)


class Revision(Strict):
    action_id:str
    operation:Literal['accept','remove','retarget','replace','shorten','move','reroute']
    target_ids:list[str]=Field(default_factory=list,max_length=6)
    tool:Tool|None=None
    text:str=Field(default='',max_length=100)
    placement:Literal['near_target','above','below','left','right','margin']='near_target'
    reason:str=Field(default='',max_length=240)


class Review(Strict):
    verdict:Literal['accept','revise']
    decisions:list[Revision]=Field(default_factory=list,max_length=6)
    summary:str=Field(default='',max_length=240)

    @model_validator(mode='after')
    def consistent(self):
        if self.verdict=='accept' and any(d.operation!='accept' for d in self.decisions):raise ValueError('contradictory_review')
        if len({d.action_id for d in self.decisions})!=len(self.decisions):raise ValueError('duplicate_review_action')
        return self


class VisionRequest(Strict):
    page:int=Field(ge=1)
    image_path:Path
    operation:Literal['agent_plan','agent_review']
    payload:dict
