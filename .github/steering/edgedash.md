# EdgeDash Steering Document

## PROJECT

**EdgeDash** — an autonomous AI career intelligence agent.

A scheduled loop that:
1. Fetches live job listings
2. Scores them for fit against my profile
3. Surfaces my skill gaps
4. Verifies its own output
5. Publishes a Streamlit dashboard

---

## ARCHITECTURE

**Do not deviate without telling me.**

```
Trigger (scheduled)
       ↓
  Orchestrator (reads state, delegates)
       ↓
  Sub-agents:
   ├─ Fetcher (fetch job listings)
   ├─ Scorer (score fit against profile)
   └─ GapAnalyzer (surface skill gaps)
       ↓
  Verifier (verify output)
       ↓
  Storage (single module interface)
       ↓
  Dashboard (read-only, Streamlit)
```

**Key rules:**
- The Orchestrator reads state and delegates; it never fetches or scores directly.
- Each sub-agent has one goal and one stop condition.
- Storage access is funneled through a single storage module.

---

## HARD RULES

### 1. Python 3.11+
- Standard library first.
- Add a dependency only when it genuinely saves real work, and tell me why before you add it.

### 2. Single Storage Module
- ALL storage access goes through a single storage module with a thin interface.
- No other module may `import sqlite3` directly.
- We will swap SQLite for hosted Postgres in week 4 and it must be a one-file change.

### 3. Configuration, Not Hardcoding
- Never hardcode role, city, keywords, or skills profile.
- Everything user-specific lives in config.

### 4. No Secrets in Code
- Environment variables only.
- Secrets are loaded in one place.

### 5. Cycle Logging
- Every agent run writes a row to the `cycle_log` table with:
  - What ran
  - When
  - How many records touched
  - Pass/fail status
  - Any retry reason

### 6. Fail Loudly
- No `bare except: pass`.
- If something is wrong, surface it immediately.

### 7. Type Hints & Documentation
- Type hints on every function signature.
- Docstrings only where the intent is not obvious from the name.

### 8. File Size Limit
- Keep files under ~150 lines.
- Split before that becomes a problem.

---

## NETWORK & SOURCES

### 9. Source Abstraction
- Every external source lives behind a Source class with a uniform interface.
- The Fetcher never contains source-specific parsing.
- Adding a source must never require editing the Fetcher.

### 10. Normalised Output
- Every Source returns a list of normalised dicts with EXACTLY these keys:
  `source, external_id, title, company, location, url, description, posted_at, raw`
- Missing values are None, never empty string, never "N/A".

### 11. Network Helper
- All network calls go through one helper with a timeout (10s default), explicit retry (2 attempts, exponential backoff), and a User-Agent header.
- No bare `requests.get()` anywhere else in the codebase.

### 12. Per-Source Error Handling
- A source failing must NEVER kill the cycle.
- Catch per-source, log the failure to cycle_log with status "failed", continue to the next source.
- One dead job board must not stop the other sources.

### 13. Secret Management
- Secrets come from environment variables via a .env file that is gitignored.
- Never a literal key in code, never a key in config.yaml.
- If a key is missing, that source skips itself with a clear log line — it does not crash the cycle.

### 14. Source Respect
- Rate limit to at most 1 request per second per source.
- Set a real User-Agent header.
- Honour any documented page limits from the source.

---

## INTELLIGENCE & SCORING

### 15. Single LLM Module
- All LLM calls go through one module, `edgedash/llm.py`, exposing one function.
- The provider and model name come from config, never hardcoded.
- Rate limit to stay inside a free tier (default 1 request per second, max 15 per minute).
- No other file imports an LLM SDK.

### 16. Extraction, Not Scoring
- NEVER ask a model for a final score, ranking, or numeric rating.
- The model extracts structured facts only.
- All scoring arithmetic is deterministic Python in ONE function.
- The model never sees the scoring weights.

### 17. Response Validation
- Every model response is validated against an explicit schema before use.
- A response that fails validation is retried once, then logged as a failure for THAT listing only.
- It must not crash the cycle or stop the remaining listings.
- Never `json.loads` raw model text without a validation and repair path.

### 18. Idempotent Scoring
- Never re-score a listing that already has a score.
- Select only listings WHERE score IS NULL.
- Cache extraction results keyed on a hash of the job description.
- The same description text is never sent to the model twice.

### 19. Machine-Generated Reasons
- Every score carries a human-readable reason GENERATED FROM THE SCORE COMPONENTS by our code.
- Never free text written by the model.

### 20. Score Distribution Logging
- Log the score distribution (count, min, max, mean, spread) to cycle_log on every scoring run.
- A run where all scores fall within 10 points is a suspect run and must be logged as such.

### 21. Batch Size Cap
- Cap listings scored per cycle at a configurable batch size (default 25).
- A cost or rate-limit blowup is structurally impossible.

---

## AGGREGATE ANALYSIS

### 22. Deterministic Aggregates
- Aggregate analysis is deterministic SQL and Python. No LLM call may
  produce, adjust, or rank an aggregate number.
- A model may only SUGGEST canonical groupings for a human to approve.

### 23. Canonical Skill Names
- Skill names are canonicalised through an explicit alias map in
  `config.yaml` that the user owns and can read.
- Never auto-merge skill names by model judgement or string similarity alone.

### 24. Fit-Weighted Gap Ranking
- Gap ranking is weighted by the fit score of the listing the gap came from.
- A gap in a listing scored 20 is worth far less than a gap in a listing
  scored 85.
- Never rank gaps by raw frequency alone.

### 25. Timestamped Snapshots
- Every gap report run writes a timestamped SNAPSHOT.
- Never overwrite the previous report.
- Trend over time is a first-class output, not an afterthought.

### 26. Drillable Numbers
- Every aggregate number must be traceable to the rows that produced it.
- Any reported gap must be able to list the specific listing IDs it was
  computed from.
- No number appears in the dashboard that cannot be drilled into.

### 27. Sample Size Alongside Every Aggregate
- Report the sample size alongside every aggregate.
- A gap computed from 3 listings and a gap computed from 90 listings must
  never be presented as equally reliable.

---

## ORCHESTRATION

### 28. State-Driven Delegation
- The Orchestrator reads system state and decides which agents to run.
- It never runs a fixed sequence.
- Skipping an agent because there is no work for it is a SUCCESSFUL
  outcome, not a failure.

### 29. Explicit Goals and Stop Conditions
- Every delegation carries an explicit goal and an explicit stop condition
  (max items, max duration).
- A sub-agent never decides its own limits — the Orchestrator sets them.

### 30. No Agent Work in the Orchestrator
- The Orchestrator never does an agent's work.
- It reads state, delegates, collects results, and logs.
- No fetching, scoring, or analysis logic belongs in the Orchestrator.

### 31. Plan Before Execute
- The Orchestrator prints and logs its PLAN before executing it:
  which agents will run, which are skipped, and the state value that
  caused each decision.

### 32. Partial Cycles Are Valid
- One sub-agent failing does not stop the cycle.
- Log the failure, continue with the remaining plan, and mark the cycle
  partial.

### 33. One Summary Row Per Cycle
- Every cycle writes exactly one summary row: what ran, what was skipped,
  why, duration per agent, and the outcome.

---

## STYLE

- Small, testable functions.
- Plain readable Python over clever Python.
- When you ask for one module, build one module — do not scaffold the whole app.