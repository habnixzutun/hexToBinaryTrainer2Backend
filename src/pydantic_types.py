from pydantic import BaseModel


class AnswerRequest(BaseModel):
    name: str
    bits: int
    mode: str

    def __str__(self):
        return (f"Name: {self.name}"
                f"Bits: {self.bits}"
                f"Mode: {self.mode}")

class UserType(BaseModel):
    name: str
    correct: int
    incorrect: int
    points: int

class UserList(BaseModel):
    entries: list[UserType]

class ResultResponse(BaseModel):
    message: str
    user: UserType