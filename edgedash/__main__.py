"""Config validation entry point: python -m edgedash.config"""

import sys
from edgedash.config import Config


def main() -> int:
    """Load and validate config.yaml, print all fields."""
    try:
        config = Config.load()
        print("\n✓ Config loaded successfully\n")
        print(f"  target_role:    {config.target_role}")
        print(f"  target_city:    {config.target_city}")
        print(f"  experience:     {config.experience_years} years")
        print(f"  db_path:        {config.db_path}")
        print(f"  min_fit_score:  {config.min_fit_score}")
        print(f"\n  keywords:       {', '.join(config.keywords)}")
        print(f"  my_skills:      {', '.join(config.my_skills)}")
        print("\n✓ All config fields resolved correctly\n")
        return 0
    except FileNotFoundError as e:
        print(f"\n❌ {e}\n", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"\n❌ Config error: {e}\n", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
