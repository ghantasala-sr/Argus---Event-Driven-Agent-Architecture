# CLAUDE.md — Argus Project Intelligence

> This file provides context to Claude Code (claude.ai/code) for working on the Argus codebase.
> It describes the project architecture, conventions, and key decisions so Claude can assist effectively.

## Project Overview

**Argus** is a distributed, event-driven multi-agent code review platform built on AWS. When a developer opens a Pull Request on GitHub, 6 specialized AI agents review the code in parallel — security, performance, style, testing — and post a single, severity-ranked review comment on the PR within ~15 seconds.

**One-liner:** Distributed multi-agent AI system that reviews PRs like a senior engineer — catching SQL injections, N+1 queries, missing tests, and style issues in parallel, in 15 seconds instead of 15 hours.

### Why This Project Exists

- Code reviews are the #1 bottleneck in developer productivity (2-24 hour wait times)
- Human reviewers catch only 15-30% of security issues in diffs
- 40-60% of review comments are repetitive style feedback
- Built as a portfolio project demonstrating distributed systems + agentic AI + AWS architecture

## Architecture

### High-Level Flow

```
GitHub PR webhook → API Gateway → SNS: pr.webhook → SQS: parse-queue
    → Parser Agent (Lambda)
        → SNS: pr.parsed (FAN-OUT to all 4 review agents in PARALLEL)
            ├── Security Agent (AgentCore Runtime / Lambda)
            ├── Performance Agent (AgentCore Runtime / Lambda)
            ├── Style Agent (Lambda)
            └── Test Agent (AgentCore Runtime / Lambda)
        → SNS: review.findings (each agent publishes independently)
            → Summary Agent (Lambda)
                → GitHub PR Comment posted
                → SNS: review.complete
                    ├── LTM Writer (Lambda) → DynamoDB + AgentCore Memory
                    └── Dashboard (WebSocket via API Gateway)
```

### Key Design Principle: Agents as Microservices

Each agent is an **independent service** that:
- Reads from its own SQS queue
- Processes independently (no shared state during review)
- Writes findings to SNS
- Can scale independently (Lambda concurrency or K8s pods)
- Can crash without affecting other agents (messages re-queue)
- Can be deployed/updated independently

### Transport Abstraction

**CRITICAL DESIGN DECISION:** Agents never import SQS or Kafka directly. They use `shared/transport.py` which provides an `AgentTransport` interface. The backend (SQS or Kafka) is selected via `TRANSPORT_TYPE` environment variable.

```python
# ALWAYS use this pattern — never call boto3 SQS/SNS directly from agent code
from shared.transport import get_transport
transport = get_transport()  # reads TRANSPORT_TYPE env var
events = transport.consume()
transport.publish(topic, event)
transport.ack(receipt)
```

This enables migration from free-tier (SNS/SQS) to production (Kafka/MSK) with zero agent code changes.

## Project Structure

```
argus/
├── bin/
│   └── argus.ts                     # CDK app entry point
├── lib/
│   └── stacks/
│       ├── messaging-stack.ts       # SNS topics + SQS queues + DLQs
│       ├── agents-stack.ts          # Lambda functions per agent
│       ├── agentcore-stack.ts       # AgentCore Runtime, Memory, Gateway, Identity
│       ├── storage-stack.ts         # DynamoDB tables + S3 bucket
│       ├── api-stack.ts             # API Gateway (webhook + WebSocket)
│       └── monitoring-stack.ts      # CloudWatch alarms + dashboards
├── agents/
│   ├── shared/
│   │   ├── transport.py             # AgentTransport abstraction (SQS/Kafka)
│   │   ├── models.py                # Pydantic event schemas (ALL events)
│   │   ├── github_client.py         # GitHub API wrapper (diff, comments, status)
│   │   ├── bedrock_client.py        # Nova Micro/Lite/Pro client with model routing
│   │   └── config.py                # Environment variable loading + validation
│   ├── parser/
│   │   ├── handler.py               # Lambda entry point (SQS trigger)
│   │   ├── agent.py                 # Diff parsing, language detection, chunking
│   │   └── tests/
│   ├── security/
│   │   ├── handler.py               # Lambda/AgentCore entry point
│   │   ├── agent.py                 # Semgrep + Bandit + secret scan + Nova Pro
│   │   ├── rules/                   # Custom Semgrep rules (YAML)
│   │   ├── Dockerfile               # For AgentCore Runtime deployment
│   │   └── tests/
│   ├── performance/
│   │   ├── handler.py
│   │   ├── agent.py                 # AST parsing + N+1 detection + complexity
│   │   ├── Dockerfile
│   │   └── tests/
│   ├── style/
│   │   ├── handler.py
│   │   ├── agent.py                 # Naming + types + docstrings + imports
│   │   └── tests/
│   ├── test_coverage/
│   │   ├── handler.py
│   │   ├── agent.py                 # Coverage gaps + test case suggestions
│   │   ├── Dockerfile
│   │   └── tests/
│   ├── summary/
│   │   ├── handler.py
│   │   ├── agent.py                 # Aggregate + deduplicate + rank + format
│   │   ├── templates/               # GitHub comment markdown templates
│   │   └── tests/
│   └── ltm_writer/
│       ├── handler.py               # Pattern extraction + memory storage
│       └── tests/
├── dashboard/
│   ├── src/                         # React app (WebSocket real-time view)
│   └── package.json
├── demo/
│   ├── branches/                    # Pre-built demo PR branches
│   │   ├── vulnerable-code/
│   │   ├── clean-code/
│   │   └── team-patterns/
│   └── setup.sh                     # Seeds DynamoDB + LTM for demos
├── tests/
│   ├── unit/                        # Per-agent tests with mock events
│   ├── integration/                 # End-to-end with LocalStack
│   └── load/                        # Artillery load tests
├── .github/
│   └── workflows/
│       └── test.yml                 # PR: lint + type check + unit tests (GitHub Actions)
├── cdk.json
├── pyproject.toml
├── CLAUDE.md                        # ← You are here
├── GEMINI.md
└── README.md
```

## Technology Stack

| Layer | Technology | Notes |
|-------|-----------|-------|
| **IaC** | AWS CDK (TypeScript) | All infrastructure as code, no manual console work |
| **Agent Runtime** | AWS Lambda + AgentCore Runtime | Lambda for simple agents, AgentCore for container agents |
| **LLM** | Amazon Nova Micro/Lite/Pro via Bedrock | Micro: style (cheap), Lite: parser/test/summary, Pro: security/perf |
| **Message Bus** | SNS (fan-out) + SQS (queues) + DLQ | Free tier. Transport abstraction supports Kafka swap |
| **API** | API Gateway (REST + WebSocket) | Webhook endpoint + dashboard real-time updates |
| **Database** | DynamoDB | Reviews, findings, team patterns, agent decisions |
| **Tool Access** | AgentCore Gateway (MCP) | GitHub API, Semgrep, Bandit exposed as MCP tools |
| **Memory** | AgentCore Memory (STM + LTM) | Session checkpoints + team coding pattern learning |
| **Code Exec** | AgentCore Code Interpreter | Semgrep, Bandit, AST parsing in sandboxed env |
| **Auth** | AgentCore Identity + GitHub App | GitHub installation tokens, repo-level permissions |
| **Observability** | AgentCore Observability + CloudWatch | OTEL traces, review latency, accuracy metrics |
| **Language** | Python 3.12 | All agent code |

## Coding Conventions

### Python Style

- **Formatter:** Black (line length 100)
- **Linter:** Ruff
- **Type hints:** Required on all public functions
- **Docstrings:** Google style on all public functions and classes
- **Naming:** snake_case for variables/functions, PascalCase for classes
- **Imports:** stdlib → third-party → local, sorted within groups

### Event Schemas

ALL events use Pydantic models defined in `agents/shared/models.py`. Every event MUST include:

```python
class BaseEvent(BaseModel):
    event_type: str                    # e.g., "review.security"
    review_id: str                     # Correlation key across all events
    timestamp: datetime                # ISO 8601 UTC
    trace_id: str                      # OTEL trace ID
    agent_meta: AgentMeta              # model, tokens, latency

class AgentMeta(BaseModel):
    agent: str                         # "security", "performance", etc.
    model: str                         # "nova-micro", "nova-lite", "nova-pro"
    tokens_in: int
    tokens_out: int
    latency_ms: int
    tools_called: list[str] = []
```

### Agent Handler Pattern

Every agent Lambda handler follows this exact pattern:

```python
# <agent>/handler.py
import json
from shared.transport import get_transport
from shared.models import ParsedPREvent, SecurityReviewEvent
from security.agent import SecurityAgent

agent = SecurityAgent(...)
transport = get_transport()

def handler(event, context):
    for record in event['Records']:
        parsed_pr = ParsedPREvent(**json.loads(record['body']))
        result: SecurityReviewEvent = agent.process(parsed_pr)
        transport.publish('review.findings', result.model_dump())
    return {'statusCode': 200}
```

**DO NOT** put business logic in handler.py. Handlers are thin wrappers. All logic lives in agent.py.

### DynamoDB Schema

Single-table design with these access patterns:

```
PK                      SK                          Data
──────────────────────  ──────────────────────────  ────────────────────────
REV#<review_id>         META                        pr_url, status, created_at
REV#<review_id>         FINDING#security#<n>        severity, category, file, line, message
REV#<review_id>         FINDING#performance#<n>     severity, category, file, line, message
REV#<review_id>         FINDING#style#<n>           severity, category, file, line, message
REV#<review_id>         FINDING#test#<n>            severity, category, file, line, message
TEAM#<repo_owner>       PATTERN#<hash>              style_prefs, suppressions, hot_paths
TEAM#<repo_owner>       ACCURACY#<month>            accepted, dismissed, false_positive_rate
```

### Error Handling

- Every SQS queue has a Dead Letter Queue (maxReceiveCount: 3)
- Agent code MUST handle exceptions gracefully — catch, log, and let the message retry
- After 3 failures, message goes to DLQ → CloudWatch Alarm → alert
- NEVER let an unhandled exception crash the Lambda — it wastes retries

### Environment Variables

Every agent reads config from environment variables (injected by CDK):

```
TRANSPORT_TYPE=sqs|kafka           # Transport backend selection
INPUT_QUEUE_URL=<sqs-queue-url>    # Agent's input queue
INCIDENTS_TRIAGED_TOPIC_ARN=<arn>  # Output SNS topic (naming follows topic name)
DYNAMODB_TABLE=argus-reviews       # Main DynamoDB table
MODEL_ID=amazon.nova-pro-v1:0     # Bedrock model for this agent
MEMORY_ID=<agentcore-memory-id>    # AgentCore Memory identifier
GITHUB_APP_ID=<app-id>            # GitHub App for API access
GITHUB_PRIVATE_KEY_SECRET=<arn>    # Secrets Manager ARN for GitHub App key
```

## Testing

### Unit Tests

```bash
# Run all unit tests
pytest agents/tests/unit/ -v

# Run specific agent tests
pytest agents/security/tests/ -v

# With coverage
pytest --cov=agents --cov-report=html
```

- Mock ALL external services (Bedrock, DynamoDB, SQS, GitHub API)
- Test with realistic event payloads (see `tests/fixtures/`)
- Every agent must have tests for: happy path, edge cases, error handling

### Integration Tests

```bash
# Requires LocalStack running
docker-compose up -d localstack
pytest tests/integration/ -v
```

### Local Development

```bash
# Install dependencies
poetry install

# Run locally with SAM (single agent)
sam local invoke ParserFunction -e tests/fixtures/pr_webhook_event.json

# Run full pipeline with LocalStack
docker-compose up  # starts LocalStack + local agents
```

## CI/CD — GitHub Actions for Tests Only

Deployment is manual (`cdk deploy` from terminal). GitHub Actions runs tests on every PR to keep the repo clean and show green checkmarks.

### `.github/workflows/test.yml`

```yaml
name: Test
on:
  pull_request:
    branches: [main]
  push:
    branches: [main]

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install ruff black
      - run: ruff check agents/
      - run: black --check agents/

  typecheck:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install poetry && poetry install
      - run: poetry run mypy agents/shared/

  unit-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install poetry && poetry install
      - run: poetry run pytest agents/ -v --cov=agents --cov-report=xml
      - uses: codecov/codecov-action@v4  # optional: coverage badge

  cdk-synth:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: "20" }
      - run: cd lib && npm install && npx cdk synth
```

**4 parallel jobs:** lint, typecheck, unit tests, CDK validation. Runs in ~60 seconds total.

**Free tier:** 2000 min/month. We'll use ~50 min/month.

### Deployment (Manual from Terminal)

```bash
# Deploy all stacks to dev
cd lib && npx cdk deploy --all --context stage=dev

# Deploy single stack (when only one agent changed)
npx cdk deploy ArgusAgentsStack --context stage=dev

# Destroy (cleanup)
npx cdk destroy --all --context stage=dev
```

**Why no CodePipeline:** Solo developer building a portfolio project. Manual `cdk deploy` from terminal is faster and simpler. CI/CD pipelines solve team problems we don't have.

## Key Decisions to Remember

1. **Transport abstraction is sacred** — NEVER bypass it. All inter-agent communication goes through `AgentTransport`.

2. **Agents are stateless** — No shared memory between agents during a review. Each agent gets the full ParsedPREvent and works independently. State aggregation happens only in Summary Agent via DynamoDB.

3. **Fan-out via SNS, buffering via SQS** — SNS: pr.parsed fans out to 4 SQS queues (one per review agent). This gives us parallel processing + independent failure handling + DLQ per agent.

4. **Model selection is cost-driven** — Style Agent uses Nova Micro ($0.035/M tokens) because it's just classification. Security/Performance use Nova Pro ($0.80/M tokens) because they need deep reasoning. Don't upgrade a model without justifying the cost.

5. **LTM learns team patterns, not universal rules** — AgentCore Memory stores per-team preferences (naming conventions, suppressed warnings, hot paths). Don't hardcode team-specific rules.

6. **GitHub comment is the product** — The PR comment must be clear, actionable, and severity-ranked. If the comment is noisy or unhelpful, nothing else matters.

7. **DLQ = oncall alert** — Every DLQ message triggers a CloudWatch Alarm. Failed reviews must be visible, not silently lost.

8. **Code Interpreter for real analysis** — Security findings from Semgrep/Bandit are more reliable than LLM pattern matching. Use Code Interpreter for static analysis, LLM for contextual reasoning on top.

## Common Tasks

### Adding a New Agent

1. Create `agents/<new_agent>/` with `handler.py`, `agent.py`, `tests/`
2. Add Pydantic event model to `shared/models.py`
3. Add SQS queue + DLQ + subscription to SNS topic in `messaging-stack.ts`
4. Add Lambda function in `agents-stack.ts`
5. Add CloudWatch alarm for DLQ in `monitoring-stack.ts`

### Adding a New Semgrep Rule

1. Add YAML rule to `agents/security/rules/`
2. Rule runs automatically — Code Interpreter loads all rules from the directory
3. Add test case in `agents/security/tests/` with code that should trigger the rule

### Modifying the GitHub Comment Format

1. Edit templates in `agents/summary/templates/`
2. The summary agent uses Jinja2 templates for the markdown output
3. Test by opening a real PR on the test repo

## Development Phases

This project is built incrementally across 6 phases over ~11 weeks. Each phase produces a working, testable deliverable. **Do not skip ahead** — each phase builds on the previous one. When assisting with code, check which phase we are in and only use services/agents that exist in that phase.

### Phase 1 — Webhook + Parser + GitHub Integration (Week 1-2)

**Goal:** GitHub PR webhook triggers Parser Agent, which publishes a structured diff event.

**What gets built:**
- CDK: `api-stack` — API Gateway POST `/webhook` → SNS: `pr.webhook`
- CDK: `messaging-stack` — SNS (`pr.webhook`, `pr.parsed`) + SQS (`parse-queue` + `parse-dlq`)
- CDK: `storage-stack` — DynamoDB `argus-reviews` table
- GitHub App: configure webhook for `pull_request` events (opened, synchronize)
- `shared/transport.py` — `SQSTransport` class implementing `AgentTransport` interface
- `shared/models.py` — `PRWebhookEvent`, `ParsedPREvent` Pydantic models
- `shared/github_client.py` — fetch diff, file list, PR metadata using GitHub App installation token
- `shared/config.py` — environment variable loader with validation
- `parser/handler.py` — Lambda SQS trigger (thin wrapper)
- `parser/agent.py` — parse diff into structured format, detect languages, filter generated files, chunk large diffs
- Unit tests with mock webhook payloads and mock GitHub API responses

**Deliverable:** Open a PR on test repo → Parser Agent publishes `ParsedPREvent` to SNS: `pr.parsed`

**Services available in this phase:**
- API Gateway, SNS (2 topics), SQS (1 queue + 1 DLQ), Lambda (1 function), DynamoDB
- GitHub API (read-only: diff, files, metadata)
- NO Bedrock, NO AgentCore yet — Parser uses deterministic logic only

**CDK deploy command:**
```bash
npx cdk deploy ArgusApiStack ArgusMessagingStack ArgusStorageStack ArgusAgentsStack --context stage=dev
```

---

### Phase 2 — Security Agent + Code Interpreter (Week 3-4)

**Goal:** Security Agent reviews code using Semgrep, Bandit, and Nova Pro, then publishes findings.

**What gets built:**
- CDK: add SQS `security-queue` + `security-dlq` ← SNS: `pr.parsed` subscription
- CDK: add SNS `review.findings` topic
- AgentCore Code Interpreter: setup Semgrep (OWASP rules) + Bandit in sandbox
- AgentCore Gateway (MCP): register GitHub API as MCP tool
- AgentCore Browser: CVE database check for new dependencies
- `shared/bedrock_client.py` — Bedrock Converse API wrapper with model routing
- `security/agent.py`:
  - Run Semgrep with OWASP Top 10 rules via Code Interpreter
  - Run Bandit for Python-specific security checks via Code Interpreter
  - Regex scan for hardcoded secrets (API keys, passwords, tokens)
  - Nova Pro: analyze complex patterns Semgrep misses (business logic flaws)
  - Check new dependencies against CVE databases via Browser
  - Classify findings: CRITICAL / WARNING / INFO with file + line number
- `security/handler.py` — Lambda/AgentCore entry point
- `security/rules/` — custom Semgrep YAML rules
- `security/Dockerfile` — for AgentCore Runtime deployment (optional, can use Lambda)
- `shared/models.py` — add `SecurityReviewEvent`, `Finding` models
- Unit tests with code snippets containing known vulnerabilities (SQL injection, XSS, secrets)

**Deliverable:** Push PR with SQL injection → Security Agent catches it with exact line number and fix suggestion

**New services in this phase:**
- Amazon Bedrock (Nova Pro)
- AgentCore Code Interpreter, Gateway (MCP), Browser
- SNS: `review.findings` topic + SQS: `security-queue`

---

### Phase 3 — Performance + Style + Test Agents (Week 5-6)

**Goal:** All 4 review agents run in parallel. SNS fan-out from `pr.parsed` triggers all simultaneously.

**What gets built:**
- CDK: add SQS queues for `performance-queue`, `style-queue`, `test-queue` (each with DLQ)
- CDK: add SNS: `pr.parsed` subscriptions for all 3 new queues (fan-out)
- `performance/agent.py`:
  - Code Interpreter: parse AST of changed files
  - Detect N+1 query patterns (DB call inside loop)
  - Detect O(n²) algorithms (nested loops over same collection)
  - Detect memory issues (loading full dataset, missing cleanup)
  - Nova Pro: contextual analysis (is this a hot path or one-time script?)
- `performance/Dockerfile` — for AgentCore Runtime
- `style/agent.py`:
  - Check naming conventions (snake_case vs camelCase per language)
  - Check missing type hints, docstrings, import organization
  - AgentCore Memory LTM: apply team-specific style preferences
  - Nova Micro: fast classification (cheapest model — style doesn't need deep reasoning)
- `test_coverage/agent.py`:
  - Identify new/modified functions without corresponding tests
  - Nova Lite: generate 2-3 suggested test cases (happy path, edge case, error case)
  - Code Interpreter: validate suggested tests actually parse
- `test_coverage/Dockerfile` — for AgentCore Runtime
- AgentCore Memory LTM: initial setup for team pattern storage
- Handlers for all 3 agents
- Unit tests for each agent

**Deliverable:** Push PR → all 4 agents review in parallel → 4 independent `FindingsEvent` messages in SNS: `review.findings`

**New services in this phase:**
- Amazon Bedrock (Nova Micro for style, Nova Lite for tests — in addition to Pro for security/perf)
- AgentCore Memory (LTM for team patterns)
- 3 new SQS queues + DLQs

**Critical test:** Verify fan-out timing — all 4 agents should START within 1 second of each other, not sequentially.

---

### Phase 4 — Summary Agent + GitHub Comment (Week 7-8)

**Goal:** Complete end-to-end pipeline. PR → parallel review → single GitHub comment with severity-ranked findings.

**What gets built:**
- CDK: add SQS `summary-queue` + `summary-dlq` ← SNS: `review.findings` subscription
- CDK: add SNS: `review.complete` topic
- `summary/agent.py`:
  - Aggregation pattern: poll DynamoDB for findings from all 4 agents
  - Wait strategy: poll every 2s, timeout at 120s, proceed with partial results if timeout
  - Deduplicate overlapping findings (security + performance may flag same line)
  - Rank all findings: CRITICAL → WARNING → INFO
  - AgentCore Memory LTM: check team preferences (suppressed warnings, custom rules)
  - Determine verdict: any CRITICAL → Request Changes; only WARNING/INFO → Comment; none → Approve
  - Format as GitHub-flavored markdown using Jinja2 templates
  - GitHub API: post review comment on PR
  - GitHub API: set commit status (pass/fail)
- `summary/templates/` — Jinja2 templates for the PR comment markdown
  - `review_comment.md.j2` — main review template with collapsible sections
  - `finding_critical.md.j2` — critical finding format with code block + suggestion
  - `finding_warning.md.j2` — warning format
  - `finding_info.md.j2` — info format (inside <details> collapse)
- `shared/github_client.py` — add `post_review()`, `set_commit_status()` methods
- `ltm_writer/handler.py` — store review patterns after completion:
  - Extract common finding categories per repo
  - Track accepted vs dismissed findings (if developer pushes fix vs ignores)
  - Store in AgentCore Memory LTM + DynamoDB `TEAM#` patterns
- CDK: add SQS `learn-queue` ← SNS: `review.complete`
- Integration test: full end-to-end on test repo

**Deliverable:** Open PR → GitHub comment appears in <30 seconds with all findings ranked and formatted

**This is the first time the system is USABLE end-to-end.** After this phase, you can demo it.

**Critical test:** The GitHub comment must be clean, readable, and actionable. If the comment is ugly or confusing, iterate on templates until it looks professional.

---

### Phase 5 — Learning + Dashboard + Transport Abstraction (Week 9-10)

**Goal:** System learns from reviews, dashboard shows real-time activity, transport layer supports Kafka swap.

**What gets built:**
- LTM learning loop:
  - After each review, LTM Writer extracts patterns: "This repo always uses snake_case", "Team suppresses import order warnings"
  - Style Agent queries LTM before reviewing: pre-loads team preferences
  - Summary Agent queries LTM: suppresses known false positives
  - Track accuracy: when developer fixes a flagged issue (accepted) vs pushes without fixing (dismissed)
- `shared/transport.py` — add `KafkaTransport` class implementing same `AgentTransport` interface
  - Test with `docker-compose` Kafka (Confluent local image)
  - Verify: same test suite passes with both `TRANSPORT_TYPE=sqs` and `TRANSPORT_TYPE=kafka`
- Dashboard (React app):
  - WebSocket connection via API Gateway
  - Real-time event feed: shows each agent starting, processing, completing
  - Incident timeline: progress bar from webhook → comment posted
  - Metrics panel: reviews today, avg findings, avg latency, cost per review
  - CDK: add WebSocket API Gateway + Lambda connection handler
  - CDK: add SNS: `dashboard.events` + SQS: `dashboard-queue` (each agent publishes status updates)
- AgentCore Observability:
  - OTEL traces: webhook → parser → 4 agents → summary → comment
  - CloudWatch metrics: review_latency, findings_per_pr, tokens_per_review
  - CloudWatch dashboard with key graphs
- CDK: `monitoring-stack.ts` — 8 CloudWatch alarms:
  - DLQ message count > 0 (per agent)
  - Review latency > 60s
  - Error rate > 5%

**Deliverable:** Self-learning system + real-time dashboard + transport abstraction verified + monitoring

---

### Phase 6 — Demo + Portfolio + Documentation (Week 11)

**Goal:** Production-ready demo, documentation, and portfolio materials.

**What gets built:**
- Demo branches:
  - `demo/vulnerable-code` — PR with SQL injection, XSS, N+1, hardcoded secret, missing tests
  - `demo/clean-code` — PR with parameterized queries, type hints, docstrings, full tests
  - `demo/team-patterns` — PR with same SQL injection pattern (shows LTM learning)
- `demo/setup.sh` — seeds DynamoDB with runbooks and LTM with past patterns
- K8s documentation (NOT built, documented for interviews):
  - `docs/k8s/deployments/` — K8s Deployment YAML per agent
  - `docs/k8s/keda/` — KEDA ScaledObject YAML per agent
  - `docs/k8s/architecture.md` — EKS + KEDA + Kafka architecture explanation
  - `docs/k8s/interview-answers.md` — prepared answers for scaling questions
- README.md:
  - Architecture diagram
  - Setup guide (CDK deploy)
  - Cost analysis
  - Demo instructions
  - Screenshots of GitHub PR comment output
- Record 5-minute demo video:
  - Push vulnerable PR → watch agents review → show GitHub comment
  - Push clean PR → agents approve
  - Push repeat pattern → show LTM learning (faster, higher confidence)
  - Brief architecture walkthrough
- Blog post draft: "Building a Distributed Code Review Agent on AWS Free Tier"
- Resume bullet: "Built Argus, a distributed multi-agent code review platform using 6 specialized AI agents communicating via event bus, reviewing PRs in 15 seconds with 85%+ security issue detection, deployed on AWS free tier (~$2/mo) with documented K8s + KEDA production scaling path"

**Deliverable:** Portfolio-ready project with live demo, video, docs, K8s documentation, and resume materials

---

### Phase Dependency Map

```
Phase 1 (Parser) ──────────────────────────────────────┐
    │                                                    │
    ▼                                                    │
Phase 2 (Security Agent) ─────────────────────┐         │
    │                                          │         │
    ▼                                          │         │
Phase 3 (Perf + Style + Test Agents) ────┐    │         │
    │                                     │    │         │
    ▼                                     ▼    ▼         ▼
Phase 4 (Summary + GitHub Comment) ← needs all agents + parser
    │
    ├── Phase 5a (Learning + LTM) ← needs review.complete events
    ├── Phase 5b (Dashboard) ← can be built in parallel with 5a
    ├── Phase 5c (Kafka Transport) ← can be built in parallel with 5a/5b
    │
    ▼
Phase 6 (Demo + Docs) ← needs everything working
```

**When generating code, ALWAYS check which phase we're in.** Do not reference agents, topics, or services that haven't been built yet. For example:
- In Phase 1: do NOT import `bedrock_client` (no LLM yet)
- In Phase 2: do NOT reference `style-queue` (doesn't exist yet)
- In Phase 3: do NOT write Summary Agent aggregation logic (Phase 4)

## Links

- **GitHub Repo:** [to be added]
- **AWS Console:** us-east-1
- **AgentCore Docs:** https://docs.aws.amazon.com/bedrock-agentcore/
- **Bedrock Pricing:** https://aws.amazon.com/bedrock/pricing/
- **KEDA Docs:** https://keda.sh/docs/