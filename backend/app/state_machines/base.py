"""
Base state machine class for all request state machines.
"""
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone
from statemachine import StateMachine, State
from app.models.request import RequestStatus, RequestType, StateMachineState
from app.db import ApprovalModel, RequestModel
from app.state_machines.facts import has_fact, get_latest_fact, add_fact
from sqlalchemy.orm import Session
import logging

logger = logging.getLogger(__name__)


class BaseRequestStateMachine(StateMachine):
    """
    Base class for all request state machines.
    
    Handles state transitions, fact-based reconciliation, and UI state generation.
    """
    
    # --------------------------------------------------------------------------
    # Configuration & Mappings (Override in subclasses)
    # --------------------------------------------------------------------------

    # Map states to their primary completion facts for progress tracking
    STATE_COMPLETION_FACTS = {
        "pending": "request_submitted",
        "manager_approval": "approval_received",
        "data_owner_approval": "approval_received",
        "platform_admin_approval": "approval_received",
        "training_pending": "training_completed",
        "provisioning": "provisioning_completed",
        "rejected": "request_rejected",
        "parameters_updated": "parameters_edited",
    }

    # Map states to all facts that should be shown in their logs
    STATE_LOG_FACTS = {
        "pending": ["request_submitted"],
        "manager_approval": ["approval_received"],
        "data_owner_approval": ["approval_received"],
        "platform_admin_approval": ["approval_received"],
        "training_pending": ["training_completed"],
        "provisioning": ["provisioning_started", "workspace_created", "repo_created", "provisioning_completed", "provisioning_failed"],
        "rejected": ["request_rejected"],
        "parameters_updated": ["parameters_edited"],
    }

    # Map internal states to the top-level RequestStatus enum
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

    # Custom approval configuration: {state_id: {"approval_type": str, "name": str}}
    APPROVAL_NODES = {}

    # Map persisted-but-removed state ids to their current equivalent. Used to
    # recover requests that were persisted in a state that a later flow refactor
    # deleted (e.g. data access dropped "manager_approval"). Override in subclasses.
    LEGACY_STATE_MAP: Dict[str, str] = {}

    def get_editable_states(self) -> list:
        """States from which a platform_admin can trigger Edit & Restart.

        Override this in subclasses that support parameter editing.
        Returning an empty list (the default) disables the feature for that SM.
        """
        return []

    # --------------------------------------------------------------------------
    # Lifecycle
    # --------------------------------------------------------------------------
    
    def __init__(self, request: RequestModel, db: Session, **kwargs):
        self.request = request
        self.db = db
        start_value = self._resolve_start_value(request.current_state)
        super().__init__(start_value=start_value, **kwargs)

    def _resolve_start_value(self, current_state: Optional[str]) -> Optional[str]:
        """Validate the persisted state against this SM's known states.

        A flow refactor can delete a state that older requests were persisted in
        (e.g. data access dropped ``manager_approval``). Loading such a request
        would otherwise raise ``InvalidStateValue`` and make it permanently
        unviewable/unprocessable. We instead remap via ``LEGACY_STATE_MAP`` when
        possible, else fall back to the initial state, and persist the
        correction so fact-based reconciliation can move it forward.
        """
        if not current_state:
            return current_state

        valid_ids = set(getattr(type(self), "states_map", {}).keys())
        if not valid_ids or current_state in valid_ids:
            return current_state

        remapped = self.LEGACY_STATE_MAP.get(current_state)
        if remapped and remapped in valid_ids:
            logger.warning(
                f"[{self.request.id}] Persisted state '{current_state}' is no longer "
                f"defined for {type(self).__name__}; remapping to '{remapped}'."
            )
            corrected = remapped
        else:
            initial_id = next(
                (sid for sid, s in type(self).states_map.items() if getattr(s, "initial", False)),
                None,
            )
            logger.warning(
                f"[{self.request.id}] Persisted state '{current_state}' is invalid for "
                f"{type(self).__name__} and has no legacy mapping; resetting to "
                f"initial state '{initial_id}'."
            )
            corrected = initial_id

        if corrected and corrected != current_state:
            try:
                self.request.current_state = corrected
                self.db.add(self.request)
                self.db.commit()
            except Exception as e:  # noqa: BLE001 - best-effort self-heal
                logger.error(f"[{self.request.id}] Failed to persist state correction: {e}")
                self.db.rollback()
        return corrected

    def to_state_machine_state(self) -> StateMachineState:
        """Generates the UI view of the state machine."""
        # Ensure state is fresh before building the view
        if self.request.status not in ["completed", "rejected", "failed"]:
            if self.tick():
                self.save()
                self.db.commit()
                logger.debug(f"[{self.request.id}] State synchronized during UI fetch")

        states_view = []
        # Track previous completion time to determine next state's start time
        # Initial state starts when request was created
        previous_completed_at = self.request.created_at
        
        for state in self.states:
            # Hide rejection logs/status if not applicable
            if state.id == "rejected" and self.current_state_value != "rejected":
                continue
            
            completed_at = self._get_state_completion_timestamp(state.id)
            
            states_view.append({
                "id": state.id,
                "name": self._get_state_display_name(state.id),
                "isActive": state.id == self.current_state_value,
                "isCompleted": self._is_state_completed(state.id),
                "isInitial": state.initial,
                "isFinal": state.final,
                "completedAt": completed_at,
                "startedAt": previous_completed_at,
                "facts": self._get_state_facts(state.id)
            })
            
            # The next state starts when this one completes
            # If this state is not completed, the next one hasn't started (or logic depends on parallel paths, but for now linear)
            if completed_at:
                previous_completed_at = completed_at
        
        return StateMachineState(
            currentState=self.current_state_value,
            states=states_view,
            currentProgress=self._get_current_progress()
        )

    # --------------------------------------------------------------------------
    # Flow Logic (The "Engine")
    # --------------------------------------------------------------------------

    def tick(self) -> bool:
        """Reconciles internal state with external facts."""
        initial_state = self.current_state_value
        
        # 1. Try transitions first (prioritize moving forward based on facts)
        self._try_transitions()
        
        # 2. Process current state (create tasks/approvals for the current state)
        self._process_current_state()
        
        # 3. Handle state entry hooks
        if self.current_state_value != initial_state:
            logger.info(f"[{self.request.id}] Transition: {initial_state} -> {self.current_state_value}")
            self._call_on_enter_hooks(initial_state, self.current_state_value)
            return True
            
        return False

    def _try_transitions(self):
        """
        Attempts to execute available transitions based on guard conditions.
        
        Dynamically discovers all outgoing transitions from the current state and attempts
        to fire their events. This avoids hardcoding triggers in the base class.
        """
        current_state_obj = list(self.configuration)[0]
        
        if not hasattr(current_state_obj, 'transitions'):
            return

        # Transitions that are meant to be called EXPLICITLY by error-handling or
        # admin-action code paths, never auto-triggered by state reconciliation.
        # mark_failed: called by exception handlers when a step permanently fails.
        # reject: called when an approver submits a rejection action.
        # Any unconditional transition auto-firing here can race against guarded
        # transitions due to set() ordering being non-deterministic across runs.
        _EXCLUDED_FROM_AUTO_TRIGGER = {"mark_failed", "reject"}

        # Collect unique event names to try
        triggers_to_try = set()
        for transition in current_state_obj.transitions:
            if hasattr(transition, 'event'):
                triggers_to_try.add(transition.event)
        
        for trigger in triggers_to_try:
            if trigger in _EXCLUDED_FROM_AUTO_TRIGGER:
                continue

            func = getattr(self, trigger, None)
            if not func: continue
            
            try:
                state_before = self.current_state_value
                func()
                if self.current_state_value != state_before:
                    logger.info(f"[{self.request.id}] Triggered '{trigger}': {state_before} -> {self.current_state_value}")
                    break # Single transition per tick
            except Exception:
                pass # Guard conditions not met


    def _process_current_state(self) -> bool:
        """Handles background logic required by the current state."""
        state_id = self.current_state_value
        changed = False

        # Automatic submission for pending requests
        if state_id == "pending" and not self.has_request_submitted:
            add_fact(self.db, self.request.id, "request_submitted", {}, actor="system")
            if hasattr(self, 'submit'):
                self.submit()
                changed = True
        
        # Handle rejection facts
        if self.has_request_rejected and state_id not in ["rejected", "completed"]:
            if hasattr(self, "reject"):
                self.reject()
                changed = True

        # Handle approval creation
        if state_id.endswith("_approval") and state_id in self.APPROVAL_NODES:
            approval_type = self.APPROVAL_NODES[state_id].get("approval_type")
            if approval_type:
                self.create_approval_task(approval_type)
        
        # Handle provisioning completion if workspace already exists
        if state_id == "provisioning" and self.has_workspace_created:
            if not has_fact(self.db, self.request.id, "provisioning_completed"):
                add_fact(self.db, self.request.id, "provisioning_completed", {}, actor="system")
        
        # Handle training verification
        if state_id == "training_pending" and not self.has_training_completed:
            # Check if user has completed required training
            from app.providers.training.client import TrainingProvider
            
            provider = TrainingProvider(self.db)
            ctx = self.request.state_context or {}
            user_email = ctx.get("requested_by_email") or self.request.requester_email
            
            if not user_email:
                logger.warning(f"[{self.request.id}] Cannot verify training: No email found in context")
                return changed

            # Get required courses from context, default to empty
            required_courses = ctx.get("required_trainings", [])
            
            completed_courses = provider.get_user_training_status(user_email)
            
            # Check if all required courses are in completed list
            missing = [c for c in required_courses if c not in completed_courses]
            
            if not missing:
                logger.info(f"[{self.request.id}] Training verification passed for {user_email}")
                add_fact(self.db, self.request.id, "training_completed", {
                    "completed_courses": completed_courses,
                    "verified_at": datetime.now(timezone.utc).isoformat()
                }, actor="system")
                
                # Check for auto-transition
                if hasattr(self, "complete_training"):
                     self.complete_training()
                     changed = True
            else:
                # Log only once or periodically? For now, we just don't transition.
                # maybe log info occasionally
                pass

        return changed

    # --------------------------------------------------------------------------
    # Helpers: Database & Persistence
    # --------------------------------------------------------------------------

    def save(self):
        """Persists state and status to DB."""
        self.request.current_state = self.current_state_value
        
        # Update status based on state machine's mapped status
        # Only preserve "failed" status if we're in an actual terminal failure state
        new_status = self.get_mapped_status().value
        terminal_failure_states = {"failed", "rejected"}
        
        if self.request.status == "failed" and self.current_state_value not in terminal_failure_states:
            # State machine recovered from failure - update status
            self.request.status = new_status
        elif self.request.status != "failed":
            # Normal case - update status
            self.request.status = new_status
        # else: keep "failed" status for terminal failure states
        
        self.request.updated_at = datetime.now(timezone.utc)

    def get_mapped_status(self) -> RequestStatus:
        return self.STATUS_MAPPING.get(self.current_state_value, RequestStatus.PENDING)

    def create_approval_task(self, approval_type: str):
        """Standardized approval task creation with duplicate prevention."""
        exists = self.db.query(ApprovalModel).filter(
            ApprovalModel.request_id == self.request.id,
            ApprovalModel.approval_type == approval_type,
            ApprovalModel.status.in_(["pending", "approved"])
        ).first()
        
        if not exists:
            ctx = self.request.state_context or {}
            
            # Find the state ID that corresponds to this approval type to get its config
            state_config = {}
            for config in self.APPROVAL_NODES.values():
                if config.get("approval_type") == approval_type:
                    state_config = config
                    break
                    
            assignee_key = state_config.get("assignee_context_key")
            assigned_to_email = ctx.get(assignee_key) if assignee_key else None
            
            assignee_role_key = state_config.get("assignee_role_key")
            assigned_to_role = ctx.get(assignee_role_key) if assignee_role_key else None
            
            new_approval = ApprovalModel(
                id=f"app-{datetime.now(timezone.utc).timestamp()}",
                request_id=self.request.id,
                approval_type=approval_type,
                requested_by=ctx.get("requested_by", "system"),
                requested_by_email=ctx.get("requested_by_email", ""),
                assigned_to_email=assigned_to_email,
                assigned_to_role=assigned_to_role,
                status="pending",
                created_at=datetime.now(timezone.utc)
            )
            self.db.add(new_approval)
            self.db.commit()
            logger.info(f"[{self.request.id}] Created '{approval_type}' approval task")

    # --------------------------------------------------------------------------
    # Helpers: UI State Building
    # --------------------------------------------------------------------------

    def _is_state_completed(self, state_id: str) -> bool:
        """Returns True if the state is finished based on facts or position."""
        if self.current_state_value == "completed": return state_id != "completed"
        if self.current_state_value == "rejected": return state_id not in ["rejected", "completed"]
        
        fact_type = self.STATE_COMPLETION_FACTS.get(state_id)
        if fact_type:
            if state_id in self.APPROVAL_NODES:
                approval_type = self.APPROVAL_NODES[state_id].get("approval_type")
                if has_fact(self.db, self.request.id, fact_type, approval_type=approval_type):
                    return True
            elif has_fact(self.db, self.request.id, fact_type):
                return True
        
        # Position-based fallback
        all_states = [s.id for s in self.states]
        try:
            return all_states.index(self.current_state_value) > all_states.index(state_id)
        except ValueError:
            return False

    def _get_state_completion_timestamp(self, state_id: str) -> Optional[datetime]:
        fact_type = self.STATE_COMPLETION_FACTS.get(state_id)
        if state_id == "completed": fact_type = "provisioning_completed"
        if not fact_type: return None
            
        if state_id in self.APPROVAL_NODES:
            approval_type = self.APPROVAL_NODES[state_id].get("approval_type")
            fact = get_latest_fact(self.db, self.request.id, fact_type, approval_type=approval_type)
        else:
            fact = get_latest_fact(self.db, self.request.id, fact_type)
            
        return fact.created_at if fact else None

    def _get_state_facts(self, state_id: str) -> List[Dict[str, Any]]:
        from app.db import EventModel
        
        # Only show facts for completed or active states
        if not self._is_state_completed(state_id) and self.current_state_value != state_id:
            if not (state_id == "provisioning" and self.current_state_value == "completed"):
                return []
        
        event_types = self.STATE_LOG_FACTS.get(state_id, [])
        if not event_types: return []
            
        query = self.db.query(EventModel).filter(
            EventModel.request_id == self.request.id,
            EventModel.event_type.in_(event_types)
        ).order_by(EventModel.created_at.asc())
        
        results = []
        for event in query.all():
            if event.event_type == "approval_received" and state_id in self.APPROVAL_NODES:
                if event.event_data.get("approval_type") != self.APPROVAL_NODES[state_id].get("approval_type"):
                    continue
            results.append({
                "type": event.event_type,
                "data": event.event_data,
                "timestamp": event.created_at.isoformat()
            })
        return results

    def _get_current_progress(self) -> Optional[Dict[str, Any]]:
        fact = get_latest_fact(self.db, self.request.id, "provisioning_progress")
        if fact and fact.event_data:
            data = fact.event_data
            return {
                "message": data.get("message", ""),
                "percent": data.get("percent", 0),
                "timestamp": fact.created_at
            }
        return None

    def _get_state_display_name(self, state_id: str) -> str:
        if state_id == "pending": return "Created"
        if state_id in self.APPROVAL_NODES:
            return self.APPROVAL_NODES[state_id].get("name", self._format_state_id(state_id))
        if state_id == "provisioning":
            class_name = self.__class__.__name__
            if "ServicePrincipal" in class_name: return "Service Principal Creation"
            if "Workspace" in class_name: return "Workspace Provisioning"
            if "GitHub" in class_name or "Repo" in class_name: return "Repository Creation"
        return self._format_state_id(state_id)

    def _format_state_id(self, state_id: str) -> str:
        overrides = {"permissions_setup": "Permissions Setup", "provisioning": "Provisioning"}
        return overrides.get(state_id, state_id.replace("_", " ").title())

    # --------------------------------------------------------------------------
    # Fact Properties (Used in transitions)
    # --------------------------------------------------------------------------
    
    @property
    def has_request_submitted(self) -> bool:
        return has_fact(self.db, self.request.id, "request_submitted")
    
    @property
    def has_request_rejected(self) -> bool:
        return has_fact(self.db, self.request.id, "request_rejected")
    
    @property
    def has_manager_approval(self) -> bool:
        return has_fact(self.db, self.request.id, "approval_received", approval_type="manager")
    
    @property
    def has_training_completed(self) -> bool:
        return has_fact(self.db, self.request.id, "training_completed")
    
    @property
    def has_workspace_created(self) -> bool:
        return has_fact(self.db, self.request.id, "workspace_created")

    @property
    def requires_training(self) -> bool:
        return getattr(self.request, 'requires_training', False)

    @property
    def has_data_owner_approval(self) -> bool:
        return has_fact(self.db, self.request.id, "approval_received", approval_type="data_owner")
    
    @property
    def has_platform_admin_approval(self) -> bool:
        return has_fact(self.db, self.request.id, "approval_received", approval_type="platform_admin")

    # --------------------------------------------------------------------------
    # Utility Methods
    # --------------------------------------------------------------------------

    async def execute_tasks(self):
        """Runs the async handler for the current state."""
        handler_name = f"on_enter_{self.current_state_value}_async"
        logger.debug(f"[{self.request.id}] execute_tasks() - Looking for handler: {handler_name}")
        handler = getattr(self, handler_name, None)
        logger.debug(f"[{self.request.id}] execute_tasks() - Handler found: {handler}, callable: {callable(handler) if handler else False}")
        if handler and callable(handler):
            logger.debug(f"[{self.request.id}] execute_tasks() - Calling async handler: {handler_name}")
            await handler()
        else:
            logger.debug(f"[{self.request.id}] execute_tasks() - No async handler for state: {self.current_state_value}")

    def _call_on_enter_hooks(self, previous_state: str, new_state: str):
        """Calls synchronous on_enter hooks, including auto-generated approval ones."""
        hook = getattr(self, f"on_enter_{new_state}", None)
        if hook and callable(hook):
            hook()

    def __getattr__(self, name: str):
        """Auto-generates on_enter hooks for approval states."""
        if name.startswith("on_enter_") and name.endswith("_approval"):
            state_id = name.replace("on_enter_", "")
            if state_id in self.APPROVAL_NODES:
                approval_type = self.APPROVAL_NODES[state_id].get("approval_type")
                if approval_type:
                    def create_approval(): self.create_approval_task(approval_type)
                    setattr(self, name, create_approval)
                    return create_approval
        raise AttributeError(f"'{self.__class__.__name__}' has no attribute '{name}'")

    async def _send_notification(self, subject: str, body: str, to_email: Optional[str] = None):
        """Sends an email notification via the NotificationProvider."""
        try:
            from app.providers.notifications.client import NotificationProvider
            recipient = to_email or (self.request.state_context or {}).get("requested_by_email")
            if recipient:
                metadata = {
                    "id": self.request.id,
                    "status": self._get_state_display_name(self.request.current_state),
                    "requested_by": (self.request.state_context or {}).get("requested_by", "Unknown")
                }
                await NotificationProvider().send_email(
                    to=recipient, 
                    subject=subject, 
                    body=body,
                    metadata=metadata
                )
        except Exception as e:
            logger.error(f"[{self.request.id}] Notification failed: {e}")

    def spawn_child_request(self, request_type: str, payload: Dict[str, Any], title: str = None) -> RequestModel:
        """Spawn a child request to run an atomic workflow."""
        import uuid
        child_id = f"req-{str(uuid.uuid4())}"
        context = (self.request.state_context or {}).copy()
        context.update(payload)
        
        child = RequestModel(
            id=child_id,
            type=request_type,
            title=title or f"Sub-task: {request_type}",
            status="pending",
            current_state="pending",
            state_context=context,
            parent_id=self.request.id,
            root_id=self.request.root_id or self.request.id,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)
        )
        self.db.add(child)
        self.db.commit()
        logger.info(f"Spawned child request {child_id} ({request_type}) for parent {self.request.id}")
        return child

    def get_children(self) -> List[RequestModel]:
        """Get all direct children of this request."""
        return self.db.query(RequestModel).filter(
            RequestModel.parent_id == self.request.id
        ).all()

    def all_children_completed(self) -> bool:
        """Check if all child requests are completed. Returns False if no children."""
        children = self.get_children()
        if not children: return False # Must have children to be completed in compound workflows
        return all(c.status in ["completed", "rejected", "failed"] for c in children)

