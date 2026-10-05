import asyncio

import httpx
import pytest

from smara.autonomous_agent import SmaraAutonomousAgent
from smara.evidence_index import EvidenceIndex
from smara.harness import SessionEngine
from smara.research import RetrievedSource, fetch_public_source, restricted_content_reason
from smara.research_completeness import evidence_review_context
from smara.research_modes import QUICK_POLICY
from smara.research_session import CanonicalResearchSession, ResearchStateError
from smara.research_tools import SearchHit


GATED = "Product support teaser. Subscriber exclusive content. A subscription provides access. Log in for full access."
PUBLIC = "Subscriptions Log In. Public support policy: maintenance includes security fixes during the support period."


@pytest.mark.parametrize("text", [GATED, "Subscribe to continue reading this article.",
                                      "Sign in to read the full article.", "Just a moment. Verify you are human."])
def test_explicit_article_access_gates_are_rejected(text):
    assert restricted_content_reason(text).startswith("restricted_source_content:")


@pytest.mark.parametrize("text", [PUBLIC, "A subscription is available. Public article: all policy details are visible.",
                                      "Subscriber exclusive content is an example of an access label, not an instruction."])
def test_account_navigation_and_gate_mentions_are_not_access_failures(text):
    assert restricted_content_reason(text) is None


@pytest.mark.parametrize("body,blocked", [(GATED, True), (PUBLIC, False)])
def test_http_200_is_not_sufficient_article_evidence(monkeypatch, body, blocked):
    monkeypatch.setattr("smara.research._is_public_http_url", lambda _url: True)

    async def run():
        transport = httpx.MockTransport(lambda request: httpx.Response(
            200, headers={"content-type": "text/html"}, text=f"<html><body>{body}</body></html>", request=request))
        async with httpx.AsyncClient(transport=transport) as client:
            return await fetch_public_source(client, "https://publisher.example/article")

    if blocked:
        with pytest.raises(ValueError, match="restricted_source_content"):
            asyncio.run(run())
    else:
        assert "security fixes" in asyncio.run(run()).excerpt


def test_gated_fetch_uses_bounded_discovered_public_replacement_and_retains_failure(tmp_path):
    gated_url, public_url = "https://publisher.example/a", "https://publisher.example/b"

    class Searcher:
        async def search(self, query, **kwargs):
            return [SearchHit(gated_url, "Support policy", "Support policy", "test"),
                    SearchHit(public_url, "Support policy", "Support policy", "test")]

    class Fetcher:
        def __init__(self):
            self.urls = []

        async def fetch(self, url):
            self.urls.append(url)
            return RetrievedSource("Support policy", GATED if url == gated_url else PUBLIC,
                                   "digest", "2026-10-02T00:00:00Z", final_url=url)

    engine = SessionEngine(tmp_path, "public-replacement")
    fetcher = Fetcher()
    session = CanonicalResearchSession(session_engine=engine, searcher=Searcher(), fetcher=fetcher)
    session.plan("Support policy", [{"id": "policy", "question": "Support policy"}])
    result = session.gather([{"node_id": "policy", "query": "Support policy"}], max_sources_per_node=1)
    assert result["status"] == "ok"
    assert fetcher.urls == [gated_url, public_url]
    assert session.fetched_source_urls() == [public_url]
    assert "restricted_source_content" in session.index.failures[0]["error"]
    snippet = next(record for record in session.index.records.values() if record.canonical_url == gated_url)
    assert snippet.kind == "search_snippet"
    assert session.index.judge(snippet.id, snippet.text).state == "insufficient"
    session.gather([{"node_id": "policy", "query": "Support policy"}], max_sources_per_node=1)
    assert fetcher.urls == [gated_url, public_url]
    engine.close()


def test_restored_gated_passages_cannot_be_promoted_or_used_in_review(tmp_path):
    engine = SessionEngine(tmp_path, "cached-gate")
    session = CanonicalResearchSession(session_engine=engine)
    session.plan("Support policy", [{"id": "policy", "question": "Support policy"}])
    record = session.index.add(kind="fetched_passage", url="https://publisher.example/a",
                               content=GATED.encode(), text=GATED)
    original_hash = record.text_sha256
    assert session.fetched_source_urls() == []
    assert session.index.judge(record.id, "Product support teaser.").reason == "restricted_source_content"
    assert evidence_review_context([record], "Support policy", "Product support teaser.") == []
    for _ in range(2):
        result = session.fetch("policy", record.canonical_url)
        assert result["status"] == "error" and result["deduplicated"]
    assert len(session.index.failures) == 1
    assert session.index.records[record.id].text_sha256 == original_hash
    restored = CanonicalResearchSession(session_engine=engine)
    assert restored.fetched_source_urls() == []
    assert restored.index.judge(record.id, "Product support teaser.").state == "insufficient"
    engine.close()


def test_local_document_about_access_gates_is_not_web_access_failure():
    index = EvidenceIndex()
    record = index.add(kind="fetched_passage", url="file:///workspace/notes.txt", content=GATED.encode(), text=GATED)
    assert index.judge(record.id, "Product support teaser.").state == "supported"
    assert len(evidence_review_context([record], "teaser", "teaser")) == 1


def test_old_validation_cannot_finalize_with_restricted_citation():
    session = CanonicalResearchSession()
    session.plan("Support policy", [{"id": "policy", "question": "Support policy"}])
    gated = session.index.add(kind="fetched_passage", url="https://publisher.example/a",
                              content=GATED.encode(), text=GATED)
    for suffix in ("b", "c", "d"):
        session.index.add(kind="fetched_passage", url=f"https://publisher.example/{suffix}",
                          content=PUBLIC.encode(), text=PUBLIC)
    session.validation = {"passed": True, "score": {"claims": [{"citations": [
        {"supported": True, "evidence_id": gated.id}]}]}}
    assert session.can_finalize("FINAL LABEL: insufficient") == (False, "restricted_source_content")


def test_domain_alternatives_do_not_consume_dag_node_slots(tmp_path):
    class Searcher:
        async def search(self, query, **kwargs):
            return []
    engine = SessionEngine(tmp_path, "multi-query-node")
    engine.set("research_policy", QUICK_POLICY.to_dict())
    session = CanonicalResearchSession(session_engine=engine, searcher=Searcher())
    session.plan("Policy", [{"id": "policy", "question": "Policy"}])
    result = session.gather([{"node_id": "policy", "query": f"policy angle {index}"} for index in range(6)])
    assert result["source_count"] == 0
    with pytest.raises(ResearchStateError, match="bounded query wave limit"):
        session.gather([{"node_id": "policy", "query": "Policy"}] * 25)
    engine.close()


def test_multiple_queries_for_first_node_cannot_starve_second_node(tmp_path):
    class Searcher:
        async def search(self, query, **kwargs):
            return [SearchHit(f"https://publisher.example/{query}/{index}", query, query, "test")
                    for index in range(8)]
    class Fetcher:
        async def fetch(self, url):
            return RetrievedSource("Public policy", PUBLIC, "digest", "2026-10-02T00:00:00Z", final_url=url)
    engine = SessionEngine(tmp_path, "fair-wave")
    engine.set("research_policy", QUICK_POLICY.to_dict())
    session = CanonicalResearchSession(session_engine=engine, searcher=Searcher(), fetcher=Fetcher())
    session.plan("Compare policy", [{"id": "first", "question": "First policy"},
                                    {"id": "second", "question": "Second policy"}])
    result = session.gather([{"node_id": "first", "query": f"first-{index}"} for index in range(3)] +
                            [{"node_id": "second", "query": "second"}], max_sources_per_node=5)
    counts = {item["node_id"]: len(item["evidence"]) for item in result["nodes"]}
    assert counts == {"first": 4, "second": 4}
    assert result["source_count"] == 8
    engine.close()


@pytest.mark.parametrize("operation", ["search", "gather"])
def test_discovery_follows_each_nodes_named_authority(tmp_path, operation):
    class Searcher:
        def __init__(self):
            self.calls = []

        async def search(self, query, *, include_domains=None, **kwargs):
            self.calls.append((query, include_domains))
            return []

    engine = SessionEngine(tmp_path, "named-authorities")
    searcher = Searcher()
    session = CanonicalResearchSession(session_engine=engine, searcher=searcher)
    question = "Compare OpenSSL upstream and Red Hat policy."
    session.plan(question, [{"id": "upstream", "question": "OpenSSL upstream support?"},
                            {"id": "vendor", "question": "Red Hat vendor policy?"}])
    agent = SmaraAutonomousAgent(api_key="test-only", profile="research_web", workspace_root=tmp_path, session_engine=engine)
    agent._research, agent._active_research_task = session, question
    for node in ("upstream", "vendor"):
        if operation == "search":
            agent._dispatch_research_search({"node_id": node, "query": node})
        else:
            agent._dispatch_research_gather({"requests": [{"node_id": node, "query": node}]})
    upstream = [domains for query, domains in searcher.calls if query == "upstream" and domains]
    vendor = [domains for query, domains in searcher.calls if query == "vendor" and domains]
    assert upstream and all(set(domains) <= {"openssl.org", "openssl-library.org"} for domains in upstream)
    assert vendor and all(domains == ["redhat.com"] for domains in vendor)
    engine.close()
