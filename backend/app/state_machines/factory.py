"""
Factory for creating state machine instances based on request type.
"""
import pkgutil
import importlib
import inspect
from pathlib import Path
from sqlalchemy.orm import Session
import logging

from app.models.request import RequestType
from app.db.request import RequestModel
from app.state_machines.base import BaseRequestStateMachine
from app.state_machines.decorators import WORKFLOW_REGISTRY
from app.core.feature_flags import is_feature_enabled, is_workflow_enabled

logger = logging.getLogger(__name__)

# Flag to ensure we only load once
_WORKFLOWS_LOADED = False

def load_workflows():
    """Dynamically load all state machines from the state_machines directory."""
    global _WORKFLOWS_LOADED
    if _WORKFLOWS_LOADED:
        return
        
    package_dir = Path(__file__).resolve().parent
    
    # Walk through all modules in the state_machines directory
    for _, module_name, is_pkg in pkgutil.walk_packages([str(package_dir)], prefix="app.state_machines."):
        # Skip base modules
        if module_name in ("app.state_machines.base", "app.state_machines.factory", "app.state_machines.decorators", "app.state_machines.lock", "app.state_machines.facts"):
            continue
            
        try:
            # Importing the module will execute the @workflow decorators
            # and register them in WORKFLOW_REGISTRY
            importlib.import_module(module_name)
        except ImportError as e:
            logger.debug(f"Skipping module {module_name} due to import error: {e}")
        except Exception as e:
            logger.warning(f"Failed to load workflows from module {module_name}: {e}")
            
    _WORKFLOWS_LOADED = True

def get_state_machine(request: RequestModel, db: Session) -> BaseRequestStateMachine:
    """Factory to return the appropriate state machine instance."""
    # Ensure workflows are loaded
    load_workflows()
    
    try:
        # Ensure we have a valid enum
        r_type = RequestType(request.type)
    except ValueError:
        logger.error(f"Invalid request type '{request.type}' for request {request.id}")
        raise ValueError(f"Invalid request type: {request.type}")
    
    # Check if workflow is explicitly enabled
    if not is_workflow_enabled(r_type.value):
        raise ValueError(f"Workflow '{r_type}' is explicitly disabled in configuration.")
        
    # Check if the workflow is registered
    if r_type in WORKFLOW_REGISTRY:
        sm_class = WORKFLOW_REGISTRY[r_type]
        
        # Check feature flag
        feature_flag = getattr(sm_class, "_feature_flag", None)
        if feature_flag and not is_feature_enabled(feature_flag):
            raise ValueError(f"Workflow '{r_type}' is disabled by feature flag '{feature_flag}'")
            
        return sm_class(request, db)

    # Fallback / Default for others
    raise ValueError(f"No state machine implemented for request type: {r_type}")
