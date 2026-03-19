# This file will define the response validation parameters for presign URL requests.

from pydantic import BaseModel

class PresignResponse(BaseModel):
    method: str = "PUT"
    bucket: str
    key: str
    upload_url: str
    expires_in: int