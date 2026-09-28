# ds-provider-adminflow-py-lib

A Python package from the ds-common library collection.

## Installation

Install the package using pip:

```bash
pip install ds-provider-adminflow-py-lib
```

Or using uv (recommended):

```bash
uv pip install ds-provider-adminflow-py-lib
```

## Quick Start

```python
from ds_provider_adminflow_py_lib import __version__

print(f"ds-provider-adminflow-py-lib version: {__version__}")
```

## Features

- Read-only `Dataset`/`LinkedService` provider for the AdminFlow
  accounting-firm API (`api.adminflow.no/api/accflow`), following the same
  `ds-resource-plugin-py-lib` contract as every other `ds-provider-*` package.
- Static per-tenant bearer token authentication -- no token-exchange/refresh
  flow required.
- A packaged product catalog (13 products, including `clients`,
  `accountants`, `firmCurrent`, `clientsDetailed`/`projectsDetailed` and
  their exploded sub-tables, and `selfDeclaration`) with offset, async-offset
  (202 + `Location` polling), and single-object pagination all handled
  automatically.
- Support for reading a custom (non-packaged) endpoint directly via
  `settings.read.path`/`pagination` when a product isn't in the catalog.
- Results are returned as pandas DataFrames via the standard `Dataset`
  contract (`dataset.read()` populates `dataset.output`).

## Usage

```python
from ds_provider_adminflow_py_lib.dataset.adminflow import (
    AdminflowDataset,
    AdminflowDatasetSettings,
)
from ds_provider_adminflow_py_lib.enums import AdminflowProduct
from ds_provider_adminflow_py_lib.linked_service.adminflow import (
    AdminflowLinkedService,
    AdminflowLinkedServiceSettings,
)

linked_service = AdminflowLinkedService(
    settings=AdminflowLinkedServiceSettings(token="my-bearer-token"),
)
dataset = AdminflowDataset(
    settings=AdminflowDatasetSettings(product=AdminflowProduct.CLIENTS),
    linked_service=linked_service,
)

linked_service.connect()
dataset.read()
data = dataset.output  # pandas.DataFrame
```

## Requirements

- Python 3.11 or higher
- `ds-resource-plugin-py-lib`, `ds-common-logger-py-lib`,
  `ds-common-serde-py-lib`, `ds-protocol-http-py-lib`, `pandas` -- installed
  automatically as dependencies.

## Documentation

Full documentation is available at:

- [GitHub Repository](https://github.com/grasp-labs/ds-provider-adminflow-py-lib)
- [Documentation Site](https://grasp-labs.github.io/ds-provider-adminflow-py-lib/)

## Development

To contribute or set up a development environment:

```bash
# Clone the repository
git clone https://github.com/grasp-labs/ds-provider-adminflow-py-lib.git
cd ds-provider-adminflow-py-lib

# Install development dependencies
uv sync --all-extras --dev

# Run tests
make test
```

See the
[README](https://github.com/grasp-labs/ds-provider-adminflow-py-lib#readme)
for more information.

## License

This package is licensed under the Apache License 2.0.
See the [LICENSE-APACHE](https://github.com/grasp-labs/ds-provider-adminflow-py-lib/blob/main/LICENSE-APACHE)
file for details.

## Support

- **Issues**: [GitHub Issues](https://github.com/grasp-labs/ds-provider-adminflow-py-lib/issues)
- **Releases**: [GitHub Releases](https://github.com/grasp-labs/ds-provider-adminflow-py-lib/releases)
