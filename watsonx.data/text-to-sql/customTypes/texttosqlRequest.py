from typing import Optional, Literal
from pydantic import BaseModel, Field


class TextToSQLRequest(BaseModel):
    """
    Request payload for the /texttosql endpoint.
    All fields except `question` have defaults and can be omitted
    when the corresponding environment variables are set.
    """
    model_config = {'protected_namespaces': ()} # Allow any field names without prefixing
    question: str = Field(
        ...,
        title="Natural language question",
        description=(
            "The natural language question to convert to SQL. "
            "Make sure to provide enough context in the question for accurate SQL generation."),
        examples=["Show student names along with the courses they are enrolled in"],
    )

    top_n: int = Field(
        default=1,
        ge=1,
        le=5,
        title="Number of SQL candidates",
        description=(
            "How many SQL query candidates to generate and return. "
            "The highest-scored candidate is used for execution when "
            "db_execute=true."
        ),
    )

    raw_output: bool = Field(
        default=False,
        title="Include raw WDI output",
        description=(
            "When true, the full raw response from the WDI Text2SQL API "
            "is included in the response under raw_wdi_output. Useful for "
            "debugging and inspecting model reasoning."
        ),
    )

    db_execute: bool = Field(
        default=False,
        title="Execute SQL against DB2",
        description=(
            "When true, the best generated SQL is executed against the "
            "configured DB2 database and results are returned in "
            "query_results. Set to true when you need actual data rows, "
            "not just the SQL string."
        ),
    )
