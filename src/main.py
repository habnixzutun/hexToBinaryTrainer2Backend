from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.cors import CORSMiddleware

from src.models import User
from src.database.engine import engine, Base, get_db
from src.repositories import user_repository
import src.models
from src.pydantic_types import AnswerRequest, UserList, ResultResponse, UserType

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/getLeaderboard", response_model=UserList)
async def get_leaderboard(db: AsyncSession = Depends(get_db)):
    users = await user_repository.get_users(db)
    return {
        "entries": [
            {
                "name": user.name,
                "correct": user.correct,
                "incorrect": user.incorrect,
                "points": user.points
            }
            for user in users
        ]
    }

@app.post("/correct", response_model=ResultResponse)
async def correct_answer(request: AnswerRequest, db: AsyncSession = Depends(get_db)):
    user = await user_repository.update_user_data(db, request, is_correct=True)
    return {"message": "Success", "user": UserType(
                                            name=user.name,
                                            correct=user.correct,
                                            incorrect=user.incorrect,
                                            points=user.points
                                        )
            }

@app.post("/incorrect", response_model=ResultResponse)
async def incorrect_answer(request: AnswerRequest, db: AsyncSession = Depends(get_db)):
    user = await user_repository.update_user_data(db, request, is_correct=False)
    return {"message": "Success", "user": UserType(
                                            name=user.name,
                                            correct=user.correct,
                                            incorrect=user.incorrect,
                                            points=user.points
                                        )
            }