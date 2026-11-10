"""Client boundary tests: sanitized errors, consent, no retries or redirects."""

import httpx
import pytest

from frontend.client import APIClient, APIError


def client(handler):
    return APIClient("http://testserver", http_client=httpx.Client(
        transport=httpx.MockTransport(handler), follow_redirects=False))


@pytest.mark.parametrize("status,body", [(500, "secret-key-DO-NOT-DISPLAY"),
    (422, {"detail": [{"input": "secret-key-DO-NOT-DISPLAY"}]}),
    (422, {"detail": {"code": "unrecognized", "message": "secret-key-DO-NOT-DISPLAY"}})])
def test_raw_server_errors_are_never_displayed(status, body):
    api = client(lambda r: httpx.Response(status, json=body))
    with pytest.raises(APIError) as error:
        api.options()
    assert "secret-key" not in str(error.value)


def test_timeout_is_not_retried():
    calls = []
    def timeout(request):
        calls.append(request)
        raise httpx.ReadTimeout("secret-url", request=request)
    with pytest.raises(APIError, match="автоматического повтора нет"):
        client(timeout).check("file.docx", b"test", profile="default")
    assert len(calls) == 1


@pytest.mark.parametrize("consent", [{}, {"allow_cloud": True}, {"synthetic_data": True}])
def test_cloud_requires_both_confirmations_before_http(consent):
    api = client(lambda r: pytest.fail("No HTTP before both confirmations"))
    with pytest.raises(APIError, match="подтвердите"):
        api.check("synthetic.docx", b"test", profile="default", provider="yandex", **consent)


def test_response_contract_is_checked():
    api = client(lambda r: httpx.Response(200, json={"invented": "result"}))
    with pytest.raises(APIError, match="контракту"):
        api.options()


def test_redirect_is_not_followed():
    calls = []
    def redirect(request):
        calls.append(request)
        return httpx.Response(307, headers={"location": "https://external.invalid/collect"})
    with pytest.raises(APIError):
        client(redirect).options()
    assert len(calls) == 1
