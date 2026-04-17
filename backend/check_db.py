from app.db.session import get_lakebase_session
from app.db import RequestModel

db = get_lakebase_session()
req = db.query(RequestModel).filter(RequestModel.type == "enforcement_sentinel").order_by(RequestModel.created_at.desc()).first()
if req:
    print(f"Request ID: {req.id}")
    violations = req.state_context.get("violations", [])
    if violations:
        print(f"Violations count: {len(violations)}")
        print("Keys in first violation:", violations[0].keys())
        print(violations[0])
    else:
        print("No violations")
else:
    print("No requests found")
