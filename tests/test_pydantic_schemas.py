"""Tests for Pydantic schemas (pydantic_types.py).

Verifies validation, serialization, and __str__ methods for all schema types.
Covers God Nodes: AnswerRequest, UserType.
"""

import pytest
from pydantic import ValidationError

from src.pydantic_types import (
    AnswerRequest,
    ResultResponse,
    UserList,
    UserRequest,
    UserType,
)


# ============================================================================
# AnswerRequest Tests
# ============================================================================


class TestAnswerRequest:
    """Test AnswerRequest schema."""

    def test_valid_answer_request(self):
        """Verify AnswerRequest accepts valid data."""
        request = AnswerRequest(name="test_user", bits=5)
        assert request.name == "test_user"
        assert request.bits == 5

    def test_answer_request_with_zero_bits(self):
        """Verify AnswerRequest rejects zero bits (US-03 validation)."""
        with pytest.raises(ValidationError) as exc_info:
            AnswerRequest(name="test_user", bits=0)
        assert "bits" in str(exc_info.value)

    def test_answer_request_with_negative_bits(self):
        """Verify AnswerRequest rejects negative bits (US-03 validation)."""
        with pytest.raises(ValidationError) as exc_info:
            AnswerRequest(name="test_user", bits=-5)
        assert "bits" in str(exc_info.value)

    def test_answer_request_missing_name(self):
        """Verify AnswerRequest rejects missing name."""
        with pytest.raises(ValidationError) as exc_info:
            AnswerRequest(bits=5)
        assert "name" in str(exc_info.value)

    def test_answer_request_missing_bits(self):
        """Verify AnswerRequest rejects missing bits."""
        with pytest.raises(ValidationError) as exc_info:
            AnswerRequest(name="test_user")
        assert "bits" in str(exc_info.value)

    def test_answer_request_str_method(self):
        """Verify AnswerRequest __str__ format."""
        request = AnswerRequest(name="test_user", bits=5)
        result = str(request)
        assert result == "Name: test_user, Bits: 5"

    def test_answer_request_str_with_different_values(self):
        """Verify AnswerRequest __str__ with various inputs."""
        request = AnswerRequest(name="alice", bits=10)
        assert str(request) == "Name: alice, Bits: 10"

    def test_answer_request_serialization(self):
        """Verify AnswerRequest serializes to dict correctly."""
        request = AnswerRequest(name="test_user", bits=5)
        data = request.model_dump()
        assert data == {"name": "test_user", "bits": 5}

    def test_answer_request_deserialization(self):
        """Verify AnswerRequest deserializes from dict correctly."""
        data = {"name": "test_user", "bits": 5}
        request = AnswerRequest.model_validate(data)
        assert request.name == "test_user"
        assert request.bits == 5


# ============================================================================
# UserRequest Tests
# ============================================================================


class TestUserRequest:
    """Test UserRequest schema."""

    def test_valid_user_request(self):
        """Verify UserRequest accepts valid data."""
        request = UserRequest(name="test_user")
        assert request.name == "test_user"

    def test_user_request_missing_name(self):
        """Verify UserRequest rejects missing name."""
        with pytest.raises(ValidationError) as exc_info:
            UserRequest()
        assert "name" in str(exc_info.value)

    def test_user_request_serialization(self):
        """Verify UserRequest serializes to dict correctly."""
        request = UserRequest(name="test_user")
        data = request.model_dump()
        assert data == {"name": "test_user"}


# ============================================================================
# UserType Tests
# ============================================================================


class TestUserType:
    """Test UserType schema."""

    def test_valid_user_type(self):
        """Verify UserType accepts valid data."""
        user = UserType(name="test_user", correct=10, incorrect=2, points=50)
        assert user.name == "test_user"
        assert user.correct == 10
        assert user.incorrect == 2
        assert user.points == 50

    def test_user_type_with_zeros(self):
        """Verify UserType accepts zero values."""
        user = UserType(name="new_user", correct=0, incorrect=0, points=0)
        assert user.correct == 0
        assert user.incorrect == 0
        assert user.points == 0

    def test_user_type_missing_required_fields(self):
        """Verify UserType rejects missing required fields."""
        with pytest.raises(ValidationError) as exc_info:
            UserType(name="test_user")
        assert "correct" in str(exc_info.value) or "incorrect" in str(exc_info.value) or "points" in str(exc_info.value)

    def test_user_type_serialization(self):
        """Verify UserType serializes to dict correctly."""
        user = UserType(name="test_user", correct=10, incorrect=2, points=50)
        data = user.model_dump()
        assert data == {
            "name": "test_user",
            "correct": 10,
            "incorrect": 2,
            "points": 50,
        }

    def test_user_type_deserialization(self):
        """Verify UserType deserializes from dict correctly."""
        data = {"name": "test_user", "correct": 10, "incorrect": 2, "points": 50}
        user = UserType.model_validate(data)
        assert user.name == "test_user"
        assert user.correct == 10


# ============================================================================
# UserList Tests
# ============================================================================


class TestUserList:
    """Test UserList schema."""

    def test_valid_user_list(self):
        """Verify UserList accepts valid data."""
        users = [
            UserType(name="user1", correct=10, incorrect=2, points=50),
            UserType(name="user2", correct=8, incorrect=3, points=40),
        ]
        user_list = UserList(entries=users)
        assert len(user_list.entries) == 2
        assert user_list.entries[0].name == "user1"

    def test_empty_user_list(self):
        """Verify UserList accepts empty list."""
        user_list = UserList(entries=[])
        assert len(user_list.entries) == 0

    def test_user_list_with_single_entry(self):
        """Verify UserList accepts single entry."""
        users = [UserType(name="user1", correct=10, incorrect=2, points=50)]
        user_list = UserList(entries=users)
        assert len(user_list.entries) == 1

    def test_user_list_serialization(self):
        """Verify UserList serializes to dict correctly."""
        users = [
            UserType(name="user1", correct=10, incorrect=2, points=50),
            UserType(name="user2", correct=8, incorrect=3, points=40),
        ]
        user_list = UserList(entries=users)
        data = user_list.model_dump()
        assert len(data["entries"]) == 2
        assert data["entries"][0]["name"] == "user1"


# ============================================================================
# ResultResponse Tests
# ============================================================================


class TestResultResponse:
    """Test ResultResponse schema."""

    def test_valid_result_response(self):
        """Verify ResultResponse accepts valid data."""
        user = UserType(name="test_user", correct=10, incorrect=2, points=50)
        response = ResultResponse(message="Success", user=user)
        assert response.message == "Success"
        assert response.user.name == "test_user"

    def test_result_response_missing_message(self):
        """Verify ResultResponse rejects missing message."""
        user = UserType(name="test_user", correct=10, incorrect=2, points=50)
        with pytest.raises(ValidationError) as exc_info:
            ResultResponse(user=user)
        assert "message" in str(exc_info.value)

    def test_result_response_missing_user(self):
        """Verify ResultResponse rejects missing user."""
        with pytest.raises(ValidationError) as exc_info:
            ResultResponse(message="Success")
        assert "user" in str(exc_info.value)

    def test_result_response_serialization(self):
        """Verify ResultResponse serializes to dict correctly."""
        user = UserType(name="test_user", correct=10, incorrect=2, points=50)
        response = ResultResponse(message="Success", user=user)
        data = response.model_dump()
        assert data["message"] == "Success"
        assert data["user"]["name"] == "test_user"
        assert data["user"]["correct"] == 10
