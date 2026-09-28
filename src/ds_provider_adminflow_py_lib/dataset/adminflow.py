"""
**File:** ``adminflow.py``
**Region:** ``ds_provider_adminflow_py_lib/dataset``

Read-only dataset for the AdminFlow accounting-firm API.

A product is selected either via ``settings.product`` (the packaged
catalog) or, for an endpoint not in that catalog, via
``settings.read.path``/``pagination`` directly.

Example:
    >>> dataset = AdminflowDataset(
    ...     settings=AdminflowDatasetSettings(product=AdminflowProduct.CLIENTS),
    ...     linked_service=AdminflowLinkedService(
    ...         settings=AdminflowLinkedServiceSettings(token="my-bearer-token"),
    ...     ),
    ... )
    >>> dataset.linked_service.connect()
    >>> dataset.read()
    >>> data = dataset.output
"""

import time
from dataclasses import dataclass, field, replace
from typing import Any, Generic, NoReturn, TypeVar
from urllib.parse import urljoin

import pandas as pd
from ds_common_logger_py_lib import Logger
from ds_common_serde_py_lib import DeserializationError, Serializable
from ds_resource_plugin_py_lib.common.resource.dataset import DatasetSettings, TabularDataset
from ds_resource_plugin_py_lib.common.resource.dataset.errors import ReadError
from ds_resource_plugin_py_lib.common.resource.dataset.storage_format import DatasetStorageFormatType
from ds_resource_plugin_py_lib.common.resource.errors import NotSupportedError, ResourceException, ValidationError
from ds_resource_plugin_py_lib.common.resource.linked_service.errors import (
    AuthenticationError,
    AuthorizationError,
    ConnectionError,
)
from ds_resource_plugin_py_lib.common.serde.deserialize import PandasDeserializer
from ds_resource_plugin_py_lib.common.serde.serialize import PandasSerializer

from ..constants import (
    DEFAULT_DATA_PATH,
    DEFAULT_LIMIT,
    POLL_INITIAL_INTERVAL_SECONDS,
    POLL_MAX_INTERVAL_SECONDS,
    POLL_TIMEOUT_SECONDS,
    RESERVED_PAGE_PARAMS,
)
from ..enums import AdminflowProduct, PaginationKind, ResourceType
from ..linked_service.adminflow import AdminflowLinkedService
from ..models import ReadSpec
from ..packaged_products import load_read_spec

logger = Logger.get_logger(__name__, package=True)


@dataclass(kw_only=True)
class AdminflowReadSettings(Serializable):
    """Settings for AdminFlow read operations.

    ``path``/``pagination`` let a caller read a custom (non-packaged)
    endpoint instead of ``product`` -- exactly one of the two may be set.
    ``limit``/``params`` are shared by both modes.

    Example:
        >>> AdminflowReadSettings(path="custom/thing/", pagination=PaginationKind.OFFSET)
    """

    limit: int | None = None
    """Page size override. Uses the packaged product's own default when unset."""

    params: dict[str, Any] | None = None
    """Extra query params merged into every page request. Never
    ``offset``/``limit``/``order_by`` -- use the dedicated fields."""

    path: str | None = None
    """Custom API path. Set together with ``pagination`` instead of ``product``."""

    pagination: PaginationKind | None = None
    """Required when ``path`` is set. Ignored when ``product`` is set."""

    order_by: str | None = None
    """Query parameter value ordering pages. Ignored when ``product`` is set."""

    data_path: str | None = None
    """JSON key holding the page's row list. Ignored when ``product`` is set."""

    dtypes: dict[str, str] = field(default_factory=dict)
    """Column projection + pandas dtype per surviving column. Ignored when ``product`` is set."""


@dataclass(kw_only=True)
class AdminflowDatasetSettings(DatasetSettings):
    """Settings for the AdminFlow dataset."""

    product: AdminflowProduct | None = None
    """AdminFlow product to read."""

    read: AdminflowReadSettings = field(default_factory=AdminflowReadSettings)
    """Settings for ``read()``."""


AdminflowDatasetSettingsType = TypeVar(
    "AdminflowDatasetSettingsType",
    bound=AdminflowDatasetSettings,
)
AdminflowLinkedServiceType = TypeVar(
    "AdminflowLinkedServiceType",
    bound=AdminflowLinkedService[Any],
)


@dataclass(kw_only=True)
class AdminflowDataset(
    TabularDataset[AdminflowLinkedServiceType, AdminflowDatasetSettingsType, PandasSerializer, PandasDeserializer],
    Generic[AdminflowLinkedServiceType, AdminflowDatasetSettingsType],
):
    """Read-only tabular dataset for AdminFlow products.

    Exposed (caller-configurable): ``id``, ``name``, ``description``,
    ``version``, ``settings``, ``linked_service``, ``serializer``,
    ``deserializer``, ``checkpoint``. No field is fully internal.
    """

    linked_service: AdminflowLinkedServiceType
    settings: AdminflowDatasetSettingsType

    @property
    def type(self) -> ResourceType:
        """Return the dataset resource type."""
        return ResourceType.DATASET

    def _resolve_product(self, product: str) -> AdminflowProduct:
        """Resolve a raw ``settings.product`` value into an ``AdminflowProduct``.

        Args:
            product: The raw product value to validate.

        Returns:
            AdminflowProduct: The resolved product.

        Raises:
            ReadError: If not a recognized product value.
        """
        try:
            return AdminflowProduct(product)
        except ValueError as exc:
            raise ReadError(
                message=f"Unknown AdminFlow product: {product!r}",
                details={"type": self.type.value, "product": product},
            ) from exc

    def _resolve_packaged_read_spec(self, product: str) -> ReadSpec:
        """Resolve the read spec for a packaged product, applying ``settings.read.limit`` as an override.

        Args:
            product: The raw ``settings.product`` value to resolve.

        Returns:
            ReadSpec: The packaged product's read specification.

        Raises:
            ReadError: If not a recognized product, or its packaged read
                metadata is missing or malformed.
        """
        logger.info("Reading AdminFlow product: %s", product)
        resolved_product = self._resolve_product(product)
        try:
            read_spec = load_read_spec(resolved_product)
        except ValidationError as exc:
            raise ReadError(
                message=exc.message,
                details={**exc.details, "type": self.type.value},
            ) from exc
        if self.settings.read.limit:
            read_spec = replace(read_spec, limit=self.settings.read.limit)
        return read_spec

    def _resolve_read_spec_from_settings(self) -> ReadSpec:
        """Build a ``ReadSpec`` from ``settings.read``, for a custom (non-packaged) endpoint.

        Returns:
            ReadSpec: The endpoint built from ``settings.read``.

        Raises:
            ReadError: If ``path``/``pagination`` aren't both set.
        """
        if not self.settings.read.path:
            raise ReadError(
                message="Either settings.product or settings.read.path must be specified.",
                details={"type": self.type.value},
            )
        if not self.settings.read.pagination:
            raise ReadError(
                message="settings.read.pagination must be specified when settings.read.path is set.",
                details={"type": self.type.value, "path": self.settings.read.path},
            )
        logger.info("Reading custom AdminFlow path: %s", self.settings.read.path)
        return ReadSpec(
            path=self.settings.read.path,
            pagination=self.settings.read.pagination,
            order_by=self.settings.read.order_by,
            limit=self.settings.read.limit or DEFAULT_LIMIT,
            data_path=self.settings.read.data_path or DEFAULT_DATA_PATH,
            dtypes=self.settings.read.dtypes,
        )

    def _validate_settings(self) -> None:
        """Enforce valid settings combinations before reading.

        Raises:
            ReadError: If any custom-path ``read.*`` setting, or a
                ``deserializer``, is set alongside ``product``, or
                ``settings.read.params`` sets a reserved pagination param.
        """
        if self.settings.product and (
            self.settings.read.path
            or self.settings.read.pagination
            or self.settings.read.order_by
            or self.settings.read.data_path
            or self.settings.read.dtypes
        ):
            raise ReadError(
                message=(
                    "settings.read.path, pagination, order_by, data_path, and dtypes are "
                    "ignored when product is set -- remove them, or use read.path instead of product."
                ),
                details={"type": self.type.value, "product": str(self.settings.product)},
            )

        if self.settings.product and self.deserializer is not None:
            raise ReadError(
                message=(
                    "deserializer is ignored when product is set -- a packaged product's shaping "
                    "comes from its own catalog entry, not a caller-supplied deserializer. Remove "
                    "it, or use read.path instead of product."
                ),
                details={"type": self.type.value, "product": str(self.settings.product)},
            )

        reserved_collisions = RESERVED_PAGE_PARAMS & (self.settings.read.params or {}).keys()
        if reserved_collisions:
            raise ReadError(
                message=(
                    f"settings.read.params must not set {sorted(reserved_collisions)} -- "
                    "these are reserved for pagination and are always set internally. "
                    "Use the dedicated settings.read.limit/order_by fields instead."
                ),
                details={"type": self.type.value, "reserved_params": sorted(RESERVED_PAGE_PARAMS)},
            )

    def _resolve_read_spec(self) -> ReadSpec:
        """Resolve the endpoint to read: a packaged product, or a custom path.

        Returns:
            ReadSpec: The fully resolved endpoint to read.

        Raises:
            ReadError: If the resolved product/metadata is unrecognized/malformed.
        """
        if self.settings.product:
            return self._resolve_packaged_read_spec(self.settings.product)
        return self._resolve_read_spec_from_settings()

    def read(self) -> None:
        """Fetch all rows for the configured product and assign them to ``self.output``.

        Raises:
            AuthenticationError: If authentication fails.
            AuthorizationError: If authorization fails.
            ConnectionError: If the transport cannot reach AdminFlow.
            ReadError: If the settings are invalid, or the read fails.
        """
        self._validate_settings()
        read_spec = self._resolve_read_spec()
        records: list[dict[str, Any]] = []
        try:
            self._fetch_records(read_spec, records)
        except (AuthenticationError, AuthorizationError, ConnectionError):
            # Already ResourceException subclasses -- re-raise as themselves,
            # ahead of the broader `except ResourceException` below.
            self._shape_output_ignoring_errors(records, read_spec)
            raise
        except ResourceException as exc:
            self._shape_output_ignoring_errors(records, read_spec)
            raise ReadError(
                message=exc.message,
                status_code=exc.status_code,
                details={**exc.details, "type": self.type.value, "product": self._product_name(), "path": read_spec.path},
            ) from exc
        else:
            self.output = self._shape_output(records, read_spec)

    def _shape_output_ignoring_errors(self, records: list[dict[str, Any]], read_spec: ReadSpec) -> None:
        """Best-effort shape of a failed read's partial ``records``, without letting a shaping bug mask the real error.

        Args:
            records: Rows fetched before the failure (possibly empty).
            read_spec: The resolved endpoint, for shaping.
        """
        try:
            self.output = self._shape_output(records, read_spec)
        except Exception:
            logger.warning("Failed to shape partial output after a read failure; self.output left unset.", exc_info=True)

    def _build_url(self, path: str) -> str:
        """Join the linked service's host with a path segment.

        Args:
            path: API path segment, e.g. ``read_spec.path`` or
                ``f"self-declaration/{id}"`` -- no leading slash.

        Returns:
            str: The full request URL.
        """
        return f"{self.linked_service.settings.host}/{path}"

    def _build_page_params(self, offset: int, read_spec: ReadSpec) -> dict[str, Any]:
        """Build one page's query params, merging ``settings.read.params`` first so pagination params always win.

        Args:
            offset: Current pagination offset.
            read_spec: The resolved endpoint -- ``limit``/``order_by`` are used.

        Returns:
            dict[str, Any]: The params for this page's request.
        """
        params: dict[str, Any] = dict(self.settings.read.params or {})
        params["offset"] = offset
        params["limit"] = read_spec.limit
        if read_spec.order_by:
            params["order_by"] = read_spec.order_by
        return params

    def _fetch_records(self, read_spec: ReadSpec, records: list[dict[str, Any]]) -> None:
        """Fetch every row for ``read_spec`` into ``records``, dispatching by pagination kind.

        Args:
            read_spec: The resolved endpoint to read.
            records: Accumulator appended to in place.

        Raises:
            ReadError: If ``read_spec.pagination`` has no fetch implementation yet.
        """
        url = self._build_url(read_spec.path)
        if read_spec.pagination == PaginationKind.OFFSET:
            self._fetch_offset(url, read_spec, records)
            return
        if read_spec.pagination == PaginationKind.SINGLE:
            self._fetch_single(url, records)
            return
        if read_spec.pagination == PaginationKind.ASYNC_OFFSET:
            self._fetch_async_offset(url, read_spec, records)
            return

        pagination = getattr(read_spec.pagination, "value", read_spec.pagination)  # type: ignore[unreachable]
        raise ReadError(
            message=f"Pagination kind {pagination!r} is not yet implemented.",
            details={"type": self.type.value, "path": read_spec.path, "pagination": pagination},
        )

    def _fetch_offset(self, url: str, read_spec: ReadSpec, records: list[dict[str, Any]]) -> None:
        """Page through ``url`` via plain ``offset``/``limit`` pagination, stopping once a page comes back empty.

        Args:
            url: The request URL, built by :meth:`_fetch_records` from ``read_spec.path``.
            read_spec: The resolved endpoint to read.
            records: Accumulator appended to in place.
        """
        offset = 0

        while True:
            params = self._build_page_params(offset, read_spec)
            response = self.linked_service.connection.get(url=url, params=params)
            body = response.json()
            page = body if isinstance(body, list) else body.get(read_spec.data_path, [])
            if not page:
                break

            records.extend(page)
            offset += len(page)

    def _fetch_single(self, url: str, records: list[dict[str, Any]]) -> None:
        """Fetch ``url`` as one non-paginated ``GET`` returning a single JSON object, wrapped as ``[body]``.

        Args:
            url: The request URL, built by :meth:`_fetch_records` from ``read_spec.path``.
            records: Accumulator appended to in place.
        """
        response = self.linked_service.connection.get(url=url, params=self.settings.read.params or None)
        records.append(response.json())

    def _fetch_async_offset(self, url: str, read_spec: ReadSpec, records: list[dict[str, Any]]) -> None:
        """Page through ``url`` like :meth:`_fetch_offset`, resolving any ``202`` page first.

        Offset always advances by the raw fetched page's own length -- for
        ``self_declaration``, fewer rows (or none) may end up in ``records``
        (see :meth:`_fetch_self_declarations``), but the offset still
        advances by the full page size.

        Args:
            url: The request URL, built by :meth:`_fetch_records` from ``read_spec.path``.
            read_spec: The resolved endpoint to read.
            records: Accumulator appended to in place.
        """
        offset = 0

        while True:
            params = self._build_page_params(offset, read_spec)
            response = self.linked_service.connection.get(url=url, params=params)
            body = self._resolve_async_response(response, url)
            page = body if isinstance(body, list) else body.get(read_spec.data_path, [])
            if not page:
                break

            if self.settings.product == AdminflowProduct.SELF_DECLARATION:
                records.extend(self._fetch_self_declarations(page))
            else:
                records.extend(page)
            offset += len(page)

    def _fetch_self_declarations(self, detailed_page: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Resolve each ``detailed_page`` row's ``last_self_declaration.id`` into its own record.

        Rows missing one (absent, or explicitly ``null``) are skipped, and
        a failed per-id fetch is logged and skipped rather than aborting
        the read.

        Args:
            detailed_page: The raw, unfiltered ``clientsDetailed``-shaped page.

        Returns:
            list[dict]: The resolved self-declaration objects for this page --
                may be fewer than ``len(detailed_page)``, or empty. Callers
                must advance the pagination offset by ``len(detailed_page)``,
                never by this return value's length.

        Raises:
            AuthenticationError: If authentication fails.
            AuthorizationError: If authorization fails.
            ConnectionError: If the transport cannot reach AdminFlow.
        """
        self_declaration_ids = [
            self_declaration_id
            for detail in detailed_page
            if (self_declaration_id := (detail.get("last_self_declaration") or {}).get("id"))
        ]
        logger.info("Fetching (%s) SelfDeclaration IDs...", len(self_declaration_ids))

        resolved: list[dict[str, Any]] = []
        for self_declaration_id in self_declaration_ids:
            url = self._build_url(f"self-declaration/{self_declaration_id}")
            try:
                response = self.linked_service.connection.get(url=url)
            except (AuthenticationError, AuthorizationError, ConnectionError):
                # Already ResourceException subclasses -- re-raise as themselves,
                # ahead of the broader `except ResourceException` below. A transport
                # or auth failure isn't an ordinary per-id lookup failure; it must
                # propagate per read()'s contract, not be swallowed as partial data.
                raise
            except ResourceException as exc:
                logger.error("Error fetching self declaration %s: %s", self_declaration_id, exc)
                continue
            resolved.append(response.json())

        return resolved

    def _resolve_async_response(self, response: Any, request_url: str) -> Any:
        """Resolve a possibly-``202`` response by polling its ``Location`` header until it settles.

        Args:
            response: The initial page response, possibly ``202``.
            request_url: The URL that produced ``response`` -- resolved
                against ``Location``.

        Returns:
            Any: The resolved response's parsed JSON body.

        Raises:
            ResourceException: If polling exceeds ``POLL_TIMEOUT_SECONDS`` without resolving.
        """
        if response.status_code != 202:
            return response.json()

        location_url = urljoin(request_url, response.headers["Location"])
        interval = POLL_INITIAL_INTERVAL_SECONDS
        started_at = time.monotonic()

        while True:
            elapsed = time.monotonic() - started_at
            if elapsed > POLL_TIMEOUT_SECONDS:
                raise ResourceException(
                    message=f"Polling {location_url} exceeded {POLL_TIMEOUT_SECONDS}s",
                    status_code=504,
                    details={"location_url": location_url, "elapsed_seconds": elapsed},
                )

            response = self.linked_service.connection.get(url=location_url)
            if response.status_code != 202:
                return response.json()

            logger.info("Waiting for %s to be ready...", location_url)
            time.sleep(interval)
            interval = min(interval * 2, POLL_MAX_INTERVAL_SECONDS)

    def _resolve_deserializer(self, read_spec: ReadSpec) -> PandasDeserializer:
        """Resolve the deserializer to shape records with: packaged config, or the caller's own.

        Args:
            read_spec: The resolved endpoint, whose ``deserializer_kwargs``
                drive packaged-product shaping.

        Returns:
            PandasDeserializer: The deserializer to shape ``records`` with.
        """
        if self.settings.product:
            return PandasDeserializer(
                format=DatasetStorageFormatType.SEMI_STRUCTURED_JSON,
                kwargs=read_spec.deserializer_kwargs,
            )
        return self.deserializer or PandasDeserializer(format=DatasetStorageFormatType.SEMI_STRUCTURED_JSON, kwargs={"sep": "_"})

    def _product_name(self) -> Any:
        """Resolve ``settings.product`` into its raw value for error ``details``, tolerating a non-enum override.

        Returns:
            Any: ``settings.product.value`` if it's a real ``AdminflowProduct``,
                else ``settings.product`` as-is.
        """
        return getattr(self.settings.product, "value", self.settings.product)

    def _deserialize_records(self, records: list[dict[str, Any]], read_spec: ReadSpec) -> pd.DataFrame:
        """Flatten fetched rows into a ``DataFrame``.

        Args:
            records: Rows fetched by ``_fetch_records`` (possibly partial, if a failure occurred).
            read_spec: The resolved endpoint, whose ``deserializer_kwargs`` drive the shaping.

        Returns:
            pd.DataFrame: The flattened output, columns/dtypes as ``pandas.json_normalize`` produced them.

        Raises:
            ReadError: If the records can't be deserialized.
        """
        try:
            return self._resolve_deserializer(read_spec)(records)
        except DeserializationError as exc:
            logger.error("Failed to deserialize records with kwargs %r", read_spec.deserializer_kwargs, exc_info=True)
            raise ReadError(
                message=f"Failed to deserialize records with kwargs {read_spec.deserializer_kwargs!r}: {exc.message}",
                status_code=exc.status_code,
                details={
                    **exc.details,
                    "type": self.type.value,
                    "product": self._product_name(),
                    "path": read_spec.path,
                    "deserializer_kwargs": read_spec.deserializer_kwargs,
                },
            ) from exc

    def _cast_dtypes(self, df: pd.DataFrame, read_spec: ReadSpec) -> pd.DataFrame:
        """Project ``df`` to ``read_spec.dtypes``' columns and cast each to its declared dtype.

        Args:
            df: DataFrame from :meth:`_deserialize_records`.
            read_spec: The resolved endpoint, whose ``dtypes`` drive the projection/casting.

        Returns:
            pd.DataFrame: ``df``, projected to ``read_spec.dtypes``'
                columns (dropped if undeclared, added as all-null if
                missing) and cast to each column's declared dtype. Returned
                unchanged if ``read_spec.dtypes`` is empty.

        Raises:
            ReadError: If a column's value can't be cast to its declared dtype.
        """
        if not read_spec.dtypes:
            return df

        df = df.reindex(columns=sorted(read_spec.dtypes))
        for column, dtype in read_spec.dtypes.items():
            try:
                df[column] = df[column].astype(dtype)  # type: ignore[call-overload]
            except (TypeError, ValueError) as exc:
                logger.error("Failed to cast column %r to dtype %r", column, dtype, exc_info=True)
                raise ReadError(
                    message=f"Failed to cast column {column!r} to dtype {dtype!r}: {exc}",
                    details={
                        "type": self.type.value,
                        "product": self._product_name(),
                        "path": read_spec.path,
                        "column": column,
                        "dtype": dtype,
                    },
                ) from exc
        return df

    def _normalize_column_names(self, df: pd.DataFrame) -> pd.DataFrame:
        """Replace ``-`` with ``_`` in column names produced by flattening a hyphenated JSON key."""

        # Not df.columns.str.replace(): an empty DataFrame's columns are a
        # plain RangeIndex, not a string Index, and .str raises on it.
        df.columns = [col.replace("-", "_") if isinstance(col, str) else col for col in df.columns]
        return df

    def _shape_output(self, records: list[dict[str, Any]], read_spec: ReadSpec) -> pd.DataFrame:
        """Flatten fetched rows into the final, column-projected, dtype-cast ``DataFrame``.

        Args:
            records: Rows fetched by ``_fetch_records`` (possibly partial, if a failure occurred).
            read_spec: The resolved endpoint, whose ``deserializer_kwargs``/``dtypes`` drive the shaping.

        Returns:
            pd.DataFrame: The flattened, projected output.
        """
        df = self._deserialize_records(records, read_spec)
        df = self._normalize_column_names(df)
        return self._cast_dtypes(df, read_spec)

    def create(self) -> NoReturn:
        """Create is not supported by the AdminFlow provider."""
        raise NotSupportedError("Create operation is not supported for AdminFlow datasets")

    def update(self) -> NoReturn:
        """Update is not supported by the AdminFlow provider."""
        raise NotSupportedError("Update operation is not supported for AdminFlow datasets")

    def delete(self) -> NoReturn:
        """Delete is not supported by the AdminFlow provider."""
        raise NotSupportedError("Delete operation is not supported for AdminFlow datasets")

    def upsert(self) -> NoReturn:
        """Upsert is not supported by the AdminFlow provider."""
        raise NotSupportedError("Upsert operation is not supported for AdminFlow datasets")

    def rename(self) -> NoReturn:
        """Rename is not supported by the AdminFlow provider."""
        raise NotSupportedError("Rename operation is not supported for AdminFlow datasets")

    def purge(self) -> NoReturn:
        """Purge is not supported by the AdminFlow provider."""
        raise NotSupportedError("Purge operation is not supported for AdminFlow datasets")

    def list(self) -> NoReturn:
        """List is not supported by the AdminFlow provider."""
        raise NotSupportedError("List operation is not supported for AdminFlow datasets")

    def close(self) -> None:
        """Close the linked-service connection."""
        self.linked_service.close()
