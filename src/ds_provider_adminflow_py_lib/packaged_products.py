"""
**File:** ``packaged_products.py``
**Region:** ``ds_provider_adminflow_py_lib/packaged_products``

Loads packaged read metadata for AdminFlow products into a :class:`~ds_provider_adminflow_py_lib.models.ReadSpec`.

Each :class:`~ds_provider_adminflow_py_lib.enums.AdminflowProduct` has a
packaged ``assets/<product>/read/metadata.json`` describing how to read it.
"""

import json
from importlib.resources import files
from typing import Any, cast

from ds_resource_plugin_py_lib.common.resource.errors import ValidationError

from .constants import DEFAULT_DATA_PATH, DEFAULT_LIMIT
from .enums import AdminflowProduct, OperationType, PaginationKind
from .models import ReadSpec


def _load_metadata(product: AdminflowProduct, operation: OperationType) -> dict[str, Any]:
    """
    Read and parse one product's packaged operation metadata.

    Args:
        product: AdminFlow product whose assets to load.
        operation: Operation whose metadata file to load.

    Returns:
        dict[str, Any]: Parsed JSON payload.

    Raises:
        FileNotFoundError: If the product has no packaged assets for that operation.
    """
    raw = (
        files("ds_provider_adminflow_py_lib")
        .joinpath("assets", product.value, operation.value, "metadata.json")
        .read_text(encoding="utf-8")
    )
    return cast("dict[str, Any]", json.loads(raw))


def load_read_spec(product: AdminflowProduct) -> ReadSpec:
    """
    Load packaged read metadata for an AdminFlow product.

    Args:
        product: AdminFlow product to load.

    Returns:
        ReadSpec: Read specification built from ``assets/<product>/read/metadata.json``.

    Raises:
        ValidationError: If the metadata file is missing, isn't valid JSON,
            or exists but is missing a required key (``path``, ``pagination``),
            isn't itself a JSON object, has a non-object ``deserializer``, or
            ``pagination`` is not a recognized
            :class:`~ds_provider_adminflow_py_lib.enums.PaginationKind` value.
    """
    try:
        payload = _load_metadata(product, OperationType.READ)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise ValidationError(
            message=f"Missing or malformed read metadata for AdminFlow product '{product.value}': {exc}",
            details={"product": product.value, "operation": OperationType.READ.value},
        ) from exc
    try:
        return ReadSpec(
            path=payload["path"],
            pagination=PaginationKind(payload["pagination"]),
            order_by=payload.get("order_by"),
            limit=payload.get("limit", DEFAULT_LIMIT),
            data_path=payload.get("data_path", DEFAULT_DATA_PATH),
            deserializer_kwargs={"sep": "_", **payload.get("deserializer", {}).get("kwargs", {})},
            dtypes=payload.get("dtypes", {}),
        )
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        raise ValidationError(
            message=f"Invalid read metadata for AdminFlow product '{product.value}': {exc}",
            details={"product": product.value, "operation": OperationType.READ.value},
        ) from exc
