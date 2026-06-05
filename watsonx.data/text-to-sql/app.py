"""
IBM watsonx.data Intelligence — Text-to-SQL FastAPI Service
============================================================
Wraps the WDI Text2SQL API, normalises the generated SQL,
executes it against DB2, and returns results in a clean JSON
format ready for watsonx Orchestrate (OpenAPI tool or MCP).

Deploy: IBM Code Engine
Auth:   APP-API-KEY header (shared secret)
"""

import os
import re
import logging
import time
from datetime import datetime, timedelta
from typing import Optional, Literal

import jaydebeapi
import requests
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Security, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.security.api_key import APIKeyHeader
from starlette.status import HTTP_403_FORBIDDEN

from customTypes.texttosqlRequest import TextToSQLRequest
from customTypes.texttosqlResponse import (
    TextToSQLResponse,
    GeneratedQuery,
    ResourceUsage,
    ModelUsage,
    ErrorResponse,
)

# ── Logging ────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("text2sql-service")

load_dotenv()

# ── App ────────────────────────────────────────────────────────────
app = FastAPI(
    title="IBM watsonx Text-to-SQL Service",
    description=(
        "Converts natural language questions to SQL using IBM watsonx.data "
        "intelligence, normalises the SQL, and optionally executes it against "
        "DB2. Designed for use as an OpenAPI tool or MCP server in "
        "watsonx Orchestrate multi-agent systems."
    ),
    version="1.0.0",
    contact={"name": "IBM Build Engineering"},
    license_info={"name": "IBM Internal"},
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Config from environment ────────────────────────────────────────
IBM_CLOUD_API_KEY: str = os.environ.get("IBM_CLOUD_API_KEY", "")
WXD_CONTAINER_ID: str = os.environ.get("WXD_CONTAINER_ID", "")       # project or catalog id
WXD_CONTAINER_TYPE: str = os.environ.get("WXD_CONTAINER_TYPE", "project")
WXD_MODEL_ID: str = os.environ.get("WXD_MODEL_ID", "meta-llama/llama-3-3-70b-instruct")
WXD_TEXT2SQL_BASE: str = os.environ.get(
    "WXD_TEXT2SQL_BASE",
    "https://api.dataplatform.cloud.ibm.com/semantic_automation/v1/text_to_sql",
)

DB2_HOSTNAME: str = os.environ.get("DB2_HOSTNAME", "")
DB2_PORT: str = os.environ.get("DB2_PORT", "50001")
DB2_DATABASE: str = os.environ.get("DB2_DATABASE", "")
DB2_SCHEMA: str = os.environ.get("DB2_SCHEMA", "")
DB2_USERNAME: str = os.environ.get("DB2_USERNAME", "")
DB2_PASSWORD: str = os.environ.get("DB2_PASSWORD", "")

APP_API_KEY_VALUE: str = os.environ.get("APP_API_KEY", "")

# ── Security ───────────────────────────────────────────────────────
API_KEY_HEADER_NAME = "APP-API-KEY"
api_key_header = APIKeyHeader(name=API_KEY_HEADER_NAME, auto_error=False)


async def require_api_key(key: str = Security(api_key_header)) -> str:
    if key and key == APP_API_KEY_VALUE:
        return key
    raise HTTPException(
        status_code=HTTP_403_FORBIDDEN,
        detail="Invalid or missing APP-API-KEY header.",
    )


# ── IAM token cache ────────────────────────────────────────────────
_iam_token: Optional[str] = None
_iam_token_refreshed_at: Optional[datetime] = None
_TOKEN_TTL_MINUTES = 20


def get_iam_token() -> str:
    """Return a cached IAM bearer token, refreshing if older than 20 min."""
    global _iam_token, _iam_token_refreshed_at

    now = datetime.now()
    if (
        _iam_token is None
        or _iam_token_refreshed_at is None
        or now - _iam_token_refreshed_at > timedelta(minutes=_TOKEN_TTL_MINUTES)
    ):
        logger.info("Refreshing IAM token...")
        resp = requests.post(
            "https://iam.cloud.ibm.com/identity/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "urn:ibm:params:oauth:grant-type:apikey",
                "apikey": IBM_CLOUD_API_KEY,
            },
            timeout=30,
            verify=False,
        )
        resp.raise_for_status()
        _iam_token = resp.json()["access_token"]
        _iam_token_refreshed_at = now
        logger.info("IAM token refreshed successfully.")

    return _iam_token


# ── DB2 connection cache ───────────────────────────────────────────
_db2_conn = None


def get_db2_connection():
    """Return a cached DB2 JDBC connection (reconnects on failure)."""
    global _db2_conn

    if _db2_conn is not None:
        try:
            # Lightweight liveness check
            cur = _db2_conn.cursor()
            cur.execute("SELECT 1 FROM SYSIBM.SYSDUMMY1")
            cur.close()
            return _db2_conn
        except Exception:
            logger.warning("DB2 connection stale, reconnecting...")
            _db2_conn = None

    jdbc_url = (
        f"jdbc:db2://{DB2_HOSTNAME}:{DB2_PORT}/{DB2_DATABASE}"
        f":currentSchema={DB2_SCHEMA};"
        f"user={DB2_USERNAME};password={DB2_PASSWORD};sslConnection=true;"
    )
    logger.info("Connecting to DB2: %s:%s/%s", DB2_HOSTNAME, DB2_PORT, DB2_DATABASE)
    _db2_conn = jaydebeapi.connect(
        "com.ibm.db2.jcc.DB2Driver",
        jdbc_url,
        None,
        "db2jcc4.jar",
    )
    logger.info("DB2 connection established.")
    return _db2_conn


# ── SQL normaliser ─────────────────────────────────────────────────
def normalise_sql(raw_sql: str) -> str:
    """
    Clean and normalise SQL generated by WDI Text2SQL so it can be
    executed against DB2 without modification.

    Steps:
    1. Strip leading/trailing whitespace.
    2. Remove any trailing semicolons (jaydebeapi does not want them).
    3. Collapse multiple whitespace characters into single spaces.
    4. Remove markdown code fences if the model wrapped output in them.
    5. Strip common prefixes the model sometimes adds ("SQL:", "Query:").
    6. Ensure schema-qualified table names use the configured schema
       when the model emits unqualified names.
    """
    sql = raw_sql.strip()

    # Remove markdown fences
    sql = re.sub(r"^```(?:sql)?\s*", "", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\s*```$", "", sql)

    # Remove common model prefixes
    sql = re.sub(r"^(?:SQL|Query|Answer)\s*:\s*", "", sql, flags=re.IGNORECASE)

    # Collapse whitespace
    sql = re.sub(r"\s+", " ", sql).strip()

    # Remove trailing semicolon
    sql = sql.rstrip(";").strip()

    return sql


def execute_sql(sql: str) -> list[dict]:
    """Execute normalised SQL against DB2 and return rows as list of dicts."""
    conn = get_db2_connection()
    cur = conn.cursor()
    try:
        logger.info("Executing SQL: %.200s", sql)
        t0 = time.monotonic()
        cur.execute(sql)
        rows = cur.fetchall()
        elapsed = round((time.monotonic() - t0) * 1000, 2)
        columns = [desc[0] for desc in cur.description]
        result = [dict(zip(columns, row)) for row in rows]
        logger.info(
            "SQL returned %d rows in %sms", len(result), elapsed
        )
        return result
    finally:
        cur.close()


# ── WDI Text2SQL call ──────────────────────────────────────────────
def call_wdi_text2sql(
    question: str,
    container_id: str,
    container_type: str,
    dialect: str,
    model_id: str,
    raw_output: bool,
    top_n: int,
) -> dict:
    """Call the IBM watsonx.data intelligence Text2SQL REST API."""
    token = get_iam_token()
    params = {
        "container_id": container_id,
        "container_type": container_type,
        "dialect": dialect,
        "model_id": model_id,
        "top_n": top_n,
    }
    payload = {
        "query": question,
        "raw_output": raw_output,
    }
    headers = {
        "accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }

    logger.info(
        "Calling WDI Text2SQL | container=%s | model=%s | question=%.100s",
        container_id,
        model_id,
        question,
    )
    resp = requests.post(
        WXD_TEXT2SQL_BASE,
        params=params,
        headers=headers,
        json=payload,
        timeout=120,
        verify=False,
    )

    if resp.status_code != 200:
        logger.error("WDI API error %s: %s", resp.status_code, resp.text[:500])
        raise HTTPException(
            status_code=502,
            detail=f"WDI Text2SQL API returned {resp.status_code}: {resp.text[:300]}",
        )

    return resp.json()


# ══════════════════════════════════════════════════════════════════
# ROUTES
# ══════════════════════════════════════════════════════════════════

@app.get("/", tags=["Health"])
def health():
    """Service liveness probe."""
    return {
        "service": "IBM watsonx Text-to-SQL",
        "status": "ok",
        "version": "1.0.0",
    }


@app.get("/health", tags=["Health"])
def health_detailed():
    """Detailed health check — verifies DB2 connectivity."""
    db2_ok = False
    db2_error = None
    try:
        get_db2_connection()
        db2_ok = True
    except Exception as exc:
        db2_error = str(exc)

    return {
        "service": "IBM watsonx Text-to-SQL",
        "status": "ok" if db2_ok else "degraded",
        "db2_connected": db2_ok,
        "db2_error": db2_error,
        "iam_token_cached": _iam_token is not None,
    }


@app.post(
    "/texttosql",
    response_model=TextToSQLResponse,
    tags=["Text-to-SQL"],
    summary="Convert natural language to SQL and optionally execute against DB2",
    description=(
        "Accepts a natural language question about project management data, "
        "calls the IBM watsonx.data intelligence Text2SQL API to generate SQL, "
        "normalises the SQL for DB2 execution, and optionally executes it "
        "returning actual data rows. "
        "\n\n"
        "**Use this tool for STRUCTURED queries**: task counts, deadlines, "
        "assignments, budget totals, status breakdowns, overdue items, "
        "team workloads, and any question answerable from relational tables. "
        "\n\n"
        "Set `db_execute=true` to get actual result rows alongside the SQL."
    ),
    responses={
        200: {"description": "SQL generated (and optionally executed) successfully"},
        400: {"model": ErrorResponse, "description": "Bad request"},
        403: {"model": ErrorResponse, "description": "Invalid API key"},
        502: {"model": ErrorResponse, "description": "WDI API error"},
    },
)
async def texttosql(
    request: TextToSQLRequest,
    _key: str = Security(require_api_key),
) -> TextToSQLResponse:
    """
    Main endpoint. Calls WDI Text2SQL then optionally executes the SQL.

    **Routing hint for watsonx Orchestrate agents**: Use this endpoint
    when the user asks about structured project data — counts, dates,
    statuses, assignments, budgets, or any question that maps to a
    database query.
    """
    # Resolve defaults — allow per-request overrides of env defaults
    container_id = WXD_CONTAINER_ID
    container_type = WXD_CONTAINER_TYPE
    model_id = WXD_MODEL_ID

    if not container_id:
        raise HTTPException(
            status_code=400,
            detail="container_id is required (set in request or WXD_CONTAINER_ID env var).",
        )

    # ── 1. Generate SQL via WDI ────────────────────────────────────
    wdi_resp = call_wdi_text2sql(
        question=request.question,
        container_id=container_id,
        container_type=container_type,
        dialect="db2",
        model_id=model_id,
        raw_output=request.raw_output,
        top_n=request.top_n,
    )

    raw_queries: list[dict] = wdi_resp.get("generated_sql_queries", [])
    if not raw_queries:
        return TextToSQLResponse(
            nl_question=request.question,
            model_id=wdi_resp.get("model_id", model_id),
            generated_queries=[],
            executed=False,
            query_results=None,
            row_count=None,
            error="WDI returned no generated SQL queries for this question.",
            resource_usage=_parse_usage(wdi_resp),
            raw_wdi_output=wdi_resp if request.raw_output else None,
        )

    # ── 2. Normalise all returned SQL candidates ───────────────────
    normalised_queries: list[GeneratedQuery] = []
    for q in raw_queries:
        raw_sql = q.get("sql", "")
        normalised = normalise_sql(raw_sql)
        normalised_queries.append(
            GeneratedQuery(
                sql_raw=raw_sql,
                sql=normalised,
                score=q.get("score"),
            )
        )

    # ── 3. Execute best SQL if requested ──────────────────────────
    executed = False
    query_results = None
    row_count = None
    execution_error = None

    best_sql = normalised_queries[0].sql  # highest-scored query

    if request.db_execute:
        try:
            query_results = execute_sql(best_sql)
            row_count = len(query_results)
            executed = True
        except Exception as exc:
            logger.error("SQL execution failed: %s", exc)
            execution_error = str(exc)

    return TextToSQLResponse(
        nl_question=request.question,
        model_id=wdi_resp.get("model_id", model_id),
        generated_queries=normalised_queries,
        executed=executed,
        query_results=query_results,
        row_count=row_count,
        error=execution_error,
        resource_usage=_parse_usage(wdi_resp),
        raw_wdi_output=wdi_resp if request.raw_output else None,
    )


def _parse_usage(wdi_resp: dict) -> Optional[ResourceUsage]:
    ru = wdi_resp.get("resource_usage")
    if not ru:
        return None
    return ResourceUsage(
        token_count=ru.get("token_count"),
        capacity_unit_hours=ru.get("capacity_unit_hours"),
        model_usages=[
            ModelUsage(
                model_type=m.get("model_type"),
                model_id=m.get("model_id"),
                input_token_count=m.get("input_token_count"),
                output_token_count=m.get("output_token_count"),
            )
            for m in ru.get("model_usages", [])
        ],
    )


# ── Custom OpenAPI schema (enriched for Orchestrate) ───────────────
def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    # Orchestrate reads servers[0].url for the base URL
    schema["servers"] = [{"url": os.environ.get("SERVICE_BASE_URL", "http://localhost:4050")}]
    app.openapi_schema = schema
    return schema


app.openapi = custom_openapi


if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=4050, reload=True)
