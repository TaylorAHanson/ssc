from app.db.session import get_lakebase_session
from app.db.request import RequestModel
from sqlalchemy import desc
db = get_lakebase_session()
req = db.query(RequestModel).filter(RequestModel.type == "data_certification").order_by(desc(RequestModel.created_at)).first()
if req:
    print(f"Request ID: {req.id}")
    import json
    print(json.dumps(req.state_context, indent=2))
else:
    print("No requests found.")
