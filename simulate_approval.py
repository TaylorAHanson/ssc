import sys
import os
import asyncio

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), 'backend')))
from app.db.session import get_session_local
from app.db.request import RequestModel
from app.state_machines.facts import add_fact

def simulate_approvals(request_id: str):
    Session = get_session_local()
    db = Session()
    try:
        req = db.query(RequestModel).filter(RequestModel.id == request_id).first()
        if not req:
            print(f"Request {request_id} not found.")
            return

        print(f"Processing request {request_id} (Current State: {req.current_state})")

        if req.current_state == "admin_review":
            print("Simulating Admin Approval...")
            add_fact(db, request_id, "admin_approved", {
                "actor": "admin@example.com",
                "notes": "Looks good from governance side."
            })
            db.commit()
            print("Added admin_approved fact. The poller should pick this up and transition to sme_review.")
            
            # Wait for poller to run or we can manually force the next fact for the next state?
            # It's better to let the poller run, but we can also just add both facts
            # However, the state machine might only listen for sme_approved when in sme_review state.
            # Actually, `has_sme_approved` checks `self.has_fact("sme_approved")`. The transition condition
            # is `sme_approve = sme_review.to(completed, cond="has_sme_approved")`.
            # If we add the fact now, when the state machine enters `sme_review`, it will immediately transition to `completed`.
            
            print("Simulating SME Approval...")
            add_fact(db, request_id, "sme_approved", {
                "actor": "sme@example.com",
                "notes": "Data contract is accurate."
            })
            db.commit()
            print("Added sme_approved fact. The poller should eventually transition this to completed.")

    except Exception as e:
        print(f"Error: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    # Using the ID for 'sales_data' from earlier logs
    simulate_approvals("9815a0e6-dfa7-4425-a54f-a5b3ae4367b6")
