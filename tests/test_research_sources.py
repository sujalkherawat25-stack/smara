import pytest

from smara.autonomous_agent import SmaraAutonomousAgent
from smara.harness import SessionEngine
from smara.research import RetrievedSource
from smara.research_session import CanonicalResearchSession
from smara.research_tools import SearchHit
from smara.research_sources import (
    authority_domain_groups,
    missing_authority_domain_groups,
    normalize_search_domains,
)


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("What PEP number and title define the marker?", [("peps.python.org",)]),
        ("Which RFC defines Problem Details?", [("rfc-editor.org", "ietf.org")]),
        ("What was the Git 2.49 release date?", [("git-scm.com",)]),
        ("In which Python version was tomllib added?", [("python.org", "docs.python.org")]),
    ],
)
def test_official_domain_hints_are_discovery_only_and_topic_based(question, expected):
    assert authority_domain_groups(question) == expected


def test_conflicting_organizations_get_separate_authority_groups():
    question = "Compare OpenSSL upstream end of life with Red Hat RHEL 8 support."
    assert authority_domain_groups(question) == [("openssl.org",), ("access.redhat.com",)]


def test_existing_first_party_evidence_only_suppresses_its_matching_group():
    question = "OpenSSL upstream and Red Hat RHEL 8 support"
    assert missing_authority_domain_groups(question, ["https://www.openssl.org/source/old/1.1.1/"]) == [
        ("access.redhat.com",)
    ]


@pytest.mark.parametrize("value", ["example.com site:bad.com", "https://example.com", "localhost", ["ok.org", "bad host"]])
def test_search_domain_filters_reject_non_hostnames(value):
    with pytest.raises(ValueError):
        normalize_search_domains(value)


def test_search_domain_filters_are_normalized_and_bounded():
    assert normalize_search_domains(["Docs.Python.org.", "docs.python.org"]) == ["docs.python.org"]
    with pytest.raises(ValueError, match="at most five"):
        normalize_search_domains([f"s{index}.example" for index in range(6)])


def test_canonical_gather_injects_first_party_domain_filter(tmp_path):
    class Searcher:
        def __init__(self):
            self.domains = []

        async def search(self, query, *, max_results=5, include_domains=None, **_kwargs):
            self.domains.append(include_domains)
            host = (include_domains or ["example.org"])[0]
            return [SearchHit(f"https://{host}/source", "Official source", "PEP source passage", "test")]

    class Fetcher:
        async def fetch(self, url):
            return RetrievedSource("Official source", "PEP 668 marks Python environments as externally managed.",
                                   "digest", "2026-09-30T00:00:00Z", raw_content=b"evidence", final_url=url)

    engine = SessionEngine(tmp_path, "authority-gather")
    searcher = Searcher()
    session = CanonicalResearchSession(session_engine=engine, searcher=searcher, fetcher=Fetcher())
    session.plan("What PEP number and title define the marker?", [
        {"id": "answer", "question": "What PEP number and title define the marker?"}
    ])
    agent = SmaraAutonomousAgent(api_key="test-only", profile="research_web", workspace_root=tmp_path,
                                 session_engine=engine)
    agent._research = session
    agent._active_research_task = "What PEP number and title define the externally managed environment marker?"

    result = agent._dispatch_research_gather({
        "requests": [{"node_id": "answer", "query": "externally managed environment marker"}],
        "max_sources_per_node": 2,
    })

    assert '"status": "ok"' in result
    assert ["peps.python.org"] in searcher.domains
    engine.close()
