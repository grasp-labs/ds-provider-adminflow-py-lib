"""
**File:** ``enums.py``
**Region:** ``ds_provider_adminflow_py_lib/enums``

Constants for the AdminFlow provider.

Example:
    >>> ResourceType.LINKED_SERVICE
    'ds.resource.linked-service.adminflow'
    >>> ResourceType.DATASET
    'ds.resource.dataset.adminflow'
    >>> AdminflowProduct.CLIENTS
    'clients'
    >>> AdminflowProduct.CLIENTS_DETAILED
    'clientsDetailed'
"""

from enum import StrEnum


class ResourceType(StrEnum):
    """
    Constants for AdminFlow provider resource identifiers.
    """

    LINKED_SERVICE = "ds.resource.linked-service.adminflow"
    DATASET = "ds.resource.dataset.adminflow"


class OperationType(StrEnum):
    """
    Adminflow dataset operations (``read``, ``create``, ``update``, ``delete``).
    Only ``read`` is implemented.
    """

    READ = "read"
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


class PaginationKind(StrEnum):
    """
    How an AdminFlow product's ``read`` requests are fetched.

    Declared per product in ``assets/<product>/read/metadata.json`` for a
    packaged product, or set directly via ``AdminflowReadSettings.pagination``
    for a custom (non-packaged) path.
    """

    OFFSET = "offset"
    """Plain ``offset``/``limit`` pagination against a synchronous endpoint."""

    ASYNC_OFFSET = "async_offset"
    """``offset``/``limit`` pagination where any page may reply ``202`` with a
    ``Location`` header instead of the page itself -- that URL is polled
    until it resolves, before the next page is requested."""

    SINGLE = "single"
    """A single, non-paginated ``GET`` returning one JSON object (one row)."""


class AdminflowProduct(StrEnum):
    """
    AdminFlow products packaged with this provider (see ``packaged_products.py``).

    Each value is the folder name under ``assets/<product>/read/`` whose
    packaged metadata drives that product's read.
    """

    CLIENTS = "clients"
    ACCOUNTANTS = "accountants"
    ACCOUNTANTS_DETAILED = "accountantsDetailed"
    FIRM_CURRENT = "firmCurrent"
    CLIENTS_DETAILED = "clientsDetailed"
    CLIENTS_DETAILED_PROJECTS = "clientsDetailedProjects"
    CLIENTS_DETAILED_CONTACT_PERSONS = "clientsDetailedContactPersons"
    CLIENTS_DETAILED_CLIENT_ACCOUNTANTS = "clientsDetailedClientAccountants"
    CLIENTS_DETAILED_PROJECTS_PROJECT_ACCOUNTANTS = "clientsDetailedProjectsProjectAccountants"
    PROJECTS_DETAILED = "projectsDetailed"
    PROJECTS_DETAILED_PROJECT_ACCOUNTANTS = "projectsDetailedProjectAccountants"
    PROJECTS_DETAILED_PROJECT_PERSONS = "projectsDetailedProjectPersons"
    SELF_DECLARATION = "selfDeclaration"
