"""
**File:** ``test_enums.py``
**Region:** ``tests``

Unit tests for AdminFlow provider enums.
"""

from ds_provider_adminflow_py_lib.enums import AdminflowProduct, OperationType, PaginationKind, ResourceType


def test_resource_type_values():
    """ResourceType members expose the expected identifiers."""
    assert ResourceType.LINKED_SERVICE == "ds.resource.linked-service.adminflow"
    assert ResourceType.DATASET == "ds.resource.dataset.adminflow"


def test_operation_type_values():
    """OperationType values are the packaged asset folder names under assets/<product>/<operation>/."""
    assert OperationType.READ == "read"
    assert OperationType.CREATE == "create"
    assert OperationType.UPDATE == "update"
    assert OperationType.DELETE == "delete"


def test_pagination_kind_values():
    """PaginationKind values match the literal "pagination" string declared in packaged metadata.json files."""
    assert PaginationKind.OFFSET == "offset"
    assert PaginationKind.ASYNC_OFFSET == "async_offset"
    assert PaginationKind.SINGLE == "single"


def test_adminflow_product_values_are_correct_identifiers():
    """Implemented products' values are AdminFlow's own camelCase product identifiers."""
    assert AdminflowProduct.CLIENTS.value == "clients"
    assert AdminflowProduct.ACCOUNTANTS.value == "accountants"
    assert AdminflowProduct.ACCOUNTANTS_DETAILED.value == "accountantsDetailed"
    assert AdminflowProduct.FIRM_CURRENT.value == "firmCurrent"
    assert AdminflowProduct.CLIENTS_DETAILED.value == "clientsDetailed"
    assert AdminflowProduct.PROJECTS_DETAILED.value == "projectsDetailed"
    assert AdminflowProduct.SELF_DECLARATION.value == "selfDeclaration"
