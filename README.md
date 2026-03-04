# Argus

Distributed multi-agent AI code review platform built on AWS.

> **Phase 1** — Webhook + Parser + GitHub Integration

## Quick Start

```bash
# Install Python dependencies
python3 -m venv .venv && source .venv/bin/activate
pip install poetry && poetry install

# Run tests
poetry run pytest tests/ -v

# Install CDK dependencies
cd lib && npm install

# Validate CDK stacks
npx cdk synth
```
