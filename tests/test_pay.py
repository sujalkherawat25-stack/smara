from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.loop.pay import router
from src.loop.upi import pay_url

client = TestClient(FastAPI())
client.app.include_router(router)


def test_pay_link_redirects_to_upi_app():
    url = pay_url("https://x.test", "demo@upi", "Demo Shop", 4_075_000, "DEMO-102")
    r = client.get(url.replace("https://x.test", ""), follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "upi://pay?pa=demo@upi&pn=Demo%20Shop&am=40750.00&cu=INR&tn=DEMO-102"


def test_pay_rejects_bad_input_and_is_not_an_open_redirect():
    for q in ("pa=evil.com&am=10", "pa=a@b&am=abc", "pa=https://evil.com@x&am=10", "pa=demo@upi&am=-5",
              "pa=demo@upi&am=10&pn=" + "x" * 90):
        assert client.get("/pay?" + q, follow_redirects=False).status_code in (400, 422), q
    r = client.get("/pay?pa=demo@upi&am=10&pn=%0d%0aLocation:%20https://evil.com", follow_redirects=False)
    assert r.headers.get("location", "").startswith("upi://pay?")
