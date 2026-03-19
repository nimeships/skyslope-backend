# This file will define the response validation parameters for recent requests list.

from pydantic import BaseModel
from typing import List, Optional


class RequestItem(BaseModel):
    request_id: str
    status: str
    last_updated: str
    folder_name: Optional[str] = None


class RecentRequestsResponse(BaseModel):
    requests: List[RequestItem]
    count: int
