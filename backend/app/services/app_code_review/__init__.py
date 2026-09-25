"""Automated security review of a Databricks App's source code.

Backs the ``review_databricks_app_code`` workflow tool. The pipeline pins a
commit, reads its tarball in memory, runs deterministic checks (whose identity
reads data, committed secrets), then has an LLM reviewer with read-only access
to the snapshot give a recommendation that the checks bound from below.
"""
from app.services.app_code_review.review import review_app_code

__all__ = ["review_app_code"]
