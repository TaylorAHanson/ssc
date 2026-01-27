---
trigger: always_on
---

## Context
- When a new context or conversation is started, review backend/ARCHITECTURE.md for a detailed explanation on how the backend works. If any significant changes are needed to this file, make sure to include that as part of the implementation plan.

## Development and Troubleshooting Tools
- In local development, we store a sqllite database at backend/edas_hub.db. You may execute queries on the local database as needed.
- For troubleshooting, you may tail the logs at backend.log or frontend.log. This is especially helpful for python errors or for getting log statements. 

