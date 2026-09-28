"""
**File:** ``test_adminflow_dataset.py``
**Region:** ``tests/dataset``

Unit tests for AdminflowDataset.
"""

from uuid import uuid4

import pandas as pd
import pytest
from ds_resource_plugin_py_lib.common.resource.dataset.errors import ReadError
from ds_resource_plugin_py_lib.common.resource.errors import NotSupportedError, ResourceException, ValidationError
from ds_resource_plugin_py_lib.common.resource.linked_service.errors import AuthenticationError, ConnectionError

import ds_provider_adminflow_py_lib.dataset.adminflow as adminflow_mod
from ds_provider_adminflow_py_lib.dataset.adminflow import (
    AdminflowDataset,
    AdminflowDatasetSettings,
    AdminflowReadSettings,
)
from ds_provider_adminflow_py_lib.enums import AdminflowProduct, PaginationKind, ResourceType
from ds_provider_adminflow_py_lib.linked_service.adminflow import (
    AdminflowLinkedService,
    AdminflowLinkedServiceSettings,
)
from ds_provider_adminflow_py_lib.models import ReadSpec


class FakeResponse:
    """Mock HTTP response."""

    def __init__(self, json_data, status_code: int = 200, headers: dict[str, str] | None = None):
        self._json = json_data
        self.status_code = status_code
        self.headers = headers or {}

    def json(self):
        return self._json


class FakeSession:
    """Mock ``Http``-like session that returns predefined responses in order."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests: list[dict] = []

    def get(self, url, params=None, headers=None):
        self.requests.append({"url": url, "params": params, "headers": headers})
        if not self.responses:
            raise ConnectionError("No more mock responses available in FakeSession")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class DummyAdminflowLinkedService(AdminflowLinkedService):
    """Linked service with a session injected directly, bypassing connect()."""

    def __init__(self, settings, session):
        super().__init__(settings=settings, id=uuid4(), name="dummy", version="1.0.0")
        self._session = session


def make_linked_service(responses) -> DummyAdminflowLinkedService:
    """Create a linked service with a mocked session."""
    settings = AdminflowLinkedServiceSettings(token="test-token")
    return DummyAdminflowLinkedService(settings=settings, session=FakeSession(responses))


def make_dataset(
    responses,
    product: AdminflowProduct | None = AdminflowProduct.CLIENTS,
    read: AdminflowReadSettings | None = None,
) -> AdminflowDataset:
    """Create a dataset with a mocked linked service."""
    linked_service = make_linked_service(responses)
    settings = AdminflowDatasetSettings(product=product, read=read or AdminflowReadSettings())
    return AdminflowDataset(
        id=uuid4(),
        name="test_dataset",
        version="1.0.0",
        linked_service=linked_service,
        settings=settings,
    )


# -----------------------------------------------------------------------------
# Contract: schema/dataclass alignment -- a representative payload must
# deserialize successfully (DATASET_CONTRACT.md, "Construction guarantee")
# -----------------------------------------------------------------------------


def test_deserialize_representative_payload():
    """A representative schema-valid payload constructs successfully via deserialize()."""
    payload = {
        "id": str(uuid4()),
        "name": "adminflow",
        "version": "1.0.0",
        "settings": {"product": "clients"},
        "linked_service": {
            "id": str(uuid4()),
            "name": "adminflow",
            "version": "1.0.0",
            "settings": {"token": "my-bearer-token"},
        },
    }

    dataset = AdminflowDataset.deserialize(payload)

    assert dataset.settings.product is AdminflowProduct.CLIENTS
    assert isinstance(dataset.linked_service, AdminflowLinkedService)
    assert dataset.linked_service.settings.token == "my-bearer-token"


# -----------------------------------------------------------------------------
# Contract: dataset type and checkpoint support
# -----------------------------------------------------------------------------


def test_type_property():
    """type property returns the AdminFlow dataset resource type."""
    dataset = make_dataset([])
    assert dataset.type == ResourceType.DATASET


def test_supports_checkpoint_defaults_to_false():
    """AdminFlow has no safe incremental watermark -- supports_checkpoint stays at the base default."""
    dataset = make_dataset([])
    assert dataset.supports_checkpoint is False


# -----------------------------------------------------------------------------
# serializer/deserializer defaults
# -----------------------------------------------------------------------------


def test_deserializer_stays_none_when_unset_on_a_packaged_product():
    """A packaged product never gets a default deserializer set -- its shaping comes from read_spec alone."""
    dataset = make_dataset([], product=AdminflowProduct.CLIENTS)
    assert dataset.deserializer is None


def test_serializer_stays_none_when_unset():
    """serializer has no default -- every write method raises NotSupportedError, so nothing ever reads it."""
    dataset = make_dataset([])
    assert dataset.serializer is None


def test_serializer_and_deserializer_are_not_overridden_when_provided():
    """An explicitly-provided serializer/deserializer is left untouched."""
    custom_serializer = adminflow_mod.PandasSerializer(format=adminflow_mod.DatasetStorageFormatType.JSON, kwargs={})
    custom_deserializer = adminflow_mod.PandasDeserializer(
        format=adminflow_mod.DatasetStorageFormatType.SEMI_STRUCTURED_JSON, kwargs={}
    )
    read = AdminflowReadSettings(path="custom/thing/", pagination=PaginationKind.OFFSET)
    linked_service = make_linked_service([])
    dataset = AdminflowDataset(
        id=uuid4(),
        name="test_dataset",
        version="1.0.0",
        linked_service=linked_service,
        settings=AdminflowDatasetSettings(read=read),
        serializer=custom_serializer,
        deserializer=custom_deserializer,
    )
    assert dataset.serializer is custom_serializer
    assert dataset.deserializer is custom_deserializer


# -----------------------------------------------------------------------------
# Contract: offset pagination
# -----------------------------------------------------------------------------


def test_read_single_page_populates_output():
    """A single page followed by an empty page reads exactly one row."""
    responses = [
        FakeResponse({"results": [{"id": "c1", "name": "Acme"}]}),
        FakeResponse({"results": []}),
    ]
    dataset = make_dataset(responses, product=AdminflowProduct.CLIENTS)
    dataset.read()

    assert len(dataset.output) == 1
    assert dataset.output.iloc[0]["id"] == "c1"
    assert len(dataset.linked_service.connection.requests) == 2


def test_read_advances_offset_by_returned_page_length():
    """offset advances by len(page) on each request, for products with no row filtering."""
    responses = [
        FakeResponse({"results": [{"id": "1"}, {"id": "2"}]}),
        FakeResponse({"results": [{"id": "3"}]}),
        FakeResponse({"results": []}),
    ]
    dataset = make_dataset(responses, product=AdminflowProduct.CLIENTS)
    dataset.read()

    requests = dataset.linked_service.connection.requests
    assert [r["params"]["offset"] for r in requests] == [0, 2, 3]
    assert len(dataset.output) == 3


def test_read_sends_order_by_when_product_declares_one():
    """clients pins order_by=created_date on every request."""
    responses = [FakeResponse({"results": []})]
    dataset = make_dataset(responses, product=AdminflowProduct.CLIENTS)
    dataset.read()
    assert dataset.linked_service.connection.requests[0]["params"]["order_by"] == "created_date"


def test_read_omits_order_by_when_product_declares_none():
    """accountants has no order_by -- must not send the param at all."""
    responses = [FakeResponse({"results": []})]
    dataset = make_dataset(responses, product=AdminflowProduct.ACCOUNTANTS)
    dataset.read()
    assert "order_by" not in dataset.linked_service.connection.requests[0]["params"]


def test_read_sends_packaged_default_limit():
    """clients' packaged limit (500) is sent when settings.read.limit is unset."""
    responses = [FakeResponse({"results": []})]
    dataset = make_dataset(responses, product=AdminflowProduct.CLIENTS)
    dataset.read()
    assert dataset.linked_service.connection.requests[0]["params"]["limit"] == 500


def test_read_limit_override():
    """settings.read.limit overrides the packaged default page size."""
    responses = [FakeResponse({"results": []})]
    dataset = make_dataset(responses, product=AdminflowProduct.CLIENTS, read=AdminflowReadSettings(limit=10))
    dataset.read()
    assert dataset.linked_service.connection.requests[0]["params"]["limit"] == 10


def test_read_request_url_uses_packaged_path():
    """The request URL is the linked service host joined with the product's packaged path."""
    responses = [FakeResponse({"results": []})]
    dataset = make_dataset(responses, product=AdminflowProduct.ACCOUNTANTS_DETAILED)
    dataset.read()
    assert dataset.linked_service.connection.requests[0]["url"] == "https://api.adminflow.no/api/accflow/accountants/detailed"


def test_read_accepts_a_bare_list_response_not_only_a_results_wrapped_one():
    """A page whose body is a bare list (not {"results": [...]}) is read the same way."""
    responses = [FakeResponse([{"id": "1"}]), FakeResponse([])]
    dataset = make_dataset(responses, product=AdminflowProduct.CLIENTS)
    dataset.read()
    assert len(dataset.output) == 1


# -----------------------------------------------------------------------------
# Contract: async ("202 + Location") offset pagination (clients_detailed/projects_detailed)
# -----------------------------------------------------------------------------


def test_read_async_offset_uses_response_directly_when_not_202():
    """A page that never returns 202 is read the same as plain offset pagination -- no polling at all."""
    responses = [
        FakeResponse({"results": [{"id": "1"}]}),
        FakeResponse({"results": []}),
    ]
    dataset = make_dataset(responses, product=AdminflowProduct.CLIENTS_DETAILED)
    dataset.read()
    assert len(dataset.output) == 1
    assert len(dataset.linked_service.connection.requests) == 2


def test_read_async_offset_resolves_202_then_reads_page(monkeypatch):
    """A 202 page is polled at its Location URL (resolved against the request URL) until resolved."""
    sleep_calls: list[float] = []
    monkeypatch.setattr(adminflow_mod.time, "sleep", sleep_calls.append)
    responses = [
        FakeResponse({}, status_code=202, headers={"Location": "/jobs/abc"}),
        FakeResponse({"results": [{"id": "1"}]}),
        FakeResponse({"results": []}),
    ]
    dataset = make_dataset(responses, product=AdminflowProduct.CLIENTS_DETAILED)
    dataset.read()

    requests = dataset.linked_service.connection.requests
    assert len(dataset.output) == 1
    assert len(requests) == 3
    assert requests[1]["url"] == "https://api.adminflow.no/jobs/abc"
    # The first poll resolves immediately (non-202) -- no sleep needed at all.
    assert sleep_calls == []


def test_read_async_offset_resolves_absolute_url_in_location_header(monkeypatch):
    """A Location header that is itself a full URL is used as-is, not doubled with the request's own origin."""
    sleep_calls: list[float] = []
    monkeypatch.setattr(adminflow_mod.time, "sleep", sleep_calls.append)
    responses = [
        FakeResponse(
            {},
            status_code=202,
            headers={"Location": "https://api.adminflow.no/api/accflow/jobs/abc"},
        ),
        FakeResponse({"results": [{"id": "1"}]}),
        FakeResponse({"results": []}),
    ]
    dataset = make_dataset(responses, product=AdminflowProduct.CLIENTS_DETAILED)
    dataset.read()

    requests = dataset.linked_service.connection.requests
    assert len(dataset.output) == 1
    assert requests[1]["url"] == "https://api.adminflow.no/api/accflow/jobs/abc"


def test_read_async_offset_polls_with_exponential_backoff(monkeypatch):
    """Repeated 202s back off exponentially (2s, then 4s) before resolving.

    initial(202) -> poll1(202, sleep 2s) -> poll2(202, sleep 4s) -> poll3(resolved).
    """
    sleep_calls: list[float] = []
    monkeypatch.setattr(adminflow_mod.time, "sleep", sleep_calls.append)
    responses = [
        FakeResponse({}, status_code=202, headers={"Location": "/jobs/abc"}),
        FakeResponse({}, status_code=202),
        FakeResponse({}, status_code=202),
        FakeResponse({"results": []}),
    ]
    dataset = make_dataset(responses, product=AdminflowProduct.CLIENTS_DETAILED)
    dataset.read()
    assert sleep_calls == [2.0, 4.0]


def test_read_async_offset_raises_on_poll_timeout(monkeypatch):
    """Polling past the timeout budget raises ReadError instead of blocking forever."""
    monotonic_values = iter([0.0, 301.0])
    monkeypatch.setattr(adminflow_mod.time, "monotonic", lambda: next(monotonic_values))
    responses = [FakeResponse({}, status_code=202, headers={"Location": "/jobs/abc"})]
    dataset = make_dataset(responses, product=AdminflowProduct.CLIENTS_DETAILED)
    with pytest.raises(ReadError, match="Polling"):
        dataset.read()


def test_read_async_offset_advances_offset_by_resolved_page_length():
    """offset advances by the resolved page's length, same as plain offset pagination."""
    responses = [
        FakeResponse({"results": [{"id": "1"}, {"id": "2"}]}),
        FakeResponse({"results": [{"id": "3"}]}),
        FakeResponse({"results": []}),
    ]
    dataset = make_dataset(responses, product=AdminflowProduct.CLIENTS_DETAILED)
    dataset.read()
    requests = dataset.linked_service.connection.requests
    assert [r["params"]["offset"] for r in requests] == [0, 2, 3]
    assert len(dataset.output) == 3


def test_read_async_offset_sends_order_by_and_packaged_limit():
    """clients_detailed pins order_by=created_date and its packaged limit (100), same as offset products."""
    responses = [FakeResponse({"results": []})]
    dataset = make_dataset(responses, product=AdminflowProduct.CLIENTS_DETAILED)
    dataset.read()
    params = dataset.linked_service.connection.requests[0]["params"]
    assert params["order_by"] == "created_date"
    assert params["limit"] == 100


def test_fetch_async_offset_omits_order_by_when_product_declares_none():
    """A hypothetical async_offset product with no order_by omits the param, like offset products do."""
    dataset = make_dataset([FakeResponse({"results": []})], product=AdminflowProduct.CLIENTS_DETAILED)
    read_spec = ReadSpec(path="some/path/", pagination=PaginationKind.ASYNC_OFFSET, limit=50)
    records: list[dict] = []
    dataset._fetch_async_offset(dataset._build_url(read_spec.path), read_spec, records)
    assert "order_by" not in dataset.linked_service.connection.requests[0]["params"]


# -----------------------------------------------------------------------------
# Contract: self_declaration (two-request-per-page fetch, raw-page offset advance)
# -----------------------------------------------------------------------------


def test_read_self_declaration_resolves_ids_and_advances_offset_by_raw_page_length():
    """Offset advances by the raw detailed page length, not the resolved self-declaration count.

    Regression test for a real, previously-fixed AdminFlow production bug
    that collapsed the raw-consumed count with the returned/filtered count.
    """
    responses = [
        FakeResponse(
            {
                "results": [
                    {"id": "d1", "last_self_declaration": {"id": "sd1"}},
                    {"id": "d2"},
                ]
            }
        ),
        FakeResponse({"id": "sd1", "status": "completed"}),
        FakeResponse({"results": []}),
    ]
    dataset = make_dataset(responses, product=AdminflowProduct.SELF_DECLARATION)
    dataset.read()

    assert len(dataset.output) == 1
    assert dataset.output.iloc[0]["id"] == "sd1"
    requests = dataset.linked_service.connection.requests
    assert len(requests) == 3
    assert requests[1]["url"] == "https://api.adminflow.no/api/accflow/self-declaration/sd1"
    assert requests[2]["params"]["offset"] == 2


def test_read_self_declaration_continues_pagination_past_a_zero_yield_page():
    """A page where zero detailed records qualify still advances -- must not be treated as end-of-pagination."""
    responses = [
        FakeResponse({"results": [{"id": "d1"}, {"id": "d2"}]}),
        FakeResponse({"results": []}),
    ]
    dataset = make_dataset(responses, product=AdminflowProduct.SELF_DECLARATION)
    dataset.read()

    assert len(dataset.output) == 0
    requests = dataset.linked_service.connection.requests
    assert len(requests) == 2
    assert requests[1]["params"]["offset"] == 2


def test_fetch_self_declarations_skips_a_failed_id_and_keeps_the_rest():
    """A failed per-id fetch is logged and skipped, matching the legacy service exactly -- not an aborted read."""
    responses = [
        ResourceException(message="boom", status_code=500, details={}),
        FakeResponse({"id": "sd2"}),
    ]
    dataset = make_dataset(responses)
    detailed_page = [
        {"last_self_declaration": {"id": "sd1"}},
        {"last_self_declaration": {"id": "sd2"}},
    ]

    resolved = dataset._fetch_self_declarations(detailed_page)

    assert resolved == [{"id": "sd2"}]


def test_fetch_self_declarations_propagates_authentication_error_instead_of_skipping():
    """An AuthenticationError during a per-id lookup must propagate, not be swallowed as a per-id failure."""
    responses = [AuthenticationError(message="Authentication error: 401", details={})]
    dataset = make_dataset(responses)
    detailed_page = [{"last_self_declaration": {"id": "sd1"}}]

    with pytest.raises(AuthenticationError):
        dataset._fetch_self_declarations(detailed_page)


def test_fetch_self_declarations_propagates_connection_error_instead_of_skipping():
    """A ConnectionError during a per-id lookup must propagate, not be swallowed as a per-id failure."""
    responses = [ConnectionError(message="network down", details={})]
    dataset = make_dataset(responses)
    detailed_page = [{"last_self_declaration": {"id": "sd1"}}]

    with pytest.raises(ConnectionError):
        dataset._fetch_self_declarations(detailed_page)


def test_fetch_self_declarations_skips_records_without_a_last_self_declaration():
    """Records with no last_self_declaration, or none with an id, are skipped -- no GET issued for them."""
    dataset = make_dataset([])
    detailed_page = [
        {"id": "d1"},
        {"id": "d2", "last_self_declaration": {}},
        {"id": "d3", "last_self_declaration": {"id": None}},
        {"id": "d4", "last_self_declaration": None},
    ]

    resolved = dataset._fetch_self_declarations(detailed_page)

    assert resolved == []
    assert dataset.linked_service.connection.requests == []


# -----------------------------------------------------------------------------
# Contract: single non-paginated object response (firm_current)
# -----------------------------------------------------------------------------


def test_read_single_object_product_populates_one_row():
    """firm_current's response is one JSON object, wrapped into a one-row DataFrame."""
    responses = [FakeResponse({"id": "f1", "name": "My Firm", "has_maestro": True})]
    dataset = make_dataset(responses, product=AdminflowProduct.FIRM_CURRENT)
    dataset.read()

    assert len(dataset.output) == 1
    assert dataset.output.iloc[0]["id"] == "f1"
    assert bool(dataset.output.iloc[0]["has_maestro"]) is True


def test_read_single_object_product_sends_exactly_one_request_with_no_pagination_params():
    """firm_current sends one request with no offset/limit/order_by -- it isn't paginated."""
    responses = [FakeResponse({"id": "f1"})]
    dataset = make_dataset(responses, product=AdminflowProduct.FIRM_CURRENT)
    dataset.read()

    requests = dataset.linked_service.connection.requests
    assert len(requests) == 1
    assert requests[0]["params"] is None
    assert requests[0]["url"] == "https://api.adminflow.no/api/accflow/firm/current/"


def test_read_normalizes_hyphens_in_flattened_permission_column_names():
    """firm_current's permissions object is flattened, and hyphens become underscores in the output column names."""
    responses = [
        FakeResponse(
            {
                "id": "f1",
                "permissions": {
                    "accflow-quality-control-templates": 3,
                    "accflow-task-templates": 3,
                    "accflow-chat-templates": 3,
                },
            }
        )
    ]
    dataset = make_dataset(responses, product=AdminflowProduct.FIRM_CURRENT)
    dataset.read()

    row = dataset.output.iloc[0]
    assert row["permissions_accflow_quality_control_templates"] == 3
    assert row["permissions_accflow_task_templates"] == 3
    assert row["permissions_accflow_chat_templates"] == 3


# -----------------------------------------------------------------------------
# Contract: output shaping (dtype projection)
# -----------------------------------------------------------------------------


def test_read_projects_and_casts_declared_dtypes():
    """Declared dtype columns are cast; a spot-check across string/Int64/boolean/datetime."""
    responses = [
        FakeResponse(
            {
                "results": [
                    {
                        "id": "c1",
                        "name": "Acme",
                        "archived": False,
                        "client_type": 2,
                        "created_date": "2024-01-01T00:00:00Z",
                    }
                ]
            }
        ),
        FakeResponse({"results": []}),
    ]
    dataset = make_dataset(responses, product=AdminflowProduct.CLIENTS)
    dataset.read()

    row = dataset.output.iloc[0]
    assert row["id"] == "c1"
    assert dataset.output["archived"].dtype == "boolean"
    assert dataset.output["client_type"].dtype == "Int64"
    assert pd.api.types.is_datetime64_any_dtype(dataset.output["created_date"])


def test_read_drops_columns_not_in_dtypes():
    """A response field not declared in the product's dtypes is dropped from the output."""
    responses = [
        FakeResponse({"results": [{"id": "c1", "name": "Acme", "totally_undeclared_field": "x"}]}),
        FakeResponse({"results": []}),
    ]
    dataset = make_dataset(responses, product=AdminflowProduct.CLIENTS)
    dataset.read()
    assert "totally_undeclared_field" not in dataset.output.columns


def test_read_adds_missing_dtype_columns_as_null():
    """A dtype column absent from the response is added as an all-null column, not dropped."""
    responses = [FakeResponse({"results": [{"id": "c1"}]}), FakeResponse({"results": []})]
    dataset = make_dataset(responses, product=AdminflowProduct.CLIENTS)
    dataset.read()
    assert "organization_number" in dataset.output.columns
    assert dataset.output["organization_number"].isna().all()


def test_read_raises_read_error_on_a_cast_failure():
    """An incompatible cast raises ReadError -- matches the legacy transform service's own convert_dtypes()."""
    responses = [
        FakeResponse({"results": [{"id": "c1", "client_type": "not-a-number"}]}),
        FakeResponse({"results": []}),
    ]
    dataset = make_dataset(responses, product=AdminflowProduct.CLIENTS)
    with pytest.raises(ReadError, match="client_type") as exc_info:
        dataset.read()
    assert exc_info.value.details["column"] == "client_type"
    assert exc_info.value.details["dtype"] == "Int64"


def test_shape_output_returns_an_empty_frame_with_declared_dtypes_when_records_is_empty():
    """No records (e.g. a failed read before any page was fetched) still yields every declared dtype column.

    reindex() adds missing columns even at zero rows, and every declared
    dtype casts cleanly on an empty column -- confirms _shape_output_ignoring_errors
    produces a well-shaped (if empty) self.output rather than a bare 0x0 frame.
    """
    dataset = make_dataset([])
    read_spec = ReadSpec(
        path="x/",
        pagination=PaginationKind.OFFSET,
        dtypes={"id": "string", "name": "string", "created_date": "datetime64[ns, UTC]", "count": "Int64"},
    )
    output = dataset._shape_output([], read_spec)
    assert len(output) == 0
    assert list(output.columns) == ["count", "created_date", "id", "name"]
    assert output["id"].dtype == "string"
    assert output["name"].dtype == "string"
    assert output["created_date"].dtype == "datetime64[ns, UTC]"
    assert output["count"].dtype == "Int64"


def test_shape_output_casts_string_dtype():
    """A "string" dtype column casts to pandas' nullable StringDtype, not plain object."""
    dataset = make_dataset([])
    read_spec = ReadSpec(path="x/", pagination=PaginationKind.OFFSET, dtypes={"name": "string"})
    output = dataset._shape_output([{"name": "Acme"}], read_spec)
    assert output["name"].dtype == "string"


def test_shape_output_casts_object_dtype():
    """An "object" dtype column (a nested list/dict blob) is left as-is -- astype("object") is always a no-op cast."""
    dataset = make_dataset([])
    read_spec = ReadSpec(path="x/", pagination=PaginationKind.OFFSET, dtypes={"modules": "object"})
    output = dataset._shape_output([{"modules": ["aml", "kyc"]}], read_spec)
    assert output.iloc[0]["modules"] == ["aml", "kyc"]
    assert output["modules"].dtype == "object"


def test_shape_output_casts_float64_dtype():
    """A "float64" dtype column casts correctly."""
    dataset = make_dataset([])
    read_spec = ReadSpec(path="x/", pagination=PaginationKind.OFFSET, dtypes={"lat": "float64"})
    output = dataset._shape_output([{"lat": 59.9139}], read_spec)
    assert output["lat"].dtype == "float64"
    assert output.iloc[0]["lat"] == 59.9139


def test_shape_output_casts_int64_dtype():
    """A plain (non-nullable) "int64" dtype column (e.g. firm_current's permission fields) casts fine when present."""
    dataset = make_dataset([])
    read_spec = ReadSpec(path="x/", pagination=PaginationKind.OFFSET, dtypes={"permission": "int64"})
    output = dataset._shape_output([{"permission": 1}], read_spec)
    assert output["permission"].dtype == "int64"
    assert output.iloc[0]["permission"] == 1


def test_shape_output_raises_read_error_when_a_missing_column_cant_hold_non_nullable_int64():
    """A declared int64 column absent from the response raises ReadError, not silently kept uncast.

    Regression test for a real interaction between two otherwise-separately-
    tested behaviors: a missing dtype column is added as all-null (NaN), and
    plain numpy "int64" (unlike "Int64") can't hold NaN, so the cast raises
    pandas.errors.IntCastingNaNError -- a ValueError subclass, so it's
    caught and wrapped as ReadError like any other bad cast.

    No real packaged product declares plain "int64" (the catalog fixed this
    exact issue by using nullable "Int64" for firm_current's permission
    fields instead), so this test constructs the scenario directly.
    """
    dataset = make_dataset([])
    read_spec = ReadSpec(path="x/", pagination=PaginationKind.OFFSET, dtypes={"permission": "int64"})
    with pytest.raises(ReadError) as exc_info:
        dataset._shape_output([{"id": "x"}], read_spec)  # no "permission" key at all
    assert exc_info.value.details["column"] == "permission"
    assert exc_info.value.details["dtype"] == "int64"


@pytest.mark.parametrize(
    ("dtype", "bad_value"),
    [
        ("Int64", "not-a-number"),
        ("int64", "not-a-number"),
        ("float64", "not-a-number"),
        ("boolean", "not-a-bool"),
        ("datetime64[ns, UTC]", "not-a-date"),
    ],
)
def test_shape_output_raises_read_error_for_every_castable_dtype_on_a_cast_failure(dtype, bad_value):
    """A value that can't be cast to its declared dtype raises ReadError -- for every dtype that can fail.

    "string"/"object" are deliberately excluded: astype() never fails for
    those (any value becomes a string repr, or stays as-is for object), so
    there's no failure case here.
    """
    dataset = make_dataset([])
    read_spec = ReadSpec(path="x/", pagination=PaginationKind.OFFSET, dtypes={"field": dtype})
    with pytest.raises(ReadError) as exc_info:
        dataset._shape_output([{"field": bad_value}], read_spec)
    assert exc_info.value.details["column"] == "field"
    assert exc_info.value.details["dtype"] == dtype


def test_shape_output_raises_read_error_when_a_record_is_missing_its_declared_record_path():
    """A record genuinely missing the declared record_path key (not just an empty list) raises ReadError."""
    dataset = make_dataset([])
    read_spec = ReadSpec(
        path="clients/detailed/",
        pagination=PaginationKind.ASYNC_OFFSET,
        deserializer_kwargs={"sep": "_", "record_path": ["projects"]},
    )
    records = [{"id": "c1", "name": "Acme"}]  # no "projects" key at all

    with pytest.raises(ReadError, match="record_path") as exc_info:
        dataset._shape_output(records, read_spec)
    assert exc_info.value.details["deserializer_kwargs"] == {"sep": "_", "record_path": ["projects"]}
    assert exc_info.value.details["path"] == "clients/detailed/"


def test_shape_output_explodes_record_path_without_meta_or_meta_prefix():
    """record_path alone (no meta/meta_prefix) still explodes correctly -- both are optional."""
    dataset = make_dataset([])
    read_spec = ReadSpec(
        path="clients/detailed/",
        pagination=PaginationKind.ASYNC_OFFSET,
        deserializer_kwargs={"record_path": ["projects"]},
        dtypes={"name": "string"},
    )
    records = [{"id": "c1", "projects": [{"name": "Project One"}, {"name": "Project Two"}]}]

    output = dataset._shape_output(records, read_spec)

    assert list(output["name"]) == ["Project One", "Project Two"]


def test_shape_output_explodes_record_path_bi_products():
    """_shape_output explodes a nested array via deserializer_kwargs (the bi-product mechanism)."""
    dataset = make_dataset([])
    read_spec = ReadSpec(
        path="clients/detailed/",
        pagination=PaginationKind.ASYNC_OFFSET,
        deserializer_kwargs={"record_path": ["projects"], "meta": ["id"], "meta_prefix": "client_"},
        dtypes={"id": "string", "name": "string", "client_id": "string"},
    )
    records = [{"id": "c1", "projects": [{"id": "p1", "name": "Project One"}, {"id": "p2", "name": "Project Two"}]}]

    output = dataset._shape_output(records, read_spec)

    assert len(output) == 2
    assert set(output["client_id"]) == {"c1"}
    assert set(output["id"]) == {"p1", "p2"}


# -----------------------------------------------------------------------------
# Contract: bi-products (packaged catalog entries exploding a parent's nested array)
# -----------------------------------------------------------------------------


def test_read_clients_detailed_projects_explodes_the_nested_projects_array():
    """A bi-product read() fully re-fetches its parent's endpoint and explodes the declared record_path."""
    responses = [
        FakeResponse(
            {
                "results": [
                    {
                        "id": "c1",
                        "projects": [
                            {"id": "p1", "name": "Project One"},
                            {"id": "p2", "name": "Project Two"},
                        ],
                    }
                ]
            }
        ),
        FakeResponse({"results": []}),
    ]
    dataset = make_dataset(responses, product=AdminflowProduct.CLIENTS_DETAILED_PROJECTS)
    dataset.read()

    assert dataset.linked_service.connection.requests[0]["url"] == "https://api.adminflow.no/api/accflow/clients/detailed/"
    assert len(dataset.output) == 2
    assert list(dataset.output["name"]) == ["Project One", "Project Two"]
    assert "client_id" not in dataset.output.columns


def test_read_clients_detailed_contact_persons_carries_the_parent_client_id():
    """Unlike clients_detailed_projects, this bi-product's packaged dtypes keep the meta-carried client_id."""
    responses = [
        FakeResponse({"results": [{"id": "c1", "contact_persons": [{"id": "cp1", "name": "Jane Doe", "is_main": True}]}]}),
        FakeResponse({"results": []}),
    ]
    dataset = make_dataset(responses, product=AdminflowProduct.CLIENTS_DETAILED_CONTACT_PERSONS)
    dataset.read()

    assert len(dataset.output) == 1
    assert dataset.output.iloc[0]["client_id"] == "c1"
    assert dataset.output.iloc[0]["name"] == "Jane Doe"


# -----------------------------------------------------------------------------
# Contract: custom (non-packaged) read via settings.read.path
# -----------------------------------------------------------------------------


def test_read_custom_path_populates_output():
    """settings.read.path/pagination reads an endpoint outside the packaged catalog."""
    responses = [
        FakeResponse({"results": [{"id": "x1", "name": "Custom Thing"}]}),
        FakeResponse({"results": []}),
    ]
    read = AdminflowReadSettings(
        path="custom/thing/",
        pagination=PaginationKind.OFFSET,
        dtypes={"id": "string", "name": "string"},
    )
    dataset = make_dataset(responses, product=None, read=read)
    dataset.read()

    assert len(dataset.output) == 1
    assert dataset.output.iloc[0]["id"] == "x1"
    assert dataset.linked_service.connection.requests[0]["url"] == "https://api.adminflow.no/api/accflow/custom/thing/"


def test_read_custom_path_respects_deserializer_kwargs_for_record_path():
    """A custom path's record_path/meta/meta_prefix shaping comes from dataset.deserializer, not settings.read."""
    responses = [FakeResponse({"projects": [{"id": "p1", "name": "Project One"}, {"id": "p2", "name": "Project Two"}]})]
    linked_service = make_linked_service(responses)
    settings = AdminflowDatasetSettings(
        read=AdminflowReadSettings(path="custom/thing/", pagination=PaginationKind.SINGLE, dtypes={"name": "string"}),
    )
    dataset = AdminflowDataset(
        id=uuid4(),
        name="test_dataset",
        version="1.0.0",
        linked_service=linked_service,
        settings=settings,
        deserializer=adminflow_mod.PandasDeserializer(
            format=adminflow_mod.DatasetStorageFormatType.SEMI_STRUCTURED_JSON,
            kwargs={"sep": "_", "record_path": ["projects"]},
        ),
    )
    dataset.read()

    assert list(dataset.output["name"]) == ["Project One", "Project Two"]


def test_resolve_read_spec_from_settings_raises_when_pagination_unset():
    """settings.read.path alone, without pagination, raises ReadError."""
    read = AdminflowReadSettings(path="custom/thing/")
    dataset = make_dataset([], product=None, read=read)
    with pytest.raises(ReadError, match=r"settings\.read\.pagination"):
        dataset.read()


def test_validate_settings_raises_when_product_and_custom_path_both_set():
    """settings.product and settings.read.path are mutually exclusive."""
    read = AdminflowReadSettings(path="custom/thing/", pagination=PaginationKind.OFFSET)
    dataset = make_dataset([], product=AdminflowProduct.CLIENTS, read=read)
    with pytest.raises(ReadError, match="ignored when product is set"):
        dataset.read()


def test_validate_settings_raises_when_product_and_deserializer_both_set():
    """A caller-supplied deserializer alongside product is a config conflict, not a silent merge."""
    linked_service = make_linked_service([])
    dataset = AdminflowDataset(
        id=uuid4(),
        name="test_dataset",
        version="1.0.0",
        linked_service=linked_service,
        settings=AdminflowDatasetSettings(product=AdminflowProduct.CLIENTS),
        deserializer=adminflow_mod.PandasDeserializer(
            format=adminflow_mod.DatasetStorageFormatType.SEMI_STRUCTURED_JSON, kwargs={}
        ),
    )
    with pytest.raises(ReadError, match="deserializer is ignored when product is set"):
        dataset.read()


def test_read_offset_merges_custom_params_without_overriding_pagination():
    """settings.read.params is merged into every page request, but never overrides offset/limit/order_by."""
    responses = [FakeResponse({"results": []})]
    read = AdminflowReadSettings(params={"isInactive": "true"})
    dataset = make_dataset(responses, product=AdminflowProduct.CLIENTS, read=read)
    dataset.read()

    params = dataset.linked_service.connection.requests[0]["params"]
    assert params["isInactive"] == "true"
    assert params["offset"] == 0
    assert params["order_by"] == "created_date"


def test_read_single_object_product_forwards_custom_params():
    """settings.read.params is forwarded to a single (non-paginated) product's request too."""
    responses = [FakeResponse({"id": "f1"})]
    read = AdminflowReadSettings(params={"foo": "bar"})
    dataset = make_dataset(responses, product=AdminflowProduct.FIRM_CURRENT, read=read)
    dataset.read()

    assert dataset.linked_service.connection.requests[0]["params"] == {"foo": "bar"}


@pytest.mark.parametrize("reserved_key", ["offset", "limit", "order_by"])
def test_validate_settings_raises_when_params_sets_a_reserved_key(reserved_key):
    """settings.read.params must not set offset/limit/order_by -- those are always computed internally."""
    read = AdminflowReadSettings(params={reserved_key: "x"})
    dataset = make_dataset([], product=AdminflowProduct.CLIENTS, read=read)
    with pytest.raises(ReadError, match="reserved for pagination"):
        dataset.read()


# -----------------------------------------------------------------------------
# Contract: pagination kinds not yet implemented (later phases)
# -----------------------------------------------------------------------------


def test_read_raises_for_not_yet_implemented_pagination_kind(monkeypatch):
    """A product resolving to a pagination kind with no fetch method yet raises ReadError.

    All three real PaginationKind members are implemented as of this phase --
    this exercises the dispatch's defensive fallback (protects against a
    future kind being added to the enum without a matching fetch method)
    using a stand-in value that bypasses PaginationKind's closed set,
    since ReadSpec itself doesn't enforce it at construction time.
    """
    stub_info = ReadSpec(path="clients/detailed/", pagination="not_a_real_kind")
    monkeypatch.setattr(adminflow_mod, "load_read_spec", lambda product: stub_info)
    dataset = make_dataset([], product=AdminflowProduct.CLIENTS_DETAILED)
    with pytest.raises(ReadError, match="not yet implemented"):
        dataset.read()


# -----------------------------------------------------------------------------
# Contract: validation / error wrapping
# -----------------------------------------------------------------------------


def test_read_wraps_missing_packaged_assets_as_read_error(monkeypatch):
    """load_read_spec()'s ValidationError (e.g. missing/malformed packaged assets) is wrapped as ReadError, not leaked."""

    def _raise_validation_error(product):
        raise ValidationError(message="boom", details={"product": product.value})

    monkeypatch.setattr(adminflow_mod, "load_read_spec", _raise_validation_error)
    dataset = make_dataset([], product=AdminflowProduct.CLIENTS)
    with pytest.raises(ReadError, match="boom"):
        dataset.read()


def test_read_raises_when_product_unset():
    """read() raises ReadError when settings.product is unset."""
    dataset = make_dataset([], product=None)
    with pytest.raises(ReadError, match=r"settings\.product"):
        dataset.read()


def test_read_raises_for_unknown_product():
    """read() raises ReadError for a product value that isn't a recognized AdminflowProduct."""
    dataset = make_dataset([])
    dataset.settings.product = "not-a-real-product"
    with pytest.raises(ReadError, match="Unknown AdminFlow product"):
        dataset.read()


def test_read_wraps_resource_exception_as_read_error():
    """A raw backend failure is wrapped as ReadError, never leaked as-is."""
    responses = [ResourceException(message="unexpected failure", status_code=500, details={})]
    dataset = make_dataset(responses, product=AdminflowProduct.CLIENTS)
    with pytest.raises(ReadError) as exc_info:
        dataset.read()
    assert exc_info.value.details["product"] == "clients"
    assert exc_info.value.details["path"] == "clients/"


def test_read_passes_through_authentication_error_unwrapped():
    """AuthenticationError propagates as itself, not reclassified as ReadError."""
    original = AuthenticationError(message="Authentication error: 401", details={})
    dataset = make_dataset([original], product=AdminflowProduct.CLIENTS)
    with pytest.raises(AuthenticationError) as exc_info:
        dataset.read()
    assert exc_info.value is original


def test_read_populates_partial_output_on_mid_pagination_failure():
    """self.output holds whatever rows were fetched before a failure, per the read() contract."""
    responses = [
        FakeResponse({"results": [{"id": "c1"}]}),
        ResourceException(message="unexpected failure", status_code=500, details={}),
    ]
    dataset = make_dataset(responses, product=AdminflowProduct.CLIENTS)
    with pytest.raises(ReadError):
        dataset.read()
    assert len(dataset.output) == 1
    assert dataset.output.iloc[0]["id"] == "c1"


def test_read_surfaces_the_original_passthrough_error_even_if_shaping_also_fails(monkeypatch):
    """A ConnectionError propagates even if shaping the partial records afterward also fails.

    Regression test: _shape_output_ignoring_errors must swallow a secondary
    shaping failure rather than let it pre-empt the real error -- see
    read()'s except (AuthenticationError, AuthorizationError, ConnectionError)
    clause.
    """
    original = ConnectionError(message="network down", details={})
    dataset = make_dataset([original], product=AdminflowProduct.CLIENTS)
    monkeypatch.setattr(dataset, "_shape_output", lambda records, read_spec: (_ for _ in ()).throw(RuntimeError("shaping bug")))
    with pytest.raises(ConnectionError) as exc_info:
        dataset.read()
    assert exc_info.value is original


def test_read_surfaces_read_error_even_if_shaping_also_fails(monkeypatch):
    """A ResourceException-derived ReadError propagates even if shaping the partial records afterward also fails."""
    responses = [ResourceException(message="unexpected failure", status_code=500, details={})]
    dataset = make_dataset(responses, product=AdminflowProduct.CLIENTS)
    monkeypatch.setattr(dataset, "_shape_output", lambda records, read_spec: (_ for _ in ()).throw(RuntimeError("shaping bug")))
    with pytest.raises(ReadError, match="unexpected failure"):
        dataset.read()


# -----------------------------------------------------------------------------
# Contract: write methods raise NotSupportedError (read-only provider)
# -----------------------------------------------------------------------------


@pytest.mark.parametrize("method_name", ["create", "update", "delete", "upsert", "rename", "purge", "list"])
def test_write_methods_raise_not_supported(method_name):
    """Every write/list method raises NotSupportedError."""
    dataset = make_dataset([])
    with pytest.raises(NotSupportedError):
        getattr(dataset, method_name)()


# -----------------------------------------------------------------------------
# close
# -----------------------------------------------------------------------------


def test_close_closes_linked_service():
    """close() delegates to the linked service's close()."""
    dataset = make_dataset([])
    dataset.close()  # Should not raise; DummyAdminflowLinkedService has no real HTTP client.
