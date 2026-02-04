
from app.db.session import get_lakebase_session
session_factory = get_lakebase_session
from app.services.request_service import RequestService
from app.state_machines.persistence import load_state_machine
import logging

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def debug_sm():
    db = session_factory()
    try:
        # Get a request
        reqs = RequestService.get_requests(db, skip=0, limit=5)
        for req in reqs:
            print(f"Checking Request: {req.id} - Status: {req.status}")
            try:
                sm = load_state_machine(req, db)
                sm_state = sm.to_state_machine_state()
                print("State Machine State:")
                print(sm_state.model_dump_json(indent=2))
                
                # Check states
                for s in sm_state.states:
                    print(f"  State: {s.id}, Name: {s.name}, Active: {s.isActive}, CompletedAt: {s.completedAt}")
                    
            except Exception as e:
                print(f"Error loading SM: {e}")
            print("-" * 50)
            
    finally:
        db.close()

if __name__ == "__main__":
    debug_sm()
