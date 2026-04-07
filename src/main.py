from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.engine import engine, Base, get_db
from src.repositories import user_repository
import src.models

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield

app = FastAPI(lifespan=lifespan)

class NameRequest(BaseModel):
    name: str

@app.get("/getLeaderboard")
async def get_leaderboard(db: AsyncSession = Depends(get_db)):
    users = await user_repository.getUsers(db)
    return users

@app.post("/correct")
async def correct_answer(request: NameRequest, db: AsyncSession = Depends(get_db)):
    user = await user_repository.updateUserData(db, request.name, is_correct=True)
    return {"message": "Success", "user": user}

@app.post("/incorrect")
async def incorrect_answer(request: NameRequest, db: AsyncSession = Depends(get_db)):
    user = await user_repository.updateUserData(db, request.name, is_correct=False)
    return {"message": "Success", "user": user}