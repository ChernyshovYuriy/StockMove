# Agent Instructions

- Do not add trading execution.
- Do not add broker integration.
- Do not generate buy/sell/hold recommendations.
- Preserve raw-data/audit-trail design.
- Always add tests for collectors.
- Use source URLs and timestamps for all external records.
- Keep collectors deterministic.
- Keep LLM/AI interpretation optional and separated from raw data.
- Run `pytest` and `ruff check` before final response.
