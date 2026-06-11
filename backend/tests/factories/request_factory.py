from app.db.request import RequestModel
from datetime import datetime, timezone
import uuid
import random
from faker import Faker

fake = Faker()

# Request types are data-driven strings; a representative sample for test fixtures.
_SAMPLE_TYPES = [
    "workspace_access", "workspace_provision", "service_principal",
    "data_access_request", "github_repo_creation", "report_execution",
]

class RequestFactory:
    @staticmethod
    def create(session, **kwargs):
        """Creates and persists a RequestModel."""
        params = kwargs.pop("state_context", {})
        
        # Dynamic defaults
        default_type = random.choice(_SAMPLE_TYPES)
        default_title = fake.sentence(nb_words=3).rstrip(".")
        
        request = RequestModel(
            id=kwargs.pop("id", f"req-{uuid.uuid4()}"),
            type=kwargs.pop("type", default_type),
            title=kwargs.pop("title", default_title),
            status=kwargs.pop("status", "pending"),
            current_state=kwargs.pop("current_state", "pending"),
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            state_context=params,
            **kwargs
        )
        session.add(request)
        session.commit()
        return request
