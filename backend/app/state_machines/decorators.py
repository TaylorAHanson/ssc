from typing import Type, Callable, Optional, Any, Union, List
from app.models.request import RequestType

# Global registry for state machines
WORKFLOW_REGISTRY = {}

def workflow(request_types: Union[RequestType, List[RequestType]], feature_flag: Optional[str] = None):
    """
    Decorator to register a state machine class for specific request types.
    
    Usage:
        @workflow(
            request_types=[RequestType.DATA_ACCESS_REQUEST, RequestType.BATCH_DATA_ACCESS],
            feature_flag="core"
        )
        class DataAccessStateMachine(BaseRequestStateMachine):
            ...
    """
    def decorator(cls: Type) -> Type:
        # Normalize to list
        types_list = request_types if isinstance(request_types, list) else [request_types]
        
        # Attach the metadata to the class
        cls._request_types = types_list
        cls._feature_flag = feature_flag
        
        # Add to the global registry
        for req_type in types_list:
            WORKFLOW_REGISTRY[req_type] = cls
            
        return cls
        
    return decorator
