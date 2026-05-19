from app.db.request import RequestModel
from app.models.request import RequestType
from app.state_machines.factory import get_state_machine
from app.state_machines.facts import get_latest_fact
from datetime import datetime, timezone
import uuid

class StateMachineTestHarness:
    def __init__(self, db_session):
        self.db = db_session

    def create_request(self, params=None, **kwargs):
        """Creates a request and adds it to the DB session."""
        req_id = f"req-{uuid.uuid4()}"
        request = RequestModel(
            id=req_id,
            type=kwargs.get("type", "workspace_provision"),
            status="pending",
            current_state="pending",
            title=kwargs.get("title", "Test Request"),
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            state_context=params or {},
            **kwargs
        )
        self.db.add(request)
        self.db.commit()
        return request

    def tick(self, request_id):
        """Ticks the state machine for the given request."""
        request = self.db.get(RequestModel, request_id)
        sm = get_state_machine(request, self.db)
        changed = sm.tick()
        if changed:
            sm.save()
            self.db.commit()
        return changed

    def assert_state(self, request_id, expected_state):
        request = self.db.get(RequestModel, request_id)
        assert request.current_state == expected_state, f"Expected state {expected_state}, but got {request.current_state}"

    def assert_fact(self, request_id, fact_type):
        fact = get_latest_fact(self.db, request_id, fact_type)
        assert fact is not None, f"Fact {fact_type} not found for request {request_id}"
