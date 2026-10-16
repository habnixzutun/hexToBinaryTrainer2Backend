from pydantic import BaseModel, ConfigDict, Field


class AnswerRequest(BaseModel):
    name: str
    bits: int = Field(gt=0)

    def __str__(self):
        return (f"Name: {self.name}, "
                f"Bits: {self.bits}")

class UserRequest(BaseModel):
    name: str

class UserType(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    correct: int
    incorrect: int
    points: int

class UserList(BaseModel):
    entries: list[UserType]

class ResultResponse(BaseModel):
    message: str
    user: UserType
