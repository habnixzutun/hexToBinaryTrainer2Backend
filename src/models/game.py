"""Game statistics models."""

from sqlalchemy import Column, Integer, ForeignKey, String
from sqlalchemy.orm import relationship

from src.database.engine import Base


class UserStats(Base):
    """User game statistics."""

    __tablename__ = "user_stats"

    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    correct_count = Column(Integer, default=0, nullable=False)
    incorrect_count = Column(Integer, default=0, nullable=False)
    points = Column(Integer, default=0, nullable=False)

    user = relationship("User")
