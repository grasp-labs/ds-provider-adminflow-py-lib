"""
**File:** ``__init__.py``
**Region:** ``ds_provider_adminflow_py_lib/linked_service``

Description
-----------
Linked service package for the AdminFlow provider.
"""

from .adminflow import AdminflowLinkedService, AdminflowLinkedServiceSettings

__all__ = ["AdminflowLinkedService", "AdminflowLinkedServiceSettings"]
