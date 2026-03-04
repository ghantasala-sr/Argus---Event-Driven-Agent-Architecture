# Argus

**Distributed multi-agent AI code review platform built on AWS.**

Argus is an event-driven, microservice-based system that acts as a senior engineer reviewing Pull Requests. When a PR is opened, 4 specialized AI agents (Security, Performance, Style, and Test Coverage) analyze the code in parallel and aggregate their findings into a single, severity-ranked comment on GitHub in under 15 seconds.

## Architecture

At its core, Argus operates on an asynchronous event fan-out model:

1. **Webhook Event**: GitHub PR triggers an API Gateway endpoint.
2. **Parser Agent**: Validates and parses the diff, chunking large files. 
3. **Parallel Fan-out**: The parsed diff is published to an SNS topic, which fans out to 4 independent SQS queues.
4. **Specialized Agents**: 
   - 🛡️ **Security**: Scans for secrets, SQLi, XSS, and logic flaws (Regex + Amazon Bedrock Nova Pro).
   - ⚡ **Performance**: Detects N+1 queries, memory leaks, and O(n²) operations (Bedrock Nova Pro).
   - 🎨 **Style**: Enforces language-specific naming, types, and docstrings (Bedrock Nova Micro).
   - 🧪 **Test Coverage**: Identifies missing tests and suggests cases (Bedrock Nova Micro).
5. **Aggregation**: A Summary Agent collects the findings from DynamoDB, deduplicates, ranks by severity, and posts a Markdown-formatted comment back to GitHub.

## Project Structure

```text
argus/
├── agents/                 # Python Lambda agent code
│   ├── parser/             # Diff parsing and extraction
│   ├── security/           # Secret scanning + deep logic review
│   ├── performance/        # Complexity and resource usage constraints
│   ├── style/              # Linting and style preference matching
│   ├── test/               # Coverage gaps and validations
│   └── shared/             # Bedrock LLM client, GitHub API, Data models
├── dashboard/              # React real-time dashboard visualization via WebSocket
├── lib/stacks/             # AWS CDK (TypeScript) infrastructure
└── tests/                  # Unit and integration tests (Pytest)
```

## Quick Start

### 1. Backend & Agent Development (Python 3.12)

```bash
# Install Python dependencies using Poetry
python3 -m venv .venv 
source .venv/bin/activate
pip install poetry
poetry install

# Run the test suite
poetry run pytest tests/ -v
```

### 2. Infrastructure Deployment (AWS CDK)

```bash
# Install CDK dependencies
cd lib
npm install

# Validate and synthesize CDK stacks
npx cdk synth

# Deploy to your AWS account
npx cdk deploy --all --context stage=dev
```

### 3. Real-time Dashboard (React)

```bash
# Run the local frontend dashboard to monitor agent queues
cd dashboard
npm install
npm run dev
```

## Continuous Integration
The `main` branch enforces strict formatting and typing checks via GitHub Actions:
- **Linting & Formatting**: Ruff and Black
- **Static Typing**: Mypy
- **Tests**: Pytest unit test coverage
- **Build**: CDK synthesis verification

## Built With
* AWS API Gateway, Lambda, SNS, SQS, DynamoDB
* Amazon Bedrock (Nova Micro, Nova Pro)
* AWS Cloud Development Kit (TypeScript)
* Python 3.12 & Pydantic
* React & Vite
