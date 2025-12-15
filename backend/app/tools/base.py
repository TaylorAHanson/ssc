"""
Base tool interface.
"""
from abc import ABC, abstractmethod
from typing import Dict, Any


class BaseTool(ABC):
    """Base class for all tools."""
    
    @abstractmethod
    async def execute(self, *args, **kwargs) -> Dict[str, Any]:
        """
        Execute the tool operation.
        
        Returns:
            Dictionary with operation results
        """
        pass
    
    def validate_input(self, **kwargs) -> bool:
        """
        Validate input parameters.
        
        Returns:
            True if input is valid, raises ValidationError otherwise
        """
        return True

