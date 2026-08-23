from datetime import datetime
from typing import TYPE_CHECKING

from edgedash.agents.base import Agent, AgentResult

if TYPE_CHECKING:
    from edgedash.config import Config
    from edgedash import storage


class MockFetcher(Agent):
    """Fetches 12 realistic fake job listings. 8 are always identical to demo dedup, 4 are new each run."""

    @property
    def name(self) -> str:
        return "MockFetcher"

    def run(self, config: "Config", storage_module: "storage", stop_conditions=None) -> AgentResult:
        """Generate and upsert 12 realistic listings."""
        listings = self._generate_listings(config)
        new_count = storage_module.upsert_listings(config.db_path, listings)
        return AgentResult(
            agent=self.name,
            status="ok",
            records_touched=new_count,
            notes=f"Fetched {len(listings)} listings, {new_count} were new",
        )

    def _generate_listings(self, config: "Config") -> list[dict]:
        """Generate 12 realistic listings for role and city. 8 are duplicates, 4 are new each run."""
        import uuid
        base_url = "https://job-board.example.com"
        now = datetime.utcnow().isoformat()

        # 8 listings that repeat every run (same source:url = same stable ID for dedup)
        anchor_listings = [
            {
                "source": "example_board",
                "url": f"{base_url}/jobs/acme-1001",
                "title": f"Senior {config.target_role}",
                "company": "Acme Corp",
                "location": config.target_city,
                "description": "Lead analytics on big data pipelines. Skills: Python, SQL, Spark.",
                "posted_at": now,
            },
            {
                "source": "example_board",
                "url": f"{base_url}/jobs/techstart-2001",
                "title": f"{config.target_role} (Remote)",
                "company": "TechStart Inc",
                "location": config.target_city,
                "description": "Fast-paced role. Need Python, REST APIs, data modeling.",
                "posted_at": now,
            },
            {
                "source": "example_board",
                "url": f"{base_url}/jobs/blueprint-3001",
                "title": f"Junior {config.target_role}",
                "company": "Blueprint Labs",
                "location": config.target_city,
                "description": "Learn and grow. Excel, SQL basics, communication skills.",
                "posted_at": now,
            },
            {
                "source": "example_board",
                "url": f"{base_url}/jobs/cloudnine-4001",
                "title": f"{config.target_role} — Analytics Platform",
                "company": "Cloud Nine",
                "location": config.target_city,
                "description": "Build dashboards with Tableau, Power BI. SQL performance tuning.",
                "posted_at": now,
            },
            {
                "source": "linkedin_jobs",
                "url": f"{base_url}/jobs/dataflow-5001",
                "title": f"Mid-level {config.target_role}",
                "company": "DataFlow Systems",
                "location": config.target_city,
                "description": "Optimize ETL pipelines. Python, Unix, streaming data.",
                "posted_at": now,
            },
            {
                "source": "linkedin_jobs",
                "url": f"{base_url}/jobs/quantum-6001",
                "title": f"{config.target_role} — Financial Services",
                "company": "Quantum Finance",
                "location": config.target_city,
                "description": "Risk analytics. R, Python, statistical modeling.",
                "posted_at": now,
            },
            {
                "source": "indeed",
                "url": f"{base_url}/jobs/megacorp-7001",
                "title": f"Principal {config.target_role}",
                "company": "MegaCorp Analytics",
                "location": config.target_city,
                "description": "Lead team, mentor juniors. Advanced SQL, machine learning knowledge.",
                "posted_at": now,
            },
            {
                "source": "indeed",
                "url": f"{base_url}/jobs/shophub-8001",
                "title": f"{config.target_role} — E-commerce",
                "company": "ShopHub",
                "location": config.target_city,
                "description": "Drive user insights. Python, Pandas, data visualization.",
                "posted_at": now,
            },
        ]

        # 4 new listings each run (varied, with unique URLs per run)
        unique_id = uuid.uuid4().hex[:8]
        varied_listings = [
            {
                "source": "angel_list",
                "url": f"{base_url}/varied/role-{unique_id}-001",
                "title": f"{config.target_role} (Startup)",
                "company": "AI Startup X",
                "location": config.target_city,
                "description": "Wear many hats. Excel, SQL, basic Python, problem-solving.",
                "posted_at": now,
            },
            {
                "source": "angel_list",
                "url": f"{base_url}/varied/role-{unique_id}-002",
                "title": f"{config.target_role} — Product",
                "company": "ProductTech",
                "location": config.target_city,
                "description": "Product metrics and health dashboards. SQL, BI tools, communication.",
                "posted_at": now,
            },
            {
                "source": "naukri",
                "url": f"{base_url}/varied/role-{unique_id}-003",
                "title": f"Contract {config.target_role}",
                "company": "Consulting Group",
                "location": config.target_city,
                "description": "3-month project. Python, SQL, quick learner.",
                "posted_at": now,
            },
            {
                "source": "naukri",
                "url": f"{base_url}/varied/role-{unique_id}-004",
                "title": f"{config.target_role} — Healthcare",
                "company": "MediData Labs",
                "location": config.target_city,
                "description": "Clinical trial data. Statistical methods, SQL, compliance.",
                "posted_at": now,
            },
        ]

        return anchor_listings + varied_listings
