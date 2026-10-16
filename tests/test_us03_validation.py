"""US-03: AnswerRequest Security Validation Tests

Tests for AK-3: AnswerRequest.bits must validate gt=0 to prevent point manipulation.
Tests for AK-4: incorrect_answer must not print to stdout.
"""

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from src.main import app
from src.pydantic_types import AnswerRequest


class TestAnswerRequestBitsValidation:
    """Test AK-3: AnswerRequest.bits must validate gt=0."""

    def test_answer_request_rejects_zero_bits(self):
        """AK-3.1: AnswerRequest must reject bits=0 with ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            AnswerRequest(name="test_user", bits=0)

        # Verify the error is about 'bits' field
        error_str = str(exc_info.value)
        assert "bits" in error_str

    def test_answer_request_rejects_negative_bits(self):
        """AK-3.2: AnswerRequest must reject bits=-5 with ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            AnswerRequest(name="test_user", bits=-5)

        # Verify the error is about 'bits' field
        error_str = str(exc_info.value)
        assert "bits" in error_str

    def test_answer_request_rejects_negative_one_bits(self):
        """AK-3.3: AnswerRequest must reject bits=-1 with ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            AnswerRequest(name="test_user", bits=-1)

        error_str = str(exc_info.value)
        assert "bits" in error_str

    def test_answer_request_accepts_positive_bits(self):
        """AK-3.4: AnswerRequest must accept bits=1 (minimum valid value)."""
        request = AnswerRequest(name="test_user", bits=1)
        assert request.bits == 1

    def test_answer_request_accepts_large_positive_bits(self):
        """AK-3.5: AnswerRequest must accept large positive values."""
        request = AnswerRequest(name="test_user", bits=1000)
        assert request.bits == 1000


class TestIncorrectAnswerNoPrint:
    """Test AK-4: incorrect_answer must not print to stdout."""

    @pytest.mark.asyncio
    async def test_incorrect_answer_no_stdout(self, app_dependency_override, capsys):
        """AK-4.1: POST /api/incorrect must not print request to stdout."""
        async with AsyncClient(
            transport=ASGITransport(app=app_dependency_override),
            base_url="http://test",
        ) as client:
            # Call incorrect endpoint
            response = await client.post(
                "/api/incorrect",
                json={"name": "test_user", "bits": 5}
            )

            # Verify endpoint succeeded
            assert response.status_code == 200

            # Capture stdout - should be empty
            captured = capsys.readouterr()
            assert captured.out == "", f"incorrect_answer printed to stdout: {captured.out}"
            # Note: stderr is allowed (logging)

    @pytest.mark.asyncio
    async def test_correct_answer_no_stdout(self, app_dependency_override, capsys):
        """AK-4.2: POST /api/correct must not print to stdout (for consistency)."""
        async with AsyncClient(
            transport=ASGITransport(app=app_dependency_override),
            base_url="http://test",
        ) as client:
            # Call correct endpoint
            response = await client.post(
                "/api/correct",
                json={"name": "test_user", "bits": 5}
            )

            # Verify endpoint succeeded
            assert response.status_code == 200

            # Capture stdout - should be empty
            captured = capsys.readouterr()
            assert captured.out == "", f"correct_answer printed to stdout: {captured.out}"
