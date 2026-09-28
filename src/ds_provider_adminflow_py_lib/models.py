"""
**File:** ``models.py``
**Region:** ``ds_provider_adminflow_py_lib/models``

Typed data models shared across the AdminFlow provider package.
"""

from dataclasses import dataclass, field
from typing import Any

from .constants import DEFAULT_DATA_PATH, DEFAULT_LIMIT
from .enums import PaginationKind


@dataclass(frozen=True)
class ReadSpec:
    """
    Read specification for a packaged AdminFlow product, or a custom (non-packaged) path.

    Attributes:
        path: API path segment appended to the linked service host, e.g. ``"clients/detailed/"``.
        pagination: The pagination mechanism this endpoint uses -- see :class:`~ds_provider_adminflow_py_lib.enums.PaginationKind`.
        order_by: Query parameter value ordering pages, e.g. ``"created_date"``.
            Keeps row order stable across pages of a single paginated walk.
        limit: Page size sent as the ``limit`` query parameter.
        data_path: JSON key holding the page's row list, e.g. ``"results"``.
            Used while fetching, to pull each page's rows out of the raw
            response
        deserializer_kwargs: Fully-resolved ``pandas.json_normalize`` kwargs
            for this product -- always includes ``sep="_"`` (overridable),
            plus e.g. ``record_path``/``meta``/``meta_prefix`` for a
            bi-product exploding a nested array out of its parent product's
            row shape. Only meaningful for a packaged product -- a custom
            path's shaping comes from ``AdminflowDataset.deserializer``
            instead, so this is always ``{}`` there.
        dtypes: Column projection + pandas dtype string per surviving column,
            e.g. ``{"id": "string", "created_date": "datetime64[ns, UTC]"}``.
            Columns not listed here are dropped; columns listed but absent
            from the response are added as all-null.
    """

    path: str
    pagination: PaginationKind
    order_by: str | None = None
    limit: int = DEFAULT_LIMIT
    data_path: str = DEFAULT_DATA_PATH
    deserializer_kwargs: dict[str, Any] = field(default_factory=dict)
    dtypes: dict[str, str] = field(default_factory=dict)
