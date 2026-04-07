from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.engine import engine, Base, get_db
from src.repositories import user_repository
import src.models
from src.pydantic_types import AnswerRequest

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield

app = FastAPI(lifespan=lifespan)



@app.get("/getLeaderboard")
async def get_leaderboard(db: AsyncSession = Depends(get_db)):
    users = await user_repository.get_users(db)
    return users

@app.post("/correct")
async def correct_answer(request: AnswerRequest, db: AsyncSession = Depends(get_db)):
    user = await user_repository.update_user_data(db, request, is_correct=True)
    return {"message": "Success", "user": user}

@app.post("/incorrect")
async def incorrect_answer(request: AnswerRequest, db: AsyncSession = Depends(get_db)):
    user = await user_repository.update_user_data(db, request, is_correct=False)
    return {"message": "Success", "user": user}