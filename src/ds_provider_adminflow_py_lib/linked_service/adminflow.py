"""
**File:** ``adminflow.py``
**Region:** ``ds_provider_adminflow_py_lib/linked_service/adminflow``

Linked service for the AdminFlow accounting-firm API.

AdminFlow authenticates with a single, already-issued, static per-tenant bearer
token -- there is no token-exchange/refresh step. This fits
``ds_protocol_http_py_lib``'s built-in ``AuthType.API_KEY`` handler.
``connect()`` is inherited as-is from ``HttpLinkedService``.

Example:
    >>> linked_service = AdminflowLinkedService(
    ...     settings=AdminflowLinkedServiceSettings(token="my-bearer-token"),
    ... )
    >>> linked_service.connect()
    >>> linked_service.connection.get(f"{linked_service.settings.host}/firm/current/")
    >>> linked_service.close()
"""

from dataclasses import dataclass, field
from typing import Generic, TypeVar

from ds_protocol_http_py_lib import HttpLinkedService, HttpLinkedServiceSettings
from ds_protocol_http_py_lib.enums import AuthType
from ds_protocol_http_py_lib.linked_service.http import ApiKeyAuthSettings

from ..enums import ResourceType


@dataclass(kw_only=True)
class AdminflowLinkedServiceSettings(HttpLinkedServiceSettings):
    """
    Settings required to connect to the AdminFlow API.

    Attributes:
        token: Static per-tenant bearer token issued by AdminFlow. Masked in logs.
        host: API host. Defaults to the production host; pass ``host=`` explicitly
            to target a different environment.
        auth_type: Always :attr:`~ds_protocol_http_py_lib.enums.AuthType.API_KEY`.
        api_key: Computed in :meth:`__post_init__` from :attr:`token` if unset --
            not meant to be set directly.
    """

    token: str = field(metadata={"mask": True})
    """Static per-tenant bearer token issued by AdminFlow."""

    host: str = "https://api.adminflow.no/api/accflow"
    """API host. Defaults to the AdminFlow production environment."""

    auth_type: AuthType = AuthType.API_KEY
    """Always API_KEY -- see class docstring."""

    def __post_init__(self) -> None:
        if self.api_key is None:
            self.api_key = ApiKeyAuthSettings(name="Authorization", value=f"Bearer {self.token}")


AdminflowLinkedServiceSettingsType = TypeVar(
    "AdminflowLinkedServiceSettingsType",
    bound=AdminflowLinkedServiceSettings,
)


@dataclass(kw_only=True)
class AdminflowLinkedService(
    HttpLinkedService[AdminflowLinkedServiceSettingsType],
    Generic[AdminflowLinkedServiceSettingsType],
):
    """
    Linked service for connecting to the AdminFlow API.

    Exposed (caller-configurable): ``id``, ``name``, ``description``,
    ``version``, ``settings``. Internal (inherited from ``HttpLinkedService``,
    ``init=False``, never user-settable): ``_session``, ``_http`` -- runtime
    connection state populated by ``connect()``.
    """

    settings: AdminflowLinkedServiceSettingsType

    @property
    def type(self) -> ResourceType:  # type: ignore[override]
        """Return the resource type for the AdminFlow linked service."""
        return ResourceType.LINKED_SERVICE

    def test_connection(self) -> tuple[bool, str]:
        """
        Verify the AdminFlow connection is healthy by calling the lightweight ``firm/current/`` endpoint.

        Returns:
            tuple[bool, str]: ``(True, "")`` on success, otherwise ``(False, error_message)``.
        """
        try:
            self.connection.get(f"{self.settings.host}/firm/current/")
            return True, ""
        except Exception as exc:
            return False, str(exc)
