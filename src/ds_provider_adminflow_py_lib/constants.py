"""
**File:** ``constants.py``
**Region:** ``ds_provider_adminflow_py_lib/constants``

Shared constants used across the AdminFlow provider package.
"""

# Fallback page size and response-list key, used when a packaged product's
# metadata or a caller's settings.read override omits limit/data_path.
DEFAULT_LIMIT = 100
DEFAULT_DATA_PATH = "results"

# Async ("202 + Location") polling schedule: start at 2s, double each miss, cap
# at 20s, give up (convert to a retryable failure) after 300s total.
POLL_INITIAL_INTERVAL_SECONDS = 2.0
POLL_MAX_INTERVAL_SECONDS = 20.0
POLL_TIMEOUT_SECONDS = 300.0

# Query parameter names computed internally for pagination -- reserved so
# settings.read.params can never silently override them (see
# AdminflowDataset._validate_settings).
RESERVED_PAGE_PARAMS = frozenset({"offset", "limit", "order_by"})
