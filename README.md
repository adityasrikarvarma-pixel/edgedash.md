# EdgeDash

An autonomous AI career intelligence loop. EdgeDash fetches live job listings daily, scores them for fit against your profile, surfaces your skill gaps, verifies its own output, and publishes a Streamlit dashboard. Built to scale from mock data to real APIs, and from SQLite to hosted Postgres.

## Architecture

```
Trigger (scheduled)
        ↓
  ┌─────────────────┐
  │  Orchestrator   │ (reads state, plans, delegates)
  └────────┬────────┘
           ↓
    ┌──────┴──────┐
    ↓             ↓             ↓
 ┌──────┐    ┌────────┐   ┌──────────────┐
 │Fetcher   │Scorer   │   │GapAnalyzer   │ (sub-agents: one goal each)
 └──────┘    └────────┘   └──────────────┘
    ↓             ↓             ↓
    └──────────┬──────────┘
               ↓
         ┌──────────────┐
         │  Verifier    │ (validates output)
         └──────┬───────┘
                ↓
         ┌──────────────┐
         │  Storage     │ (SQLite → Postgres, week 4)
         └──────┬───────┘
                ↓
         ┌──────────────────────┐
         │ Dashboard (Streamlit)│ (read-only)
         └──────────────────────┘
```

## Current Status

### ✅ Week 1 (Complete)

- [x] Config system (YAML-based, validated at startup)
- [x] Storage module (SQLite, three tables: listings, skill_gaps, cycle_log)
- [x] Orchestrator (delegates to agents, logs every run)
- [x] **MockFetcher agent** ⚠️ *Temporary: generates 12 realistic fake listings for testing*
- [x] Placeholder agents (Scorer, GapAnalyzer)
- [x] Entry point (`python run_cycle.py`)

### 📅 Week 2 (In Progress)

- [ ] Real Fetcher agent (LinkedIn, Indeed, Naukri APIs)
- [ ] Scorer agent (fit scoring by keyword match, seniority, location)

### 📅 Week 3 (Planned)

- [ ] GapAnalyzer agent (identifies missing skills)
- [ ] Verifier agent (spot-checks scoring logic)

### 📅 Week 4 (Planned)

- [ ] Postgres migration (single-file change in storage.py)
- [ ] Streamlit dashboard (read-only)
- [ ] Scheduled trigger (Airflow / GitHub Actions)

---

## Setup

### Requirements

- Python 3.11+

### Installation

```bash
# Install dependencies
pip install pyyaml
```

### Configuration

Edit `config.yaml` at the repo root:

```yaml
target_role: "Data Analyst"
target_city: "Bengaluru"
keywords:
  - "data analyst"
  - "analytics"
  - "sql"
  - "python"
  - "tableau"
my_skills:
  - "Python"
  - "SQL"
  - "Excel"
  - "Tableau"
experience_years: 4
db_path: "./edgedash.db"
min_fit_score: 60
```

Validate your config:
```bash
python -m edgedash.config
```

### Running a Cycle

```bash
python run_cycle.py
```

---

## Design Decisions

### Storage Isolation

All database access routes through a single `edgedash/storage.py` module. No other module imports sqlite3 directly. This ensures that swapping SQLite for Postgres in week 4 requires changing only one file, and agents remain agnostic to the underlying storage layer.

### Stable Listing IDs

Listing IDs are stable MD5 hashes of `source:url`. The same job posting (same source, same URL) always gets the same ID, enabling reliable deduplication across runs. On the second fetch, MockFetcher's 8 anchor listings are skipped; only the 4 unique listings are inserted.

### Orchestrator Delegates

The Orchestrator reads state and decides which agents to run, but never fetches, scores, or analyzes directly. Each agent has one goal and one stop condition. This separation keeps orchestration logic distinct from domain logic, making it easy to swap agents and audit execution via cycle_log.

---

**Status:** Alpha. Mock data only. Real APIs coming week 2.

