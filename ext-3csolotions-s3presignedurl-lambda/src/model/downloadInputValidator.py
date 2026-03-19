# This file will define the input validation parameters for download URL requests.

from pydantic import BaseModel, Field, validator
from src.model.validators.unique_id_validator import validate_unique_id_format


class DownloadRequest(BaseModel):
    unique_id: str = Field(
        ...,
        min_length=10,
        max_length=100,
        description="Unique identifier for the output file (format: {uuid}_{timestamp})"
    )

    _validate_unique_id = validator('unique_id', allow_reuse=True)(validate_unique_id_format)

    class Config:
        json_schema_extra = {
            "example": {
                "unique_id": "17eeb4ec5aa947708990cb220295e4c4_20260205T181422Z"
            }
        }
