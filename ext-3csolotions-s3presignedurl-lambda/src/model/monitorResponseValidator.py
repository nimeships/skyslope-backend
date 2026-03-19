# This file will define the response validation parameters for monitor status requests.

from pydantic import BaseModel
from typing import Optional

class MonitorResponse(BaseModel):
    request_id: str
    stage: str
    status: str
    progress: int
    total: int
    message: str
    last_updated: str
    percentage: Optional[int] = None
