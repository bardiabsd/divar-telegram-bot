from __future__ import annotations
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, JSON, ForeignKey, create_engine
from sqlalchemy.orm import declarative_base, relationship, Session
import os

Base = declarative_base()

def get_database_url() -> str:
    return os.getenv("DATABASE_URL", "sqlite:///./data.db")

def make_engine(echo: bool = False):
    url = get_database_url()
    connect_args = {}
    if url.startswith("sqlite"):
        connect_args = {"check_same_thread": False}
    return create_engine(url, echo=echo, future=True, connect_args=connect_args)

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)  # Telegram user id
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    username = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    playlists = relationship("Playlist", back_populates="user", cascade="all, delete-orphan")

class Playlist(Base):
    __tablename__ = "playlists"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title = Column(String, nullable=False)
    category = Column(String, nullable=False)
    city = Column(String, nullable=False)
    district = Column(String, nullable=True)
    filters = Column(JSON, nullable=False, default={})
    created_at = Column(DateTime, default=datetime.utcnow)
    last_seen_ids = Column(JSON, nullable=False, default=list)  # برای جلوگیری از ارسال تکراری

    user = relationship("User", back_populates="playlists")
