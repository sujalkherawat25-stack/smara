"""Web search reads the links the helper returns for the page it reached. Unit level: no browser, no database."""
import base64
from types import SimpleNamespace

from src.agent import web
from src.agent.policy import SessionCtx


def _b64(url: str) -> str:
    return base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")


def test_duckduckgo_redirects_resolve_to_the_destination():
    href = "https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fpizza&rut=abc"
    assert web.real_url(href) == "https://example.org/pizza"


def test_bing_redirects_resolve_to_the_destination():
    href = f"https://www.bing.com/ck/a?!&&p=1&ntb=1&u=a1{_b64('https://example.org/pizza')}"
    assert web.real_url(href) == "https://example.org/pizza"


def test_results_group_the_title_and_the_longer_snippet_per_destination():
    links = [
        {"tag": "a", "href": "https://example.org/pizza", "text": "Mario's Pizza, Banswara"},
        {"tag": "a", "href": "https://example.org/pizza", "text": "Best cheese pizza in Banswara, open till 11 pm"},
        {"tag": "a", "href": "https://duckduckgo.com/about", "text": "About DuckDuckGo"},
        {"tag": "button", "href": "https://example.org/x", "text": "not a link"},
        {"tag": "a", "href": "#top", "text": "Top"},
    ]
    results = web.parse_results(links)
    assert [r["url"] for r in results] == ["https://example.org/pizza"]
    assert results[0]["title"] == "Mario's Pizza, Banswara"
    assert results[0]["snippet"].startswith("Best cheese pizza")


class _Browsers:
    def __init__(self, page_result):
        self.page_result, self.steps = page_result, []

    async def step(self, sid, args, tenant_id=None):
        self.steps.append(args)
        return self.page_result(args)

    async def close(self, sid):
        pass

    def where(self, sid):
        return "server"


async def test_a_page_with_links_gives_results_and_a_page_without_them_says_so():
    links_page = {"url": "https://duckduckgo.com/html/?q=x", "title": "x", "links": [
        {"tag": "a", "href": "https://example.org/pizza", "text": "Mario's Pizza"}]}
    browsers = _Browsers(lambda a: links_page)
    app = SimpleNamespace(state=SimpleNamespace(browsers=browsers,
                                                settings=SimpleNamespace(brave_search_api_key=""), pool=None))
    ctx = SessionCtx(id="w1", tenant_id="t1", trust="TRUSTED")
    out = await web.web_search(app, ctx, {"query": "pizza banswara"})
    assert out["engine"] == "duckduckgo" and out["results"][0]["url"] == "https://example.org/pizza"
    assert all(s.get("links") is True for s in browsers.steps)      # the helper is asked for the links

    # the old key (a listing without links) must not be mistaken for results
    app.state.browsers = _Browsers(lambda a: {"url": "u", "title": "t",
                                              "elements": [{"tag": "a", "href": "https://example.org/p", "text": "P"}]})
    out = await web.web_search(app, ctx, {"query": "pizza banswara"})
    assert "error" in out and "no results" in out["error"]
