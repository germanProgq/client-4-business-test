from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    """Base for API-facing schemas: accepts/emits camelCase (matching the
    assignment's example payloads) while keeping snake_case attribute names
    on the Python side."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)
