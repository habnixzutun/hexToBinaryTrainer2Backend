from pydantic import BaseModel


class AnswerRequest(BaseModel):
    name: str
    bits: int
    ip_address: str
    mode: str

    def __str__(self):
        return (f"Name: {self.name}"
                f"Bits: {self.bits}"
                f"Mode: {self.mode}"
                f"IP Address: {self.ip_address}")
