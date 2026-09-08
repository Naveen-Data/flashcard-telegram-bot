"""One-time reclassification: assign cards.topic from their existing tags.

Idempotent — only touches rows where topic IS NULL, safe to re-run.
Usage: PGHOST=... PGUSER=... PGPASSWORD=... PGDATABASE=... python scripts/migrate_tags_to_topics.py [--dry-run]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from studybot.db.connection import get_connection  # noqa: E402

# First matching rule wins. Reasoning behind each group: see the plan discussed
# with the user — grounded in the actual tag co-occurrence in production data,
# not guessed from tag names alone.
RULES: list[tuple[str, set[str]]] = [
    ("system-design", {"system-design"}),
    ("rag", {
        "rag", "advanced-rag", "retrieval", "chunking", "indexing", "embeddings",
        "vector-db", "reranking", "hybrid-search", "rrf", "context-engineering", "compression",
    }),
    ("programming", {
        "python", "oop", "functions", "decorators", "exceptions", "context-managers",
        "typing", "dataclasses", "pydantic", "async", "mixup",
    }),
    ("ai", {
        "prompt-engineering", "temperature", "few-shot-cot", "reasoning-models",
        "self-consistency", "tree-of-thought", "react", "agents", "structured-outputs",
        "evaluation", "security", "definitions", "strategy",
    }),
]


class _DryRunAbort(Exception):
    pass


def classify(tags: str) -> str | None:
    tag_set = {t.strip() for t in tags.split(",") if t.strip()}
    if any(t.startswith("git") for t in tag_set):
        return "git"
    for topic, keywords in RULES:
        if tag_set & keywords:
            return topic
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    assigned: dict[str, int] = {}
    unmatched: list[int] = []

    try:
        with get_connection() as conn:
            rows = conn.execute(
                text("SELECT id, tags FROM cards WHERE topic IS NULL AND tags IS NOT NULL")
            ).fetchall()

            for card_id, tags in rows:
                topic = classify(tags)
                if topic is None:
                    unmatched.append(card_id)
                    continue
                assigned[topic] = assigned.get(topic, 0) + 1
                conn.execute(text("UPDATE cards SET topic=:t WHERE id=:id"), {"t": topic, "id": card_id})

            if args.dry_run:
                raise _DryRunAbort()
    except _DryRunAbort:
        pass

    print("Would assign:" if args.dry_run else "Assigned:")
    for topic, count in sorted(assigned.items()):
        print(f"  {topic}: {count}")
    print(f"Unmatched (left topic=NULL): {len(unmatched)} — ids {unmatched}")
    if args.dry_run:
        print("\n--dry-run: rolled back, nothing written.")


def _self_check() -> None:
    """Real tag combinations pulled from production before writing this script."""
    cases = {
        "system-design,strangler-fig": "system-design",
        "system-design,cap-theorem": "system-design",
        "system-design,networking,dns": "system-design",
        "phase11,rag,evaluation": "rag",
        "phase11,advanced-rag,rrf": "rag",
        "phase11,advanced-rag,hybrid-search": "rag",
        "phase11,context-engineering,strategy": "rag",  # context-engineering wins over ai's "strategy"
        "python,decorators": "programming",
        "python,oop": "programming",
        "git staging": "git",
        "git reset": "git",
        "phase11,prompt-engineering": "ai",
        "phase11,prompt-engineering,security": "ai",
        "phase11,context-engineering,definitions": "rag",
        "fundamentals,advanced,navforge": None,  # meta-tags only, no subject signal
    }
    for tags, expected in cases.items():
        got = classify(tags)
        assert got == expected, f"classify({tags!r}) = {got!r}, expected {expected!r}"
    print("self-check ok")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--self-check":
        _self_check()
    else:
        main()
