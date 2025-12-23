"""
Base state machine class for all request state machines.
"""
from typing import Optional, Dict, Any, List
from datetime import datetime
from statemachine import StateMachine, State
from app.models.request import RequestStatus, RequestType, StateMachineState
from app.db.request import ApprovalModel, RequestModel
from app.state_machines.facts import has_fact, get_latest_fact, add_fact
from sqlalchemy.orm import Session
import logging

logger = logging.getLogger(__name__)


class BaseRequestStateMachine(StateMachine):
    """
    Base class for all request state machines.
    """
    
    def __init__(self, request: RequestModel, db: Session, **kwargs):
        self.request = request
        self.db = db
        # We no longer store visual state in the DB.
        # We calculate it on the fly.
        # Handle legacy "failed" state - default to a valid state
        start_state = request.current_state
        if start_state == "failed":
            # For failed requests, default to "provisioning" as the most common failure point
            # This allows the state machine to load and display the failure state
            # The actual status="failed" will be shown in the UI
            start_state = "provisioning"
        super().__init__(start_value=start_state, **kwargs)

    def to_state_machine_state(self) -> StateMachineState:
        """
        Return the state machine representation using python-statemachine's built-in states.
        Simple linear flow - no parallel paths.
        """
        # Run tick() locally to ensure we have the most up-to-date state based on facts
        # for the UI view, even if the DB hasn't been updated yet by the poller.
        # SKIP tick() for terminal states to avoid redundant processing and logs.
        if self.request.status not in ["completed", "rejected", "failed"]:
            self.tick()

        # Get all states from python-statemachine in order
        all_states = list(self.states)
        
        # Build state list with metadata
        states_list = []
        for state in all_states:
            state_id = state.id
            
            # Skip rejected state if we're not rejected
            if state_id == "rejected" and self.current_state.id != "rejected":
                continue
                
            is_active = state_id == self.current_state.id
            is_completed = self._is_state_completed(state_id)
            
            # Get display name
            display_name = self._get_state_display_name(state_id)
            
            # Get completion timestamp and facts
            completed_at = self._get_state_completion_timestamp(state_id)
            facts = self._get_state_facts(state_id)
            
            states_list.append({
                "id": state_id,
                "name": display_name,
                "isActive": is_active,
                "isCompleted": is_completed,
                "isInitial": state.initial,
                "isFinal": state.final,
                "completedAt": completed_at,
                "facts": facts
            })
        
        # Get latest progress if available
        current_progress = None
        progress_fact = get_latest_fact(self.db, self.request.id, "provisioning_progress")
        if progress_fact and progress_fact.event_data:
            data = progress_fact.event_data
            current_progress = {
                "message": data.get("message", ""),
                "percent": data.get("percent", 0),
                "timestamp": progress_fact.created_at
            }
        
        return StateMachineState(
            currentState=self.current_state.id,
            states=states_list,
            currentProgress=current_progress
        )
    
    def _get_state_completion_timestamp(self, state_id: str) -> Optional[datetime]:
        """Get the timestamp when a state was completed."""
        # Map states to their completion facts
        mapping = {
            "pending": "request_submitted",
            "manager_approval": "approval_received",
            "data_owner_approval": "approval_received",
            "platform_admin_approval": "approval_received",
            "training_pending": "training_completed",
            "provisioning": "provisioning_completed",
            "completed": None,
            "rejected": "request_rejected"
        }
        
        fact_type = mapping.get(state_id)
        if not fact_type:
            # For the terminal completed state, use the provisioning_completed fact
            if state_id == "completed":
                fact = get_latest_fact(self.db, self.request.id, "provisioning_completed")
                return fact.created_at if fact else None
            return None
            
        # For approvals, we need to match the type
        if state_id in self.APPROVAL_NODES:
            approval_type = self.APPROVAL_NODES[state_id].get("approval_type")
            fact = get_latest_fact(self.db, self.request.id, fact_type, approval_type=approval_type)
        else:
            fact = get_latest_fact(self.db, self.request.id, fact_type)
            
        return fact.created_at if fact else None

    def _get_state_facts(self, state_id: str) -> List[Dict[str, Any]]:
        """Get relevant facts for a state to show as logs."""
        from app.db.request import EventModel
        
        # Don't show facts for states we haven't reached yet
        if not self._is_state_completed(state_id) and self.current_state.id != state_id:
            # Special exception: if the request is completed, show provisioning facts
            if not (state_id == "provisioning" and self.current_state.id == "completed"):
                return []
        
        # Define which facts belong to which state
        mapping = {
            "pending": ["request_submitted"],
            "manager_approval": ["approval_received"],
            "data_owner_approval": ["approval_received"],
            "platform_admin_approval": ["approval_received"],
            "training_pending": ["training_completed"],
            "provisioning": ["provisioning_started", "workspace_created", "repo_created", "provisioning_completed", "provisioning_failed"],
            "rejected": ["request_rejected"]
        }
        
        event_types = mapping.get(state_id, [])
        if not event_types:
            return []
            
        # Query for these facts
        query = self.db.query(EventModel).filter(
            EventModel.request_id == self.request.id,
            EventModel.event_type.in_(event_types)
        )
        
        facts = []
        for event in query.order_by(EventModel.created_at.asc()).all():
            # For approvals, filter by type
            if event.event_type == "approval_received" and state_id in self.APPROVAL_NODES:
                approval_type = self.APPROVAL_NODES[state_id].get("approval_type")
                if event.event_data.get("approval_type") != approval_type:
                    continue
            
            facts.append({
                "type": event.event_type,
                "data": event.event_data,
                "timestamp": event.created_at.isoformat()
            })
            
        return facts
    
    def _is_state_completed(self, state_id: str) -> bool:
        """Check if a state has been completed based on facts."""
        # If we're past this state, it's completed
        if self.current_state.id == "completed":
            return state_id != "completed"  # completed state itself is active
        if self.current_state.id == "rejected":
            return state_id not in ["rejected", "completed"]
        
        # Check for specific completion facts using the same mapping as timestamps
        mapping = {
            "pending": "request_submitted",
            "manager_approval": "approval_received",
            "data_owner_approval": "approval_received",
            "platform_admin_approval": "approval_received",
            "training_pending": "training_completed",
            "provisioning": "provisioning_completed",
            "rejected": "request_rejected"
        }
        
        fact_type = mapping.get(state_id)
        if fact_type:
            if state_id in self.APPROVAL_NODES:
                approval_type = self.APPROVAL_NODES[state_id].get("approval_type")
                if has_fact(self.db, self.request.id, fact_type, approval_type=approval_type):
                    return True
            elif has_fact(self.db, self.request.id, fact_type):
                return True
        
        # Fallback to simple heuristic: if current state comes after this one in the state list, it's completed
        all_states = list(self.states)
        current_index = next((i for i, s in enumerate(all_states) if s.id == self.current_state.id), -1)
        state_index = next((i for i, s in enumerate(all_states) if s.id == state_id), -1)
        
        if current_index > state_index:
            return True
        
        return False
    
    def _get_state_display_name(self, state_id: str) -> str:
        """Get display name for a state."""
        # Check if it's in APPROVAL_NODES (has custom name)
        if state_id in self.APPROVAL_NODES:
            return self.APPROVAL_NODES[state_id].get("name", self._state_id_to_name(state_id))
        
        # Use custom provisioning name if applicable
        if state_id == "provisioning":
            return self._get_provisioning_state_name(state_id)
        
        # Default: convert state ID to display name
        return self._state_id_to_name(state_id)

    
    def _state_id_to_name(self, state_id: str) -> str:
        """Convert state ID (snake_case) to display name (Title Case)."""
        # Handle special cases
        name_mapping = {
            "permissions_setup": "Permissions Setup",
            "service_principal_creation": "Service Principal Creation",
            "provisioning": "Provisioning",  # Generic, can be overridden in subclasses
        }
        
        if state_id in name_mapping:
            return name_mapping[state_id]
        
        # Convert snake_case to Title Case
        return state_id.replace("_", " ").title()
    
    def _get_provisioning_state_name(self, state_id: str) -> str:
        """
        Get display name for provisioning states.
        Override in subclasses to provide custom names (e.g., "Service Principal Creation").
        """
        if state_id == "provisioning":
            # Try to infer a better name from the class name
            class_name = self.__class__.__name__
            if "ServicePrincipal" in class_name:
                return "Service Principal Creation"
            elif "Workspace" in class_name:
                return "Workspace Provisioning"
            elif "GitHub" in class_name or "Repo" in class_name:
                return "Repository Creation"
            else:
                return "Provisioning"
        return self._state_id_to_name(state_id)

    # Approval node configuration - override in subclasses
    # Format: {state_id: {"approval_type": "manager|data_owner|platform_admin", "name": "Display Name"}}
    APPROVAL_NODES = {}

    def save(self):
        """Persist ONLY the core state machine status back to the request model."""
        self.request.current_state = self.current_state.id
        # Only overwrite status if it's not already in a terminal "failed" state
        # This prevents PermanentError status from being overwritten
        if self.request.status != "failed":
            self.request.status = self.get_mapped_status().value
        # We do NOT save parallel_paths, completed_states, or active_states anymore.
        # They are derived.
        self.request.updated_at = datetime.utcnow()

    # Status mapping - override STATUS_MAPPING in subclasses if needed
    STATUS_MAPPING = {
        "pending": RequestStatus.PENDING,
        "manager_approval": RequestStatus.MANAGER_APPROVAL,
        "data_owner_approval": RequestStatus.MANAGER_APPROVAL,
        "platform_admin_approval": RequestStatus.MANAGER_APPROVAL,
        "training_pending": RequestStatus.TRAINING_PENDING,
        "provisioning": RequestStatus.PROVISIONING,
        "completed": RequestStatus.COMPLETED,
        "rejected": RequestStatus.REJECTED
    }
    
    def get_mapped_status(self) -> RequestStatus:
        """Map internal states to RequestStatus enum using STATUS_MAPPING."""
        return self.STATUS_MAPPING.get(self.current_state.id, RequestStatus.PENDING)

    # Fact-checking properties (override in subclasses as needed)
    # These are used in conditional transitions with cond="property_name"
    
    @property
    def has_request_submitted(self) -> bool:
        """Check if request has been submitted."""
        return has_fact(self.db, self.request.id, "request_submitted")
    
    @property
    def has_request_rejected(self) -> bool:
        """Check if request has been rejected."""
        return has_fact(self.db, self.request.id, "request_rejected")
    
    @property
    def has_manager_approval(self) -> bool:
        """Check if manager approval has been received."""
        result = has_fact(self.db, self.request.id, "approval_received", approval_type="manager")
        logger.debug(f"[{self.request.id}] has_manager_approval check: {result}")
        return result
    
    @property
    def has_training_completed(self) -> bool:
        """Check if training has been completed."""
        result = has_fact(self.db, self.request.id, "training_completed")
        logger.debug(f"[{self.request.id}] has_training_completed check: {result}")
        return result
    
    @property
    def has_workspace_created(self) -> bool:
        """Check if workspace has been created."""
        return has_fact(self.db, self.request.id, "workspace_created")
    
    @property
    def requires_training(self) -> bool:
        """Check if request requires training."""
        result = self.request.requires_training if hasattr(self.request, 'requires_training') else False
        logger.debug(f"[{self.request.id}] requires_training check: {result}")
        return result
    
    # Additional approval type properties (override in subclasses as needed)
    @property
    def has_data_owner_approval(self) -> bool:
        """Check if data owner approval has been received."""
        return has_fact(self.db, self.request.id, "approval_received", approval_type="data_owner")
    
    @property
    def has_platform_admin_approval(self) -> bool:
        """Check if platform admin approval has been received."""
        return has_fact(self.db, self.request.id, "approval_received", approval_type="platform_admin")

    @property
    def _always_false(self) -> bool:
        """Helper for dummy transitions."""
        return False

    def tick(self) -> bool:
        """
        Process one tick of the state machine.
        
        This is the main processing method called by the poller.
        The state machine uses conditional transitions (guards) to automatically
        transition based on facts. We trigger events and let the state machine's
        built-in conditional logic evaluate guards and transition if conditions are met.
        
        Returns:
            True if state was changed, False otherwise
        """
        initial_state = self.current_state.id
        logger.info(f"[{self.request.id}] State machine tick() - Starting from state: {initial_state}")
        
        # Step 1: Process current state (record facts if needed)
        logger.debug(f"[{self.request.id}] Processing current state: {initial_state}")
        self._process_current_state()
        
        # Step 2: Try to trigger transitions based on facts
        # The conditional transitions will evaluate their guards and execute
        # if conditions are met. If conditions aren't met, the transition
        # simply won't occur (no exception raised).
        logger.debug(f"[{self.request.id}] Attempting transitions from state: {initial_state}")
        self._try_transitions()
        
        # Step 3: If state changed, call on_enter hooks
        if self.current_state.id != initial_state:
            logger.info(f"[{self.request.id}] State transition occurred: {initial_state} -> {self.current_state.id}")
            self._call_on_enter_hooks(initial_state, self.current_state.id)
        else:
            logger.debug(f"[{self.request.id}] No state transition (still in {initial_state})")
        
        # Check if state changed
        return self.current_state.id != initial_state

    async def execute_tasks(self):
        """
        Execute any asynchronous tasks associated with the current state.
        Override in subclasses to implement state-specific actions.
        """
        pass
    
    def _try_transitions(self):
        """
        Try to trigger transitions based on current facts.
        
        This method attempts to trigger transition events. The state machine's
        conditional transitions will evaluate their guards (cond/unless) and
        only execute if conditions are met. If no conditions match, the event
        is ignored (no exception).
        """
        # Try each possible transition - the conditional guards will determine
        # if the transition actually occurs
        transitions_to_try = []
        
        # Build list of transitions to try based on current state
        if hasattr(self, 'submit'):
            transitions_to_try.append(('submit', lambda: self.submit()))
        if hasattr(self, 'reject'):
            transitions_to_try.append(('reject', lambda: self.reject()))
        if hasattr(self, 'approve_manager'):
            transitions_to_try.append(('approve_manager', lambda: self.approve_manager()))
        if hasattr(self, 'approve_owner'):
            transitions_to_try.append(('approve_owner', lambda: self.approve_owner()))
        if hasattr(self, 'approve_admin'):
            transitions_to_try.append(('approve_admin', lambda: self.approve_admin()))
        if hasattr(self, 'auto_approve'):
            transitions_to_try.append(('auto_approve', lambda: self.auto_approve()))
        if hasattr(self, 'complete_training'):
            transitions_to_try.append(('complete_training', lambda: self.complete_training()))
        if hasattr(self, 'finish_provisioning'):
            transitions_to_try.append(('finish_provisioning', lambda: self.finish_provisioning()))
        
        logger.info(f"[{self.request.id}] Trying {len(transitions_to_try)} transition(s) from state '{self.current_state.id}': {[t[0] for t in transitions_to_try]}")
        
        # Try each transition - conditional guards will prevent invalid transitions
        for name, transition_func in transitions_to_try:
            state_before = self.current_state.id
            try:
                logger.debug(f"[{self.request.id}] Attempting transition '{name}' from state '{state_before}'...")
                transition_func()
                state_after = self.current_state.id
                # If we get here, the transition occurred (condition was met)
                if state_before != state_after:
                    logger.info(f"[{self.request.id}] ✓ Transition '{name}' SUCCEEDED: {state_before} -> {state_after}")
                else:
                    logger.info(f"[{self.request.id}] ✗ Transition '{name}' attempted but no state change (guard condition not met or transition not available from this state)")
            except Exception as e:
                # Transition condition not met or not available from current state
                # This is expected - just continue to next transition
                logger.info(f"[{self.request.id}] ✗ Transition '{name}' failed: {type(e).__name__} - {str(e)[:100]}")
                pass
    
    def _call_on_enter_hooks(self, previous_state: str, new_state: str):
        """
        Call on_enter hooks when entering a new state.
        
        This explicitly calls on_enter_* hooks that may have been auto-generated
        via __getattr__ for approval states.
        """
        # Check if there's an on_enter hook for the new state
        hook_name = f"on_enter_{new_state}"
        if hasattr(self, hook_name):
            try:
                hook = getattr(self, hook_name)
                if callable(hook):
                    hook()
                    logger.debug(f"Called {hook_name} hook for request {self.request.id}")
            except AttributeError:
                # Hook doesn't exist or wasn't auto-generated - that's okay
                pass
        elif new_state.endswith("_approval"):
            # Try to auto-generate and call the hook
            try:
                hook = getattr(self, hook_name)
                if callable(hook):
                    hook()
                    logger.debug(f"Auto-generated and called {hook_name} hook for request {self.request.id}")
            except AttributeError:
                # Can't generate hook - that's okay
                pass


    def _process_current_state(self) -> bool:
        """
        Process the current state - determine what needs to happen.
        
        Override in subclasses to implement state-specific processing.
        Default implementation handles common cases.
        
        Returns:
            True if state was changed, False otherwise
        """
        changed = False
        
        # If pending, submit the request (record fact)
        if self.current_state.id == "pending":
            if not has_fact(self.db, self.request.id, "request_submitted"):
                logger.info(f"Submitting request {self.request.id}")
                add_fact(self.db, self.request.id, "request_submitted", {}, actor="system")
                if hasattr(self, 'submit'):
                    self.submit()
                    changed = True
        
        # Handle rejection
        if has_fact(self.db, self.request.id, "request_rejected"):
            if hasattr(self, "reject") and self.current_state.id not in ["rejected", "completed"]:
                self.reject()
                changed = True
        
        # Handle approval states - ensure approval record exists
        if self.current_state.id.endswith("_approval"):
            # Check if this state is in APPROVAL_NODES
            if hasattr(self, 'APPROVAL_NODES') and self.current_state.id in self.APPROVAL_NODES:
                approval_type = self.APPROVAL_NODES[self.current_state.id].get("approval_type")
                if approval_type:
                    # Create approval if it doesn't exist
                    self.create_approval_task(approval_type)
        
        # Handle provisioning state - check if workspace already exists
        if self.current_state.id == "provisioning":
            if has_fact(self.db, self.request.id, "workspace_created"):
                # Workspace already exists, mark provisioning as completed
                if not has_fact(self.db, self.request.id, "provisioning_completed"):
                    logger.info(f"Workspace already exists for request {self.request.id}, marking complete")
                    add_fact(self.db, self.request.id, "provisioning_completed", {}, actor="system")
                    # Will reconcile to completed on next tick
        
        return changed

    def create_approval_task(self, approval_type: str):
        """Helper to create an approval record."""
        exists = self.db.query(ApprovalModel).filter(
            ApprovalModel.request_id == self.request.id,
            ApprovalModel.status == "pending",
            ApprovalModel.approval_type == approval_type
        ).first()
        
        if not exists:
            # Get requested_by from state_context if available
            state_context = self.request.state_context or {}
            requested_by = state_context.get("requested_by", "system")
            requested_by_email = state_context.get("requested_by_email", "")
            
            approval_id = f"app-{datetime.utcnow().timestamp()}"
            new_approval = ApprovalModel(
                id=approval_id,
                request_id=self.request.id,
                approval_type=approval_type,
                requested_by=requested_by,
                requested_by_email=requested_by_email,
                status="pending",
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            self.db.add(new_approval)
            self.db.commit()  # Commit immediately so approval is available
            logger.info(f"Created pending approval {approval_id} ({approval_type}) for request {self.request.id} (requested_by: {requested_by})")
    
    def __getattr__(self, name: str):
        """
        Auto-generate on_enter_* hooks for approval states.
        
        If a method like on_enter_manager_approval is called but doesn't exist,
        check if it matches the pattern on_enter_<approval_state> and if that
        state is in APPROVAL_NODES, automatically create the approval task.
        """
        if name.startswith("on_enter_") and name.endswith("_approval"):
            state_id = name.replace("on_enter_", "")
            if state_id in self.APPROVAL_NODES:
                approval_type = self.APPROVAL_NODES[state_id].get("approval_type")
                if approval_type:
                    # Create a bound method that creates the approval task
                    def create_approval():
                        self.create_approval_task(approval_type)
                    # Cache it so we don't recreate it every time
                    setattr(self, name, create_approval)
                    return create_approval
        raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{name}'")

