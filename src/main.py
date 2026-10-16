from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.cors import CORSMiddleware

from src.config import settings
from src.database.engine import Base, engine, get_db
from src.pydantic_types import (
    AnswerRequest,
    ResultResponse,
    UserList,
    UserRequest,
    UserType,
)
from src.repositories import user_repository


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/leaderboard", response_model=UserList)
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

@app.post("/api/user", response_model=UserType)
async def get_user(request: UserRequest, db: AsyncSession = Depends(get_db)):
    user = await user_repository.get_user(db, request.name)
    return UserType(
            name=user.name,
            correct=user.correct,
            incorrect=user.incorrect,
            points=user.points
        )

@app.post("/api/correct", response_model=ResultResponse)
async def correct_answer(request: AnswerRequest, db: AsyncSession = Depends(get_db)):
    user = await user_repository.update_user_data(db, request, is_correct=True)
    return {"message": "Success", "user": UserType(
                                            name=user.name,
                                            correct=user.correct,
                                            incorrect=user.incorrect,
                                            points=user.points
                                        )
            }

@app.post("/api/incorrect", response_model=ResultResponse)
async def incorrect_answer(request: AnswerRequest, db: AsyncSession = Depends(get_db)):
    print(request)
    user = await user_repository.update_user_data(db, request, is_correct=False)
    return {"message": "Success", "user": UserType(
                                            name=user.name,
                                            correct=user.correct,
                                            incorrect=user.incorrect,
                                            points=user.points
                                        )
            }
