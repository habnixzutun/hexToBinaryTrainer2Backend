# src/models/user.py
from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import relationship
from src.database.engine import Base


class User(Base):
    __tablename__ = "users"

    name = Column(String, primary_key=True, index=True)
    correct = Column(Integer, default=0)
    incorrect = Column(Integer, default=0)
    points = Column(Integer, default=0)

    # 1:n Beziehung zu IpAddress
    # cascade="all, delete-orphan" sorgt dafür, dass IPs gelöscht werden, wenn der User gelöscht wird
    ip_addresses = relationship("IpAddress", back_populates="user", cascade="all, delete-orphan")