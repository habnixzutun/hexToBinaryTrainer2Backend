from pydantic import BaseModel


class AnswerRequest(BaseModel):
    name: str
    bits: int

    def __str__(self):
        return (f"Name: {self.name}, "
                f"Bits: {self.bits}")

class UserRequest(BaseModel):
    name: str

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