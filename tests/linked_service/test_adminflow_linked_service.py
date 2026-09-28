"""
**File:** ``test_adminflow_linked_service.py``
**Region:** ``tests/linked_service``

Unit tests for AdminflowLinkedService and AdminflowLinkedServiceSettings.
"""

from uuid import uuid4

from ds_protocol_http_py_lib.enums import AuthType
from ds_protocol_http_py_lib.linked_service.http import ApiKeyAuthSettings

from ds_provider_adminflow_py_lib.enums import ResourceType
from ds_provider_adminflow_py_lib.linked_service.adminflow import (
    AdminflowLinkedService,
    AdminflowLinkedServiceSettings,
)


class FakeSession:
    """Mock ``requests.Session``-like object exposing only ``headers``."""

    def __init__(self):
        self.headers: dict[str, str] = {}


class FakeResponse:
    """Mock HTTP response."""

    def __init__(self, json_data):
        self._json = json_data

    def json(self):
        return self._json


class FakeHttp:
    """Mock ``Http`` provider recording calls and returning canned responses."""

    def __init__(self, get_response=None, get_exception=None):
        self.session = FakeSession()
        self._get_response = get_response
        self._get_exception = get_exception
        self.get_calls: list[dict] = []
        self.closed = False

    def get(self, url, **kwargs):
        self.get_calls.append({"url": url, **kwargs})
        if self._get_exception is not None:
            raise self._get_exception
        return self._get_response

    def close(self):
        self.closed = True


def make_settings(**overrides) -> AdminflowLinkedServiceSettings:
    """Create AdminflowLinkedServiceSettings with sane test defaults."""
    defaults = {"token": "my-bearer-token"}
    defaults.update(overrides)
    return AdminflowLinkedServiceSettings(**defaults)


def make_service(**overrides) -> AdminflowLinkedService:
    """Create an AdminflowLinkedService instance for testing."""
    return AdminflowLinkedService(
        settings=make_settings(**overrides),
        id=uuid4(),
        name="test",
        version="1.0.0",
    )


# -----------------------------------------------------------------------------
# Settings defaults
# -----------------------------------------------------------------------------


def test_settings_defaults():
    """It initializes settings with sane defaults."""
    settings = make_settings()
    assert settings.host == "https://api.adminflow.no/api/accflow"
    assert settings.auth_type == AuthType.API_KEY


def test_settings_host_override():
    """A custom host can be passed explicitly to override the production default."""
    settings = make_settings(host="https://custom.example.com")
    assert settings.host == "https://custom.example.com"


def test_settings_masks_token():
    """token is marked for masking in logs."""
    fields = {f.name: f for f in AdminflowLinkedServiceSettings.__dataclass_fields__.values()}
    assert fields["token"].metadata.get("mask") is True


def test_settings_computes_api_key_from_token():
    """api_key is derived from token when unset -- the caller never sets it directly."""
    settings = make_settings(token="abc123")
    assert settings.api_key.name == "Authorization"
    assert settings.api_key.value == "Bearer abc123"


def test_settings_respects_explicit_api_key_override():
    """An explicitly-provided api_key is left untouched, not overwritten from token."""
    explicit = ApiKeyAuthSettings(name="X-Custom-Header", value="custom-value")
    settings = make_settings(api_key=explicit)
    assert settings.api_key is explicit


# -----------------------------------------------------------------------------
# Contract: schema/dataclass alignment -- a representative payload must
# deserialize successfully (LINKED_SERVICE_CONTRACT.md, "Construction guarantee")
# -----------------------------------------------------------------------------


def test_deserialize_representative_payload():
    """A representative schema-valid payload constructs successfully via deserialize()."""
    payload = {
        "id": str(uuid4()),
        "name": "adminflow",
        "version": "1.0.0",
        "settings": {"token": "my-bearer-token"},
    }

    service = AdminflowLinkedService.deserialize(payload)

    assert service.settings.token == "my-bearer-token"
    assert service.settings.host == "https://api.adminflow.no/api/accflow"  # default applied
    assert service.settings.api_key.value == "Bearer my-bearer-token"


# -----------------------------------------------------------------------------
# type property
# -----------------------------------------------------------------------------


def test_type_property():
    """type property returns the AdminFlow linked-service resource type."""
    service = make_service()
    assert service.type == ResourceType.LINKED_SERVICE


# -----------------------------------------------------------------------------
# connect() behavior -- inherited unmodified from HttpLinkedService; AdminFlow's
# static token fits the API_KEY handler, which just sets a header (no request).
# -----------------------------------------------------------------------------


def test_connect_sets_bearer_authorization_header():
    """connect() (inherited) sets Authorization: Bearer <token> via the API_KEY auth handler."""
    service = make_service(token="tok-123")
    service._http = FakeHttp()

    service.connect()

    assert service._http.session.headers["Authorization"] == "Bearer tok-123"


# -----------------------------------------------------------------------------
# test_connection() behavior
# -----------------------------------------------------------------------------


def test_connection_success():
    """test_connection() returns (True, '') when the firm/current/ probe succeeds."""
    service = make_service()
    service._http = FakeHttp(get_response=FakeResponse({"id": 1}))
    service.connect()

    success, message = service.test_connection()

    assert success is True
    assert message == ""


def test_connection_calls_firm_current_endpoint():
    """test_connection() probes the firm/current/ endpoint, not the bare host."""
    service = make_service()
    fake_http = FakeHttp(get_response=FakeResponse({"id": 1}))
    service._http = fake_http
    service.connect()

    service.test_connection()

    assert fake_http.get_calls[0]["url"] == "https://api.adminflow.no/api/accflow/firm/current/"


def test_connection_failure_returns_message():
    """test_connection() returns (False, message) when the probe raises."""
    service = make_service()
    service._http = FakeHttp(get_exception=RuntimeError("boom"))
    service.connect()

    success, message = service.test_connection()

    assert success is False
    assert "boom" in message


def test_connection_reports_failure_when_not_yet_connected():
    """test_connection() does not connect itself -- an unconnected service reports (False, ...), per LINKED_SERVICE_CONTRACT.md.

    ``connection`` raises ConnectionError when connect() hasn't been
    called; test_connection() must catch that and report it as a failed
    health check, not silently connect on the caller's behalf.
    """
    service = make_service()
    assert service._session is None

    success, message = service.test_connection()

    assert success is False
    assert "not initialized" in message.lower()


# -----------------------------------------------------------------------------
# close() behavior
# -----------------------------------------------------------------------------


def test_close_is_idempotent():
    """close() does not raise, including when called before connect()."""
    service = make_service()
    service.close()
    service.close()


def test_close_closes_http_client():
    """close() closes the underlying HTTP client."""
    service = make_service()
    fake_http = FakeHttp()
    service._http = fake_http
    service.connect()

    service.close()

    assert fake_http.closed is True
