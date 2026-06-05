"""
mcp_server.py — MCP (Model Context Protocol) wrapper for the
IBM watsonx Text-to-SQL service.

This exposes the same Text2SQL + execution functionality as an MCP
server so it can be wired directly into watsonx Orchestrate as an
MCP tool rather than an OpenAPI tool.

Usage:
  python mcp_server.py          # starts stdio MCP server
  python mcp_server.py --sse    # starts SSE MCP server on port 4052

The MCP server is an alternative to the OpenAPI FastAPI endpoint —
use whichever your Orchestrate instance supports.

Requirements:
  pip install mcp requests jaydebeapi JPype1 python-dotenv
"""

import argparse
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta
from typing import Any, Optional

import jaydebeapi
import requests
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("text2sql-mcp")

# ── Config ────────────────────────────────────────────────────────
IBM_CLOUD_API_KEY = os.environ.get("IBM_CLOUD_API_KEY", "")
WXD_CONTAINER_ID = os.environ.get("WXD_CONTAINER_ID", "")
WXD_CONTAINER_TYPE = os.environ.get("WXD_CONTAINER_TYPE", "project")
WXD_MODEL_ID = os.environ.get("WXD_MODEL_ID", "meta-llama/llama-3-3-70b-instruct")
WXD_TEXT2SQL_BASE = os.environ.get(
    "WXD_TEXT2SQL_BASE",
    "https://api.dataplatform.cloud.ibm.com/semantic_automation/v1/text_to_sql",
)
DB2_HOSTNAME = os.environ.get("DB2_HOSTNAME", "")
DB2_PORT = os.environ.get("DB2_PORT", "50001")
DB2_DATABASE = os.environ.get("DB2_DATABASE", "")
DB2_SCHEMA = os.environ.get("DB2_SCHEMA", "")
DB2_USERNAME = os.environ.get("DB2_USERNAME", "")
DB2_PASSWORD = os.environ.get("DB2_PASSWORD", "")

# ── IAM token cache ───────────────────────────────────────────────
_iam_token: Optional[str] = None
_iam_refreshed_at: Optional[datetime] = None


def get_iam_token() -> str:
    global _iam_token, _iam_refreshed_at
    if (
        _iam_token is None
        or _iam_refreshed_at is None
        or datetime.now() - _iam_refreshed_at > timedelta(minutes=20)
    ):
        resp = requests.post(
            "https://iam.cloud.ibm.com/identity/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "urn:ibm:params:oauth:grant-type:apikey", "apikey": IBM_CLOUD_API_KEY},
            timeout=30, verify=False,
        )
        resp.raise_for_status()
        _iam_token = resp.json()["access_token"]
        _iam_refreshed_at = datetime.now()
    return _iam_token


# ── DB2 connection cache ──────────────────────────────────────────
_db2_conn = None


def get_db2():
    global _db2_conn
    if _db2_conn:
        try:
            cur = _db2_conn.cursor()
            cur.execute("SELECT 1 FROM SYSIBM.SYSDUMMY1")
            cur.close()
            return _db2_conn
        except Exception:
            _db2_conn = None
    jdbc_url = (
        f"jdbc:db2://{DB2_HOSTNAME}:{DB2_PORT}/{DB2_DATABASE}"
        f":currentSchema={DB2_SCHEMA};user={DB2_USERNAME};password={DB2_PASSWORD};sslConnection=true;"
    )
    _db2_conn = jaydebeapi.connect("com.ibm.db2.jcc.DB2Driver", jdbc_url, None, "db2jcc4.jar")
    return _db2_conn


def normalise_sql(raw: str) -> str:
    sql = raw.strip()
    sql = re.sub(r"^```(?:sql)?\s*", "", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\s*```$", "", sql)
    sql = re.sub(r"^(?:SQL|Query|Answer)\s*:\s*", "", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\s+", " ", sql).strip().rstrip(";")
    return sql


def run_text2sql(question: str, db_execute: bool = False, dialect: str = "db2") -> dict[str, Any]:
    """Core logic shared by MCP tool and health checks."""
    token = get_iam_token()
    resp = requests.post(
        WXD_TEXT2SQL_BASE,
        params={
            "container_id": WXD_CONTAINER_ID,
            "container_type": WXD_CONTAINER_TYPE,
            "dialect": dialect,
            "model_id": WXD_MODEL_ID,
            "top_n": 1,
        },
        headers={
            "accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        json={"query": question, "raw_output": False},
        timeout=120,
        verify=False,
    )
    resp.raise_for_status()
    wdi = resp.json()

    queries = wdi.get("generated_sql_queries", [])
    if not queries:
        return {"error": "No SQL generated", "nl_question": question}

    raw_sql = queries[0]["sql"]
    sql = normalise_sql(raw_sql)
    score = queries[0].get("score")

    result: dict[str, Any] = {
        "nl_question": question,
        "sql": sql,
        "sql_raw": raw_sql,
        "score": score,
        "model_id": wdi.get("model_id"),
        "token_count": wdi.get("resource_usage", {}).get("token_count"),
        "executed": False,
        "query_results": None,
        "row_count": None,
        "error": None,
    }

    if db_execute:
        try:
            conn = get_db2()
            cur = conn.cursor()
            cur.execute(sql)
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
            result["query_results"] = [dict(zip(cols, r)) for r in rows]
            result["row_count"] = len(rows)
            result["executed"] = True
            cur.close()
        except Exception as exc:
            result["error"] = str(exc)

    return result


# ── MCP Server ────────────────────────────────────────────────────
try:
    from mcp.server import Server
    from mcp.server.models import InitializationOptions
    import mcp.server.stdio as stdio_server
    import mcp.types as types

    server = Server("ibm-text2sql")

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name="text_to_sql",
                description=(
                    "Convert a natural language question about project management data to SQL "
                    "using IBM watsonx.data intelligence, then optionally execute it against DB2. "
                    "Use for STRUCTURED queries: counts, dates, assignments, budgets, statuses."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "Natural language question about project data.",
                        },
                        "db_execute": {
                            "type": "boolean",
                            "description": "If true, execute the SQL and return result rows.",
                            "default": False,
                        },
                        "dialect": {
                            "type": "string",
                            "enum": ["db2", "presto", "mysql", "postgresql"],
                            "description": "SQL dialect. Use 'db2' for IBM Db2.",
                            "default": "db2",
                        },
                    },
                    "required": ["question"],
                },
            )
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
        if name != "text_to_sql":
            raise ValueError(f"Unknown tool: {name}")

        result = run_text2sql(
            question=arguments["question"],
            db_execute=arguments.get("db_execute", False),
            dialect=arguments.get("dialect", "db2"),
        )
        return [types.TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

    async def run_stdio():
        async with stdio_server.stdio_server() as (r, w):
            await server.run(r, w, InitializationOptions(
                server_name="ibm-text2sql",
                server_version="1.0.0",
                capabilities=server.get_capabilities(
                    notification_options=None,
                    experimental_capabilities={},
                ),
            ))

    MCP_AVAILABLE = True

except ImportError:
    MCP_AVAILABLE = False
    logger.warning("mcp package not installed. Run: pip install mcp")


if __name__ == "__main__":
    import asyncio

    parser = argparse.ArgumentParser(description="IBM Text-to-SQL MCP Server")
    parser.add_argument("--sse", action="store_true", help="Run as SSE server (for Orchestrate MCP endpoint)")
    parser.add_argument("--port", type=int, default=4052, help="SSE server port")
    args = parser.parse_args()

    if not MCP_AVAILABLE:
        print("ERROR: mcp package not installed. Run: pip install mcp")
        exit(1)

    if args.sse:
        # SSE mode — used when Orchestrate connects via URL
        try:
            from mcp.server.sse import SseServerTransport
            from starlette.applications import Starlette
            from starlette.routing import Route, Mount
            import uvicorn

            sse = SseServerTransport("/messages")

            async def handle_sse(request):
                async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
                    await server.run(streams[0], streams[1], InitializationOptions(
                        server_name="ibm-text2sql",
                        server_version="1.0.0",
                        capabilities=server.get_capabilities(
                            notification_options=None,
                            experimental_capabilities={},
                        ),
                    ))

            starlette_app = Starlette(routes=[
                Route("/sse", endpoint=handle_sse),
                Mount("/messages", app=sse.handle_post_message),
            ])

            print(f"Starting SSE MCP server on port {args.port}")
            print(f"Orchestrate MCP URL: http://0.0.0.0:{args.port}/sse")
            uvicorn.run(starlette_app, host="0.0.0.0", port=args.port)
        except ImportError:
            print("SSE mode requires: pip install mcp[server] starlette uvicorn")
    else:
        print("Starting stdio MCP server")
        asyncio.run(run_stdio())
