from app.db.request import RequestModel
from app.models.request import RequestType
from datetime import datetime
import uuid
import random
from faker import Faker

fake = Faker()

class RequestFactory:
    @staticmethod
    def create(session, **kwargs):
        """Creates and persists a RequestModel."""
        params = kwargs.pop("state_context", {})
        
        # Dynamic defaults
        default_type = random.choice(list(RequestType))
        default_title = fake.sentence(nb_words=3).rstrip(".")
        
        request = RequestModel(
            id=kwargs.pop("id", f"req-{uuid.uuid4()}"),
            type=kwargs.pop("type", default_type),
            title=kwargs.pop("title", default_title),
            status=kwargs.pop("status", "pending"),
            current_state=kwargs.pop("current_state", "pending"),
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            state_context=params,
            **kwargs
        )
        session.add(request)
        session.commit()
        return request
