"""
Base database model class.
"""
from sqlalchemy.ext.declarative import declarative_base # For older SQLA in some envs, or use .orm
# Actually the warning said: sqlalchemy.orm.declarative_base()
from sqlalchemy.orm import declarative_base

Base = declarative_base()
