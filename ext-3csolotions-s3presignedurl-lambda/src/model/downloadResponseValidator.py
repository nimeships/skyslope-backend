# This file will define the response validation parameters for download URL requests.

from pydantic import BaseModel

class DownloadResponse(BaseModel):
    method: str = "GET"
    bucket: str
    key: str
    download_url: str
    expires_in: int
