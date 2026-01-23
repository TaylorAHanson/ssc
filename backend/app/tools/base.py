"""
Base tool interface.
"""
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from app.state_machines.facts import add_fact
from sqlalchemy.orm import Session
from datetime import datetime


class BaseTool(ABC):
    """Base class for all tools."""
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Tool name for MCP."""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """Tool description for MCP."""
        pass

    @property
    @abstractmethod
    def input_schema(self) -> Dict[str, Any]:
        """JSON schema for tool input."""
        pass

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

    def report_progress(self, db: Session, request_id: str, message: str, percent: int):
        """
        Report progress of a long-running operation.
        
        Records a 'provisioning_progress' fact.
        """
        add_fact(db, request_id, "provisioning_progress", {
            "message": message,
            "percent": percent,
            "tool": self.__class__.__name__,
            "timestamp": datetime.utcnow().isoformat()
        }, actor="system")
        db.commit()

