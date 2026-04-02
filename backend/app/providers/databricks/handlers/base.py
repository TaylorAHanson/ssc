from abc import ABC, abstractmethod
from typing import List, Dict, Any
import logging

logger = logging.getLogger(__name__)

class BaseResourceHandler(ABC):
    """
    Abstract base class for Databricks resource handlers.
    Each handler is responsible for a specific resource type (e.g. apps, clusters, jobs)
    and provides methods to discover, kill, and warn owners.
    """
    def __init__(self, workspace_client):
        self.workspace_client = workspace_client

    @abstractmethod
    async def discover(self) -> List[Dict[str, Any]]:
        """
        Query Databricks and return a list of resources of this type.
        Each resource must have at least an 'id' and 'type'.
        """
        pass

    @abstractmethod
    async def kill(self, resource_id: str) -> bool:
        """
        Execute the destructive action for this specific resource type.
        Returns True if successful.
        """
        pass

    @abstractmethod
    async def warn(self, resource_id: str, message: str) -> bool:
        """
        Send a targeted warning to the resource owner.
        Returns True if successful.
        """
        pass
