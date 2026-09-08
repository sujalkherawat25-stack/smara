"""Persistent dependency DAG for adaptive research questions."""
from __future__ import annotations
from dataclasses import asdict, dataclass, field
import hashlib
from typing import Literal

State=Literal["unresolved","supported","refuted","blocked"]

@dataclass
class ResearchNode:
    id:str; question:str; dependencies:tuple[str,...]=(); state:State="unresolved"; evidence_ids:list[str]=field(default_factory=list); stopping_criterion:str="one directly supporting fetched passage"

class ResearchGraph:
    def __init__(self,max_nodes:int=64): self.max_nodes=max_nodes; self.nodes={}
    def add(self,node:ResearchNode):
        if node.id in self.nodes: raise ValueError("duplicate research node")
        if len(self.nodes)>=self.max_nodes: raise ValueError("research expansion limit reached")
        if node.id in node.dependencies: raise ValueError("self dependency")
        unknown=set(node.dependencies)-set(self.nodes)
        if unknown: raise ValueError(f"unknown dependencies: {sorted(unknown)}")
        self.nodes[node.id]=node
        if self._cycle(): del self.nodes[node.id]; raise ValueError("research dependency cycle")
    def _cycle(self):
        visiting=set(); visited=set()
        def visit(key):
            if key in visiting:return True
            if key in visited:return False
            visiting.add(key)
            if any(visit(dep) for dep in self.nodes[key].dependencies):return True
            visiting.remove(key); visited.add(key); return False
        return any(visit(key) for key in self.nodes)
    def ready(self): return [node for node in self.nodes.values() if node.state=="unresolved" and all(self.nodes[dep].state=="supported" for dep in node.dependencies)]
    def resolve(self,node_id,state:State,evidence_ids=()):
        if state not in {"supported","refuted","blocked"}: raise ValueError(state)
        node=self.nodes[node_id]; node.state=state; node.evidence_ids=list(dict.fromkeys(evidence_ids))
    def expand_contradiction(self,node_id:str,question:str):
        if self.nodes[node_id].state not in {"supported","refuted"}:raise ValueError("contradiction expansion requires evaluated evidence")
        ident="conflict-"+hashlib.sha256(f"{node_id}:{question}".encode()).hexdigest()[:16]
        if ident not in self.nodes:self.add(ResearchNode(ident,question,stopping_criterion=f"resolve contradiction for {node_id}"))
        return self.nodes[ident]
    def to_dict(self): return {"version":1,"nodes":[asdict(node) for node in self.nodes.values()]}
    @classmethod
    def from_dict(cls,value):
        if int(value.get("version",0))!=1: raise ValueError("unsupported research graph version")
        graph=cls(max_nodes=max(64,len(value.get("nodes",[]))))
        for item in value.get("nodes",[]): graph.add(ResearchNode(**{**item,"dependencies":tuple(item.get("dependencies",()))}))
        return graph
