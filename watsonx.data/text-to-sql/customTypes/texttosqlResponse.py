from typing import Any, Optional
from pydantic import BaseModel, Field


class GeneratedQuery(BaseModel):
    """A single SQL candidate returned by WDI Text2SQL, normalised for execution."""

    sql_raw: str = Field(
        ...,
        title="Raw SQL",
        description="The SQL exactly as returned by the WDI Text2SQL API before normalisation.",
    )
    sql: str = Field(
        ...,
        title="Normalised SQL",
        description=(
            "The SQL after normalisation: markdown fences removed, semicolons "
            "stripped, whitespace collapsed, ready for direct DB2 execution."
        ),
    )
    score: Optional[float] = Field(
        default=None,
        title="Confidence score",
        description="Confidence score (0–100) assigned by the WDI model.",
    )


class ModelUsage(BaseModel):
    model_config = {'protected_namespaces': ()} # Allow any field names without prefixing
    model_type: Optional[str] = Field(None, description="Type: 'embedding' or 'foundation'")
    model_id: Optional[str] = Field(None, description="Model identifier")
    input_token_count: Optional[int] = Field(None, description="Input tokens consumed")
    output_token_count: Optional[int] = Field(None, description="Output tokens generated")


class ResourceUsage(BaseModel):
    model_config = {'protected_namespaces': ()} # Allow any field names without prefixing
    token_count: Optional[int] = Field(None, description="Total tokens used")
    capacity_unit_hours: Optional[float] = Field(None, description="CUH consumed")
    model_usages: list[ModelUsage] = Field(default_factory=list)


class TextToSQLResponse(BaseModel):
    """
    Full response from the /texttosql endpoint.
    Contains the generated SQL (normalised), optional execution results,
    resource usage, and optional raw WDI output for debugging.
    """

    nl_question: str = Field(..., description="The original natural language question.")

    model_id: str = Field(..., description="The foundation model used for generation.")

    generated_queries: list[GeneratedQuery] = Field(
        ...,
        description=(
            "All SQL candidates returned by WDI, normalised. "
            "Ordered by score descending. The first entry is the best candidate "
            "and the one executed when db_execute=true."
        ),
    )

    executed: bool = Field(
        ...,
        description="True if the best SQL was executed against DB2.",
    )

    query_results: Optional[list[dict[str, Any]]] = Field(
        default=None,
        description=(
            "Rows returned by DB2 execution, as a list of column→value dicts. "
            "Null when db_execute=false or execution failed."
        ),
    )

    row_count: Optional[int] = Field(
        default=None,
        description="Number of rows returned. Null when not executed.",
    )

    error: Optional[str] = Field(
        default=None,
        description="Execution error message, if SQL execution failed.",
    )

    resource_usage: Optional[ResourceUsage] = Field(
        default=None,
        description="Token and capacity usage from the WDI API call.",
    )

    raw_wdi_output: Optional[dict[str, Any]] = Field(
        default=None,
        description=(
            "Full raw response from the WDI Text2SQL API. "
            "Only populated when raw_output=true in the request."
        ),
    )


class ErrorResponse(BaseModel):
    detail: str = Field(..., description="Error detail message.")
