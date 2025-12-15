"""
SQL database provider for querying Lakebase.
"""
from typing import Dict, Any, List, Optional
from app.providers.base import BaseProvider
from app.core.exceptions import RetryableError
from app.core.retry import retry_on_retryable
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from app.core.config import settings


class SQLProvider(BaseProvider):
    """SQL provider for database queries."""
    
    def __init__(self, connection_string: Optional[str] = None, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.connection_string = connection_string or settings.DATABASE_URL
        self.engine = create_engine(self.connection_string, pool_pre_ping=True)
        self.SessionLocal = sessionmaker(bind=self.engine)
    
    @retry_on_retryable(max_attempts=5)
    async def execute_query(self, query: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Execute SQL query."""
        try:
            session = self.SessionLocal()
            try:
                result = session.execute(text(query), params or {})
                rows = result.fetchall()
                # Convert to list of dicts
                columns = result.keys()
                return [dict(zip(columns, row)) for row in rows]
            finally:
                session.close()
        except Exception as e:
            raise RetryableError(f"Query execution failed: {str(e)}")
    
    @retry_on_retryable(max_attempts=3)
    async def execute_transaction(self, queries: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Execute transaction with multiple queries."""
        session = self.SessionLocal()
        try:
            session.begin()
            results = []
            for query_data in queries:
                query = query_data["query"]
                params = query_data.get("params", {})
                result = session.execute(text(query), params)
                results.append({"query": query, "rows_affected": result.rowcount})
            session.commit()
            return {"success": True, "results": results}
        except Exception as e:
            session.rollback()
            raise RetryableError(f"Transaction failed: {str(e)}")
        finally:
            session.close()
    
    async def health_check(self) -> bool:
        """Check if database is accessible."""
        try:
            session = self.SessionLocal()
            session.execute(text("SELECT 1"))
            session.close()
            return True
        except:
            return False

