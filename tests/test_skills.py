"""
Tests for edgedash/skills.py canonical().
Pure function — no network, no DB, no config loading.
"""

import pytest
from edgedash.skills import canonical


# Shared alias map for all tests
ALIASES: dict[str, str] = {
    "k8s": "kubernetes",
    "postgres": "postgres",
    "postgresql": "postgres",
    "psql": "postgres",
    "ml": "machine learning",
    "ci/cd": "ci/cd",
    "ci cd": "ci/cd",
    "cicd": "ci/cd",
    "nodejs": "node",
    "node.js": "node",
}


class TestCase:
    def test_lowercases(self):
        assert canonical("Python", ALIASES) == "python"

    def test_lowercases_mixed(self):
        assert canonical("PostgreSQL", ALIASES) == "postgres"

    def test_all_caps(self):
        assert canonical("SQL", ALIASES) == "sql"


class TestWhitespace:
    def test_strips_leading_trailing(self):
        assert canonical("  python  ", ALIASES) == "python"

    def test_collapses_internal(self):
        assert canonical("machine  learning", ALIASES) == "machine learning"

    def test_tab_and_spaces(self):
        # Tabs are whitespace; step 4 collapses them to single spaces
        assert canonical("ci\t/\tcd", ALIASES) == "ci / cd"

    def test_only_whitespace_returns_empty(self):
        assert canonical("   ", ALIASES) == ""


class TestSurroundingPunctuation:
    def test_strips_quotes(self):
        assert canonical('"python"', ALIASES) == "python"

    def test_strips_backtick(self):
        assert canonical("`sql`", ALIASES) == "sql"

    def test_strips_comma(self):
        assert canonical("python,", ALIASES) == "python"

    def test_strips_period(self):
        assert canonical("python.", ALIASES) == "python"


class TestParentheticalQualifiers:
    def test_drops_parens(self):
        assert canonical("Kubernetes (EKS)", ALIASES) == "kubernetes"

    def test_drops_parens_no_alias(self):
        assert canonical("docker (compose)", ALIASES) == "docker"

    def test_drops_parens_with_alias(self):
        # "k8s (eks)" → normalise to "k8s" → alias → "kubernetes"
        assert canonical("k8s (eks)", ALIASES) == "kubernetes"

    def test_parens_only_content(self):
        # "(python)" — the regex treats the whole thing as a parenthetical qualifier
        # and removes it, leaving empty string. Correct: it's not a valid skill token.
        result = canonical("(python)", ALIASES)
        assert result == ""


class TestAliasMap:
    def test_known_alias_applied(self):
        assert canonical("k8s", ALIASES) == "kubernetes"

    def test_postgres_variants(self):
        assert canonical("postgresql", ALIASES) == "postgres"
        assert canonical("psql", ALIASES) == "postgres"
        assert canonical("postgres", ALIASES) == "postgres"

    def test_ml_alias(self):
        assert canonical("ml", ALIASES) == "machine learning"

    def test_cicd_variants(self):
        assert canonical("cicd", ALIASES) == "ci/cd"
        assert canonical("ci cd", ALIASES) == "ci/cd"
        assert canonical("CI/CD", ALIASES) == "ci/cd"

    def test_nodejs_alias(self):
        assert canonical("Node.js", ALIASES) == "node"
        assert canonical("nodejs", ALIASES) == "node"


class TestNoAlias:
    def test_unknown_term_passes_through(self):
        assert canonical("spark", ALIASES) == "spark"

    def test_no_alias_still_lowercases(self):
        assert canonical("Apache Spark", ALIASES) == "apache spark"

    def test_no_alias_still_strips_parens(self):
        assert canonical("Spark (PySpark)", ALIASES) == "spark"


class TestEmptyAndEdgeCases:
    def test_empty_string(self):
        assert canonical("", ALIASES) == ""

    def test_none_like_empty(self):
        # Callers should guard, but canonical must not crash on edge inputs
        assert canonical("", ALIASES) == ""

    def test_single_char(self):
        assert canonical("R", ALIASES) == "r"

    def test_number_string(self):
        assert canonical("3", ALIASES) == "3"

    def test_empty_alias_map(self):
        # With no aliases, just normalise
        assert canonical("PostgreSQL", {}) == "postgresql"
        assert canonical("K8s", {}) == "k8s"


class TestIdempotence:
    """canonical(canonical(x)) == canonical(x) for any x."""

    @pytest.mark.parametrize("raw", [
        "Kubernetes (EKS)",
        "k8s",
        "  Python  ",
        "PostgreSQL",
        "CI/CD",
        "machine learning",
        "",
    ])
    def test_idempotent(self, raw: str):
        once = canonical(raw, ALIASES)
        twice = canonical(once, ALIASES)
        assert once == twice, f"Not idempotent: {raw!r} → {once!r} → {twice!r}"
