from typing import List, Literal, Optional
from pydantic import BaseModel, Field


class InvestigationCreate(BaseModel):
    alert_id: str = Field(min_length=1)
    owner: Optional[str] = None
    notes: Optional[str] = None
    author: Optional[str] = None


class InvestigationUpdate(BaseModel):
    status: Optional[Literal["OPEN", "IN_PROGRESS", "RESOLVED"]] = None
    owner: Optional[str] = None            # "" clears the owner
    note: Optional[str] = None
    add_evidence_ids: Optional[List[str]] = None
    author: Optional[str] = None


class NoteCreate(BaseModel):
    text: str = Field(min_length=1)
    author: Optional[str] = None
