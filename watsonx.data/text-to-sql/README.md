# IBM watsonx Text-to-SQL Service

> FastAPI microservice that wraps IBM watsonx.data intelligence Text2SQL API, normalizes generated SQL, and executes queries against IBM DB2.

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115.0-009688.svg)](https://fastapi.tiangolo.com)
[![License: IBM Internal](https://img.shields.io/badge/license-IBM%20Internal-blue.svg)](LICENSE)

## 📋 Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Architecture](#architecture)
- [Prerequisites](#prerequisites)
- [Local Development Setup](#local-development-setup)
  - [Using uv (Recommended)](#using-uv-recommended)
  - [Using venv (Alternative)](#using-venv-alternative)
- [Configuration](#configuration)
- [Running the Service](#running-the-service)
- [API Documentation](#api-documentation)
- [Deployment](#deployment)
- [Integration with watsonx Orchestrate](#integration-with-watsonx-orchestrate)
- [Troubleshooting](#troubleshooting)
- [Project Structure](#project-structure)

---

## Overview

This service provides a REST API that:

1. **Accepts natural language questions** about structured data
2. **Calls IBM watsonx.data intelligence Text2SQL API** to generate SQL
3. **Normalizes the SQL** (removes markdown, semicolons, prefixes, etc.)
4. **Executes the query** against IBM DB2 (optional)
5. **Returns results** in a clean JSON format

Designed for deployment on **IBM Code Engine** and integration with **watsonx Orchestrate** as either an OpenAPI tool or MCP server.

---

## Features

- Natural language to SQL conversion using IBM watsonx.data intelligence
- Automatic SQL normalization for DB2 compatibility
- Optional query execution with result streaming
- IAM token caching (20-minute TTL)
- DB2 connection pooling
- OpenAPI 3.0 specification for easy integration
- Health check endpoints with DB2 connectivity verification
- Comprehensive error handling and logging
- CORS support for web applications
- API key authentication

---

## Architecture

```
┌─────────────────┐
│   User/Agent    │
└────────┬────────┘
         │ Natural Language Question
         ▼
┌─────────────────────────────────────┐
│  FastAPI Service (this repo)        │
│  ┌─────────────────────────────┐   │
│  │ 1. Authenticate (APP-API-KEY)│   │
│  └─────────────────────────────┘   │
│  ┌─────────────────────────────┐   │
│  │ 2. Call WDI Text2SQL API    │───┼──► IBM watsonx.data intelligence
│  └─────────────────────────────┘   │
│  ┌─────────────────────────────┐   │
│  │ 3. Normalize SQL            │   │
│  └─────────────────────────────┘   │
│  ┌─────────────────────────────┐   │
│  │ 4. Execute SQL (optional)   │───┼──► IBM DB2 Database
│  └─────────────────────────────┘   │
│  ┌─────────────────────────────┐   │
│  │ 5. Return JSON Response     │   │
│  └─────────────────────────────┘   │
└─────────────────────────────────────┘
```

---

## Prerequisites

### Required Software

1. **Python 3.11 or higher**
   ```bash
   python3 --version  # Should be 3.11+
   ```

2. **Java Runtime Environment (JRE) 8 or higher**
   - Required by JPype1/jaydebeapi for DB2 JDBC connectivity
   ```bash
   java -version  # Should be 1.8+
   ```
   
   **Installation:**
   - **macOS**: `brew install openjdk@11`
   - **Ubuntu/Debian**: `sudo apt-get install default-jre`
   - **RHEL/CentOS**: `sudo yum install java-11-openjdk`

3. **uv (Recommended)** or **pip**
   ```bash
   # Install uv (fast Python package installer)
   curl -LsSf https://astral.sh/uv/install.sh | sh
   
   # Or use pip (traditional method)
   python3 -m pip --version
   ```

### Required Files

4. **IBM DB2 JDBC Driver (`db2jcc4.jar`)**
   - Download from: [IBM DB2 JDBC Driver Downloads](https://www.ibm.com/support/pages/db2-jdbc-driver-versions)
   - Place the `db2jcc4.jar` file in the root directory of this project
   - I already downloaded the file in [db2jcc4.jar](./db2jcc4.jar)

### Required Credentials

5. **IBM Cloud API Key** with access to:
   - IBM watsonx.data intelligence (WDI)
   - IBM DB2 database

6. **watsonx.data intelligence Project/Catalog ID**
   - The container ID that holds your IKC metadata catalog with enriched DB2 schema

7. **IBM DB2 Database Credentials**
   - Hostname, port, database name, schema, username, and password

---

## Local Development Setup

### Using uv

[uv](https://github.com/astral-sh/uv) is a fast Python package installer and resolver, written in Rust. It's significantly faster than pip and provides better dependency resolution.

#### 1. Install uv

```bash
# macOS/Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows (PowerShell)
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

# Verify installation
uv --version
```

#### 2. Clone the Repository

```bash
git clone git@github.com:hitalukder/data-engineering.git
cd data-engineering/watsonx.data/text-to-sql
```

#### 3. Download DB2 JDBC Driver

Place `db2jcc4.jar` it in the project root:

```bash
# Verify the file is present
ls -lh db2jcc4.jar
```

#### 4. Create Virtual Environment with uv

```bash
# Create a virtual environment with Python 3.11+
uv venv --python 3.11

# Activate the virtual environment
# macOS/Linux:
source .venv/bin/activate

# Windows:
.venv\Scripts\activate
```

#### 5. Install Dependencies with uv

```bash
# Install all dependencies (much faster than pip!)
uv pip install -r requirements.txt

# Verify installation
uv pip list
```

#### 6. Configure Environment Variables

```bash
# Copy the example environment file
cp .env.example .env

# Edit .env with your credentials
nano .env  # or use your preferred editor
```

See [Configuration](#configuration) section for detailed environment variable descriptions.

#### 7. Run the Service

```bash
# Start the FastAPI server
python app.py

# The service will be available at:
# http://localhost:4050
```

#### 8. Access API Documentation

Open your browser and navigate to:
- **Swagger UI**: http://localhost:4050/docs
- **ReDoc**: http://localhost:4050/redoc
- **OpenAPI JSON**: http://localhost:4050/openapi.json

---

## Configuration

### Environment Variables

Create a `.env` file in the project root (copy from `.env.example`):

```bash
cp .env.example .env
```

#### Required Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `IBM_CLOUD_API_KEY` | IBM Cloud API key for IAM authentication | `your_ibm_cloud_api_key_here` |
| `WXD_CONTAINER_ID` | watsonx.data intelligence project or catalog ID | `03ab6ef6-4bf9-40a8-84cb-755b427989ed` |
| `WXD_CONTAINER_TYPE` | Container type: `project` or `catalog` | `project` |
| `WXD_MODEL_ID` | Foundation model for SQL generation | `meta-llama/llama-3-3-70b-instruct` |
| `WXD_TEXT2SQL_BASE` | WDI Text2SQL API base URL | `https://api.dataplatform.cloud.ibm.com/semantic_automation/v1/text_to_sql` |
| `DB2_HOSTNAME` | IBM DB2 hostname | `your-db2-host.databases.appdomain.cloud` |
| `DB2_PORT` | DB2 port (typically 50001 for SSL) | `50001` |
| `DB2_DATABASE` | Database name | `BLUDB` |
| `DB2_SCHEMA` | Default schema | `BGD20177` |
| `DB2_USERNAME` | DB2 username | `your_db2_user` |
| `DB2_PASSWORD` | DB2 password | `your_db2_password` |
| `APP_API_KEY` | Shared secret for API authentication | `your_strong_random_secret_here` |

#### Optional Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `SERVICE_BASE_URL` | Base URL for OpenAPI spec (set after deployment) | `http://localhost:4050` |

### Example .env File

```bash
# IBM Cloud IAM
IBM_CLOUD_API_KEY=your_ibm_cloud_api_key_here

# watsonx.data intelligence Text2SQL
WXD_CONTAINER_ID=
WXD_CONTAINER_TYPE=project
WXD_MODEL_ID=meta-llama/llama-3-3-70b-instruct
WXD_TEXT2SQL_BASE=https://api.dataplatform.cloud.ibm.com/semantic_automation/v1/text_to_sql

# IBM DB2 Connection
DB2_HOSTNAME=your-db2-host.databases.appdomain.cloud
DB2_PORT=50001
DB2_DATABASE=BLUDB
DB2_SCHEMA=BGD20177
DB2_USERNAME=your_db2_user
DB2_PASSWORD=your_db2_password

# Service Security
APP_API_KEY=your_strong_random_secret_here

# OpenAPI Base URL (for Orchestrate import)
SERVICE_BASE_URL=http://localhost:4050
```

---

## Running the Service

### Development Mode (with auto-reload)

```bash
# Activate virtual environment first
source .venv/bin/activate  # macOS/Linux
# or
.venv\Scripts\activate  # Windows

# Run with auto-reload
python app.py
```

The service will start on `http://localhost:4050` with auto-reload enabled.

### Production Mode (with uvicorn)

```bash
# Run with multiple workers
uvicorn app:app --host 0.0.0.0 --port 4050 --workers 4
```

### Using Docker (Local)

```bash
# Build the image
docker build -t text2sql-service:latest .

# Run the container
docker run -p 4050:4050 --env-file .env text2sql-service:latest
```

---

## API Documentation

### Testing with curl

```bash
# Test the health endpoint
curl http://localhost:4050/health

# Test text-to-sql (without execution)
curl -X POST http://localhost:4050/texttosql \
  -H "Content-Type: application/json" \
  -H "APP-API-KEY: your_api_key_here" \
  -d '{
    "question": "Show me all tasks in project ALPHA",
    "db_execute": false
  }'

# Test text-to-sql (with execution)
curl -X POST http://localhost:4050/texttosql \
  -H "Content-Type: application/json" \
  -H "APP-API-KEY: your_api_key_here" \
  -d '{
    "question": "How many tasks are overdue?",
    "db_execute": true
  }'
```

## Deployment

### Deploy to IBM Code Engine

#### Prerequisites

- IBM Cloud CLI installed
- Code Engine plugin installed
- IBM Container Registry namespace created
- Logged into IBM Cloud

#### Automated Deployment

```bash
# 1. Configure deployment variables in .env
# Add these to your .env file:
ICR_NAMESPACE=your-icr-namespace
CE_PROJECT_NAME=your-code-engine-project
CE_REGION=us-south

# 2. Make deploy script executable
chmod +x deploy.sh

# 3. Run deployment
./deploy.sh
```

The script will:
1. Log into IBM Container Registry
2. Build the Docker image (linux/amd64)
3. Push to ICR
4. Create or update Code Engine application
5. Configure all environment variables
6. Output the live service URL

#### Manual Deployment

```bash
# 1. Build and push Docker image
docker build --platform linux/amd64 -t icr.io/<namespace>/text2sql-service:latest .
docker push icr.io/<namespace>/text2sql-service:latest

# 2. Create Code Engine application
ibmcloud ce application create \
  --name text2sql-service \
  --image icr.io/<namespace>/text2sql-service:latest \
  --port 4050 \
  --min-scale 1 \
  --max-scale 5 \
  --cpu 1 \
  --memory 4G \
  --env IBM_CLOUD_API_KEY=<your-api-key> \
  --env WXD_CONTAINER_ID=<your-container-id> \
  --env WXD_CONTAINER_TYPE=project \
  --env WXD_MODEL_ID=meta-llama/llama-3-3-70b-instruct \
  --env DB2_HOSTNAME=<your-db2-host> \
  --env DB2_PORT=50001 \
  --env DB2_DATABASE=<your-database> \
  --env DB2_SCHEMA=<your-schema> \
  --env DB2_USERNAME=<your-username> \
  --env DB2_PASSWORD=<your-password> \
  --env APP_API_KEY=<your-app-api-key>

# 3. Get the application URL
ibmcloud ce application get --name text2sql-service
```

---

## Integration with watsonx Orchestrate

### Option A: OpenAPI Tool

#### 1. Generate OpenAPI Spec

```bash
# Get the live OpenAPI spec from your deployed service
curl https://your-service-url.codeengine.appdomain.cloud/openapi.json > openapi.json

# Or use the local service
curl http://localhost:4050/openapi.json > openapi.json
```

#### 2. Import to Orchestrate

```bash
# Using Orchestrate CLI
orchestrate tools import --kind openapi --file openapi.json

# Or import via UI:
# 1. Go to watsonx Orchestrate
# 2. Navigate to Tools → Import Tool
# 3. Select OpenAPI
# 4. Upload openapi.json
```

#### 3. Configure Credentials

In watsonx Orchestrate UI:
1. Go to **Tools** → **text2sql-service**
2. Click **Credentials**
3. Add header authentication:
   - **Header Name**: `APP-API-KEY`
   - **Header Value**: `<your APP_API_KEY from .env>`

### Option B: MCP Server

#### 1. Install MCP Dependencies

```bash
# Using uv
uv pip install mcp starlette

# Or using pip
pip install mcp starlette
```

#### 2. Run as MCP Server

```bash
# Run as SSE MCP server
python mcp_server.py --sse --port 4052
```

#### 3. Add to Orchestrate

In watsonx Orchestrate:
1. Go to **MCP Servers** → **Add Server**
2. Configure:
   - **URL**: `https://your-service-url.codeengine.appdomain.cloud/sse`
   - **Name**: `ibm-text2sql`
   - **Authentication**: Add `APP-API-KEY` header

### Agent Configuration (YAML)

Create an agent that uses the text2sql tool:

```yaml
name: academic_text_to_sql_agent

description: >
Specialist agent for academic and student information system data queries.
Use when the user asks about students, courses, enrollments, grades,
teachers, academic performance, attendance, departments, or anything
stored in relational academic databases.

model: ibm/granite-13b-chat-v2

instructions: |
You are a Text-to-SQL specialist for academic and educational data.

When a user asks a question about structured academic data:

1. Call the texttosql tool with db_execute=true to retrieve actual data.
2. Use dialect=db2 for all queries.
3. Generate SQL only from the available database schema.
4. Summarize query_results in clear, student-friendly language.
5. Always report the number of records returned (row_count).
6. If the query returns no data, explain that no matching records were found.
7. If the query fails, explain the error and suggest alternative questions.
8. When appropriate, provide key insights such as averages, rankings, trends, or top performers.
9. Never invent data that is not returned from the database.
10. If a request is ambiguous, ask a clarifying question before generating SQL.

Example questions you can answer:

Student Information:

* "Show all students in grade 11"
* "How many students are enrolled this semester?"
* "List students born after 2008"

Course Information:

* "What courses are offered by the Computer Science department?"
* "Who teaches Introduction to Programming?"
* "How many students are enrolled in Algebra II?"

Enrollment Queries:

* "Which courses is Emma Johnson taking?"
* "Show all students enrolled in Physics"
* "List students taking more than three courses"

Academic Performance:

* "What is the average score for Physics?"
* "Who are the top 10 students by average grade?"
* "Which students received an A in Data Structures?"
* "Show failing students"

Faculty and Department Queries:

* "Which teacher teaches the most courses?"
* "Show all teachers in the Science department"
* "Which department has the highest student enrollment?"

Reporting and Analytics:

* "What are the most popular courses?"
* "Show course enrollment statistics"
* "Generate a student performance summary"
* "Which course has the highest average grade?"

tools:

* text2sql-service/texttosql

```

---

## Troubleshooting

### Common Issues

#### 1. Java Not Found

**Error**: `RuntimeError: No Java runtime present`

**Solution**:
```bash
# macOS
brew install openjdk@11
export JAVA_HOME=$(/usr/libexec/java_home -v 11)

# Ubuntu/Debian
sudo apt-get install default-jre
export JAVA_HOME=/usr/lib/jvm/default-java

# Verify
java -version
echo $JAVA_HOME
```

#### 2. DB2 JDBC Driver Missing

**Error**: `FileNotFoundError: db2jcc4.jar not found`

**Solution**:
1. Download `db2jcc4.jar` from [IBM Support](https://www.ibm.com/support/pages/db2-jdbc-driver-versions)
2. Place it in the project root directory
3. Verify: `ls -lh db2jcc4.jar`

#### 3. DB2 Connection Failed

**Error**: `jaydebeapi.DatabaseError: Connection refused`

**Solution**:
- Verify DB2 credentials in `.env`
- Check DB2 hostname and port are correct
- Ensure DB2 allows connections from your IP
- Test connectivity: `telnet <DB2_HOSTNAME> <DB2_PORT>`
- Check firewall rules

#### 4. IAM Token Expired

**Error**: `401 Unauthorized` from WDI API

**Solution**:
- Verify `IBM_CLOUD_API_KEY` is valid
- Check API key has access to watsonx.data intelligence
- Token is automatically refreshed every 20 minutes
- Restart the service to force token refresh

#### 5. WDI API Returns No SQL

**Error**: `"generated_queries": []`

**Solution**:
- Verify `WXD_CONTAINER_ID` is correct
- Check the container has enriched metadata
- Ensure the question relates to tables in the schema
- Try rephrasing the question
- Set `raw_output=true` to see WDI's full response

#### 6. SQL Execution Fails

**Error**: `SQL execution failed: [SQL0204] TABLE not found`

**Solution**:
- Verify `DB2_SCHEMA` is correct in `.env`
- Check table names match the schema
- Ensure user has SELECT permissions
- Review the normalized SQL in the response

#### 7. Port Already in Use

**Error**: `OSError: [Errno 48] Address already in use`

**Solution**:
```bash
# Find process using port 4050
lsof -i :4050

# Kill the process
kill -9 <PID>

# Or use a different port
uvicorn app:app --port 4051
```

### Debug Mode

Enable detailed logging:

```bash
# Set log level to DEBUG
export LOG_LEVEL=DEBUG

# Run the service
python app.py
```

### Health Check

```bash
# Check service health
curl http://localhost:4050/health | jq

# Expected response:
# {
#   "service": "IBM watsonx Text-to-SQL",
#   "status": "ok",
#   "db2_connected": true,
#   "db2_error": null,
#   "iam_token_cached": true
# }
```

### Logs

```bash
# View logs in real-time
tail -f logs/app.log

# Search for errors
grep ERROR logs/app.log

# Check DB2 connection logs
grep "DB2 connection" logs/app.log
```

---

## Project Structure

```
text2sql-service/
├── app.py                      # Main FastAPI application
├── mcp_server.py               # MCP server wrapper (optional)
├── requirements.txt            # Python dependencies
├── .env.example                # Environment variables template
├── .env                        # Your local configuration (git-ignored)
├── db2jcc4.jar                 # IBM DB2 JDBC driver (download separately)
├── Dockerfile                  # Multi-stage Docker build
├── deploy.sh                   # Automated Code Engine deployment script
├── openapi.json                # Generated OpenAPI specification
├── README.md                   # This file
│
├── customTypes/                # Pydantic models
│   ├── __init__.py
│   ├── texttosqlRequest.py     # Request model
│   └── texttosqlResponse.py    # Response models
│
└── .venv/                      # Virtual environment (git-ignored)
```

### Key Files

- **`app.py`**: Main FastAPI application with all endpoints and business logic
- **`requirements.txt`**: Python dependencies (FastAPI, uvicorn, jaydebeapi, etc.)
- **`.env`**: Local configuration (credentials, endpoints, etc.)
- **`db2jcc4.jar`**: IBM DB2 JDBC driver (required for database connectivity)
- **`Dockerfile`**: Multi-stage build for production deployment
- **`deploy.sh`**: Automated deployment to IBM Code Engine

---

## Contributing

This is an internal IBM project. For questions or issues, contact the Build Engineering team.

---

## License

IBM Internal Use Only

---

## Support

For issues or questions:
- **Internal**: Contact IBM Build Engineering team
- **Documentation**: See inline code comments in `app.py`
- **API Docs**: http://localhost:4050/docs (when running locally)

---

## Changelog

### Version 1.0.0 (Current)
- Initial release
- FastAPI REST API with OpenAPI 3.0 spec
- IBM watsonx.data intelligence Text2SQL integration
- DB2 query execution
- IAM token caching
- SQL normalization
- Health check endpoints
- Docker support
- Code Engine deployment automation
