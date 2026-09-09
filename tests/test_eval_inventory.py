import hashlib
import json
from pathlib import Path


def test_eval_inventory_counts_hashes_and_blocked_denominators():
    root=Path(__file__).parents[1];inventory=json.loads((root/"tests/evals/inventory.json").read_text(encoding="utf-8"));categories=inventory["categories"]
    assert inventory["total_declared_cases"]==sum(item["count"] for item in categories)==89
    assert inventory["historical_172_cases_recovered"] is False
    for item in categories:
        fixture=root/item["fixture"];assert fixture.is_file()
        if item.get("supporting_fixture"):assert (root/item["supporting_fixture"]).is_file()
        if item.get("fixture_sha256"):assert hashlib.sha256(fixture.read_bytes()).hexdigest()==item["fixture_sha256"]
        if item.get("manifest_sha256"):assert hashlib.sha256((root/item["manifest"]).read_bytes()).hexdigest()==item["manifest_sha256"]
    desktop=next(item for item in categories if item["id"]=="h5_desktop_d01_d20")
    assert desktop["count"]==20 and "blocked" in desktop["status"]
