import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    import yaml
except ImportError:
    yaml = None


@dataclass
class Config:
    """User profile and environment configuration."""

    target_role: str
    target_city: str
    keywords: list[str]
    my_skills: list[str]
    experience_years: int
    db_path: str
    min_fit_score: int
    sources: list[str]
    use_mock_fetcher: bool
    llm_provider: str
    llm_model: str
    llm_batch_size: int
    target_seniority: str
    score_weight_skill: float
    score_weight_seniority: float
    score_weight_location: float
    score_weight_recency: float
    score_batch_size: int
    skill_aliases: dict[str, str]
    fetch_interval_hours: int
    fetch_max_pages: int
    fetch_max_listings: int
    score_max_seconds: int
    analyse_max_seconds: int

    @classmethod
    def load(cls) -> "Config":
        """Load config from config.yaml at repo root. Fail loudly if missing."""
        config_path = Path(__file__).parent.parent / "config.yaml"

        if not config_path.exists():
            raise FileNotFoundError(
                f"config.yaml not found at {config_path}. "
                "Copy and edit config.example.yaml to get started."
            )

        if yaml is None:
            raise ImportError(
                "PyYAML is required. Install with: pip install pyyaml"
            )

        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        return cls(
            target_role=data.get("target_role", "Software Engineer"),
            target_city=data.get("target_city", "San Francisco"),
            keywords=data.get("keywords", ["python", "backend", "api"]),
            my_skills=data.get("my_skills", ["Python", "SQL", "REST APIs"]),
            experience_years=data.get("experience_years", 5),
            db_path=data.get("db_path", "./edgedash.db"),
            min_fit_score=data.get("min_fit_score", 60),
            sources=data.get("sources", ["arbeitnow"]),
            use_mock_fetcher=data.get("use_mock_fetcher", False),
            llm_provider=data.get("llm_provider", "gemini"),
            llm_model=data.get("llm_model", "gemini-2.0-flash"),
            llm_batch_size=data.get("llm_batch_size", 25),
            target_seniority=data.get("target_seniority", "mid"),
            score_weight_skill=data.get("score_weight_skill", 0.45),
            score_weight_seniority=data.get("score_weight_seniority", 0.25),
            score_weight_location=data.get("score_weight_location", 0.15),
            score_weight_recency=data.get("score_weight_recency", 0.15),
            score_batch_size=data.get("score_batch_size", 25),
            skill_aliases=data.get("skill_aliases") or {},
            fetch_interval_hours=data.get("fetch_interval_hours", 6),
            fetch_max_pages=data.get("fetch_max_pages", 5),
            fetch_max_listings=data.get("fetch_max_listings", 200),
            score_max_seconds=data.get("score_max_seconds", 300),
            analyse_max_seconds=data.get("analyse_max_seconds", 120),
        )


if __name__ == "__main__":
    import sys
    try:
        cfg = Config.load()
        print("\n✓ Config loaded successfully\n")
        print(f"  target_role:    {cfg.target_role}")
        print(f"  target_city:    {cfg.target_city}")
        print(f"  experience:     {cfg.experience_years} years")
        print(f"  db_path:        {cfg.db_path}")
        print(f"  min_fit_score:  {cfg.min_fit_score}")
        print(f"\n  keywords:       {', '.join(cfg.keywords)}")
        print(f"  my_skills:      {', '.join(cfg.my_skills)}")
        print("\n✓ All config fields resolved correctly\n")
        sys.exit(0)
    except FileNotFoundError as e:
        print(f"\n❌ {e}\n", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Config error: {e}\n", file=sys.stderr)
        sys.exit(1)
