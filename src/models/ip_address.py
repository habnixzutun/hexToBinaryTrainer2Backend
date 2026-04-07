from sqlalchemy import Column, Integer, String, ForeignKey
from sqlalchemy.orm import relationship
from src.database.engine import Base


class IpAddress(Base):
    __tablename__ = "ip_addresses"

    id = Column(Integer, primary_key=True, index=True)
    ip = Column(String, nullable=False)

    # Foreign Key auf die users Tabelle
    user_name = Column(String, ForeignKey("users.name", ondelete="CASCADE"))

    # Back-Population für die 1:n Beziehung
    user = relationship("User", back_populates="ip_addresses")