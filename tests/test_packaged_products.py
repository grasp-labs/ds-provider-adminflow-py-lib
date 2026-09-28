"""
**File:** ``test_packaged_products.py``
**Region:** ``tests``

Unit tests for the AdminFlow packaged asset loader.
"""

import json

import pandas as pd
import pytest
from ds_resource_plugin_py_lib.common.resource.errors import ValidationError

from ds_provider_adminflow_py_lib.enums import AdminflowProduct, OperationType, PaginationKind
from ds_provider_adminflow_py_lib.packaged_products import _load_metadata, load_read_spec

# -----------------------------------------------------------------------------
# Full catalog: every AdminflowProduct member ships packaged read assets.
# -----------------------------------------------------------------------------

BI_PRODUCTS = [
    AdminflowProduct.CLIENTS_DETAILED_PROJECTS,
    AdminflowProduct.CLIENTS_DETAILED_CONTACT_PERSONS,
    AdminflowProduct.CLIENTS_DETAILED_CLIENT_ACCOUNTANTS,
    AdminflowProduct.CLIENTS_DETAILED_PROJECTS_PROJECT_ACCOUNTANTS,
    AdminflowProduct.PROJECTS_DETAILED_PROJECT_ACCOUNTANTS,
    AdminflowProduct.PROJECTS_DETAILED_PROJECT_PERSONS,
]

IMPLEMENTED_PRODUCTS = list(AdminflowProduct)


def test_all_catalog_products_have_read_assets():
    """Every AdminflowProduct member must have packaged read metadata."""
    for product in AdminflowProduct:
        load_read_spec(product)  # raises ValidationError if assets are missing/malformed


def test_implemented_products_have_a_non_empty_dtype_projection():
    """Every catalog product must declare at least one output column."""
    for product in IMPLEMENTED_PRODUCTS:
        info = load_read_spec(product)
        assert info.dtypes, f"{product!r} has an empty dtypes projection"
        assert info.path, f"{product!r} has an empty path"


def test_every_declared_dtype_is_a_real_pandas_dtype():
    """A typo'd dtype string (e.g. "strnig") wouldn't crash anything -- astype() failures are silently tolerated.

    So nothing else would catch it: the column would just quietly end up as
    plain "object" instead of the intended type, with no error anywhere.
    This is the one place that actually checks every declared dtype string,
    across every packaged product, is a real dtype pandas recognizes.
    """
    for product in IMPLEMENTED_PRODUCTS:
        info = load_read_spec(product)
        for column, dtype in info.dtypes.items():
            try:
                pd.Series([], dtype="object").astype(dtype)
            except (TypeError, ValueError) as exc:
                pytest.fail(f"{product!r} declares an invalid dtype for column {column!r}: {dtype!r} ({exc})")


def test_bi_products_declare_record_path_and_meta_prefix():
    """Bi-products explode a nested array out of their parent's payload, re-fetching the same endpoint.

    Each bi-product is its own catalog entry (its own read() call, its own
    re-fetch of the parent endpoint) -- see packaged_products.py for why there's no
    single-fetch-many-tables mechanism here.
    """
    for product in BI_PRODUCTS:
        info = load_read_spec(product)
        assert info.deserializer_kwargs.get("record_path"), f"{product!r} has no record_path"
        assert info.deserializer_kwargs.get("meta_prefix"), f"{product!r} has no meta_prefix"


def test_clients_detailed_projects_project_accountants_uses_nested_meta_path():
    """The doubly-nested bi-product carries both the client id and the parent project id."""
    info = load_read_spec(AdminflowProduct.CLIENTS_DETAILED_PROJECTS_PROJECT_ACCOUNTANTS)
    assert info.deserializer_kwargs["record_path"] == ["projects", "project_accountants"]
    assert info.deserializer_kwargs["meta"] == ["id", ["projects", "id"]]
    assert "client_id" in info.dtypes
    assert "client_projects_id" in info.dtypes


def test_firm_current_is_single_pagination_with_no_order_by():
    """firm_current is a single non-paginated object -- no order_by concept applies."""
    info = load_read_spec(AdminflowProduct.FIRM_CURRENT)
    assert info.pagination is PaginationKind.SINGLE
    assert info.order_by is None


def test_detailed_products_use_async_offset_pagination_ordered_by_created_date():
    """clients_detailed/projects_detailed use async_offset pagination, ordered by created_date."""
    for product in (AdminflowProduct.CLIENTS_DETAILED, AdminflowProduct.PROJECTS_DETAILED):
        info = load_read_spec(product)
        assert info.pagination is PaginationKind.ASYNC_OFFSET
        assert info.order_by == "created_date"
        assert info.limit == 100


def test_self_declaration_uses_async_offset_pagination_on_the_clients_detailed_path():
    """self_declaration reuses clients/detailed/'s async_offset pagination.

    The two-request-per-row resolution this product needs is triggered by
    checking settings.product directly in AdminflowDataset, not by a
    packaged/settings flag -- see dataset/adminflow.py's _fetch_async_offset.
    """
    info = load_read_spec(AdminflowProduct.SELF_DECLARATION)
    assert info.path == "clients/detailed/"
    assert info.pagination is PaginationKind.ASYNC_OFFSET
    assert info.order_by == "created_date"
    assert info.limit == 100


def test_clients_orders_by_created_date():
    """clients must pin order_by=created_date -- AdminFlow's default order isn't stable."""
    assert load_read_spec(AdminflowProduct.CLIENTS).order_by == "created_date"


def test_accountants_products_have_no_order_by():
    """accountants/accountants_detailed declare no order_by, matching the source registry."""
    assert load_read_spec(AdminflowProduct.ACCOUNTANTS).order_by is None
    assert load_read_spec(AdminflowProduct.ACCOUNTANTS_DETAILED).order_by is None


# -----------------------------------------------------------------------------
# Asset loading: only read/ exists today, but create/update/delete are
# supported by the layout for a future write-capable product.
# -----------------------------------------------------------------------------


def test_load_metadata_raises_for_unimplemented_operation():
    """No product defines create/update/delete assets yet -- loading one raises."""
    with pytest.raises(FileNotFoundError):
        _load_metadata(AdminflowProduct.CLIENTS, OperationType.CREATE)


# -----------------------------------------------------------------------------
# Fail loudly on missing or malformed read metadata.
# -----------------------------------------------------------------------------


def test_load_read_spec_raises_when_read_metadata_missing(monkeypatch):
    """load_read_spec() raises ValidationError when a product has no read assets."""

    def _raise_not_found(product: object, operation: object) -> None:
        raise FileNotFoundError

    monkeypatch.setattr("ds_provider_adminflow_py_lib.packaged_products._load_metadata", _raise_not_found)
    with pytest.raises(ValidationError) as exc_info:
        load_read_spec(AdminflowProduct.CLIENTS)
    assert "clients" in exc_info.value.message
    assert exc_info.value.details["product"] == "clients"


def test_load_read_spec_raises_on_malformed_json(monkeypatch):
    """A corrupted packaged metadata.json raises ValidationError, not a raw JSONDecodeError."""

    def _raise_decode_error(product: object, operation: object) -> None:
        raise json.JSONDecodeError("Expecting value", "not json", 0)

    monkeypatch.setattr("ds_provider_adminflow_py_lib.packaged_products._load_metadata", _raise_decode_error)
    with pytest.raises(ValidationError) as exc_info:
        load_read_spec(AdminflowProduct.CLIENTS)
    assert "clients" in exc_info.value.message


@pytest.mark.parametrize("missing_key", ["path", "pagination"])
def test_load_read_spec_raises_on_missing_key(monkeypatch, missing_key):
    """A metadata payload missing a required key raises ValidationError."""
    payload = {"path": "clients/", "pagination": "offset"}
    del payload[missing_key]
    monkeypatch.setattr(
        "ds_provider_adminflow_py_lib.packaged_products._load_metadata",
        lambda product, operation: payload,
    )
    with pytest.raises(ValidationError) as exc_info:
        load_read_spec(AdminflowProduct.CLIENTS)
    assert "clients" in exc_info.value.message
    assert exc_info.value.details["product"] == "clients"


def test_load_read_spec_defaults_optional_fields_when_absent(monkeypatch):
    """order_by/limit/data_path/dtypes are all optional; deserializer_kwargs always defaults to just sep."""
    payload = {"path": "clients/", "pagination": "offset"}
    monkeypatch.setattr(
        "ds_provider_adminflow_py_lib.packaged_products._load_metadata",
        lambda product, operation: payload,
    )
    info = load_read_spec(AdminflowProduct.CLIENTS)
    assert info.order_by is None
    assert info.limit == 100
    assert info.data_path == "results"
    assert info.deserializer_kwargs == {"sep": "_"}
    assert info.dtypes == {}


def test_load_read_spec_loads_deserializer_kwargs_from_nested_deserializer_key(monkeypatch):
    """deserializer_kwargs comes from a nested deserializer.kwargs object, mirroring the SOAP schema's shape."""
    payload = {
        "path": "clients/detailed/",
        "pagination": "async_offset",
        "deserializer": {"kwargs": {"record_path": ["projects"], "meta": ["id"], "meta_prefix": "client_"}},
    }
    monkeypatch.setattr(
        "ds_provider_adminflow_py_lib.packaged_products._load_metadata",
        lambda product, operation: payload,
    )
    info = load_read_spec(AdminflowProduct.CLIENTS)
    assert info.deserializer_kwargs == {
        "sep": "_",
        "record_path": ["projects"],
        "meta": ["id"],
        "meta_prefix": "client_",
    }


def test_load_read_spec_lets_a_packaged_product_override_the_sep_default(monkeypatch):
    """A packaged product's own deserializer.kwargs.sep wins over the sep='_' default."""
    payload = {
        "path": "clients/",
        "pagination": "offset",
        "deserializer": {"kwargs": {"sep": "."}},
    }
    monkeypatch.setattr(
        "ds_provider_adminflow_py_lib.packaged_products._load_metadata",
        lambda product, operation: payload,
    )
    info = load_read_spec(AdminflowProduct.CLIENTS)
    assert info.deserializer_kwargs == {"sep": "."}


def test_load_read_spec_raises_on_unrecognized_pagination_kind(monkeypatch):
    """An unrecognized pagination value raises ValidationError, not a silent fallback."""
    payload = {"path": "clients/", "pagination": "not-a-real-kind"}
    monkeypatch.setattr(
        "ds_provider_adminflow_py_lib.packaged_products._load_metadata",
        lambda product, operation: payload,
    )
    with pytest.raises(ValidationError):
        load_read_spec(AdminflowProduct.CLIENTS)


def test_load_read_spec_raises_on_non_object_payload(monkeypatch):
    """Valid JSON that isn't a JSON object (e.g. a list) raises ValidationError, not a raw TypeError."""
    monkeypatch.setattr(
        "ds_provider_adminflow_py_lib.packaged_products._load_metadata",
        lambda product, operation: ["not", "a", "dict"],
    )
    with pytest.raises(ValidationError) as exc_info:
        load_read_spec(AdminflowProduct.CLIENTS)
    assert "clients" in exc_info.value.message


def test_load_read_spec_raises_on_non_object_deserializer(monkeypatch):
    """A non-object deserializer key raises ValidationError, not a raw AttributeError."""
    payload = {"path": "clients/", "pagination": "offset", "deserializer": "not-an-object"}
    monkeypatch.setattr(
        "ds_provider_adminflow_py_lib.packaged_products._load_metadata",
        lambda product, operation: payload,
    )
    with pytest.raises(ValidationError) as exc_info:
        load_read_spec(AdminflowProduct.CLIENTS)
    assert "clients" in exc_info.value.message


def test_load_read_spec_raises_on_unhashable_pagination_value(monkeypatch):
    """An unhashable pagination value (e.g. a list) raises ValidationError, not a raw TypeError."""
    payload = {"path": "clients/", "pagination": ["offset"]}
    monkeypatch.setattr(
        "ds_provider_adminflow_py_lib.packaged_products._load_metadata",
        lambda product, operation: payload,
    )
    with pytest.raises(ValidationError) as exc_info:
        load_read_spec(AdminflowProduct.CLIENTS)
    assert "clients" in exc_info.value.message
