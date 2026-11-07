"""Per-shop model usage and estimated cost.

    docker exec munim_app python scripts/usage_report.py [days=30] [rupees_per_million_tokens=0]

Cost is only an estimate: pass your provider's blended price per 1M tokens (in rupees).
"""
import os
import sys

import psycopg
from psycopg.rows import dict_row

QUERY = """
SELECT t.name, count(DISTINCT u.session_id) AS sessions, count(*) AS model_calls,
       coalesce(sum(u.prompt_tokens), 0) AS prompt_tokens, coalesce(sum(u.completion_tokens), 0) AS completion_tokens,
       coalesce(sum(u.total_tokens), 0) AS total_tokens,
       count(*) FILTER (WHERE u.model <> %(main)s) AS fallback_calls
  FROM llm_usage u JOIN tenants t ON t.id = u.tenant_id
 WHERE u.created_at > now() - make_interval(days => %(days)s)
 GROUP BY t.name ORDER BY total_tokens DESC
"""


def usage_rows(conn, days: int = 30, main_model: str = "") -> list[dict]:
    return conn.execute(QUERY, {"days": days, "main": main_model}).fetchall()


def main() -> int:
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    rate = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0
    main_model = os.environ.get("LLM_AGENT_MODEL", "")
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        rows = usage_rows(conn, days, main_model)
    print(f"Model usage, last {days} days" + (f" (estimate at Rs {rate}/1M tokens)" if rate else ""))
    print(f"{'shop':<28}{'sessions':>9}{'calls':>7}{'tokens':>11}{'fallback':>9}" + (f"{'est. Rs':>10}" if rate else ""))
    for r in rows:
        line = f"{r['name'][:27]:<28}{r['sessions']:>9}{r['model_calls']:>7}{r['total_tokens']:>11,}{r['fallback_calls']:>9}"
        if rate:
            line += f"{r['total_tokens'] / 1e6 * rate:>10.2f}"
        print(line)
    if not rows:
        print("(no usage recorded yet)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
