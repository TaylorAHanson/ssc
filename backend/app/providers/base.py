"""
Base provider interface.
"""
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional


class BaseProvider(ABC):
    """Base class for all providers."""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize provider with configuration.
        
        Args:
            config: Provider-specific configuration dictionary
        """
        self.config = config or {}
    
    @abstractmethod
    async def health_check(self) -> bool:
        """
        Check if the provider is healthy and accessible.
        
        Should check:
        1. Connectivity to the external system
        2. Authentication validity
        3. Basic permissions
        
        Returns:
            True if provider is healthy, False otherwise
        """
        pass
    
    def get_config(self, key: str, default: Any = None) -> Any:
        """Get configuration value."""
        return self.config.get(key, default)

