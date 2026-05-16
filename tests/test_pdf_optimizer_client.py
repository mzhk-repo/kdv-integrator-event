import requests

from src.services.pdf import PDFOptimizerClient


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, post_response=None, get_responses=None, post_exc=None):
        self.post_response = post_response or FakeResponse(202, {"status": "processing"})
        self.get_responses = list(get_responses or [])
        self.post_exc = post_exc

    def post(self, *args, **kwargs):
        if self.post_exc:
            raise self.post_exc
        return self.post_response

    def get(self, *args, **kwargs):
        if self.get_responses:
            return self.get_responses.pop(0)
        return FakeResponse(200, {"status": "processing"})


def test_optimizer_unavailable_returns_fallback(tmp_path):
    original = tmp_path / "original.pdf"
    original.write_bytes(b"original")
    client = PDFOptimizerClient(
        "http://optimizer",
        timeout=1,
        poll_interval=0,
        session=FakeSession(post_exc=requests.exceptions.ConnectionError()),
    )

    result = client.optimize(str(original), "job-id")

    assert result.success is False
    assert result.path == str(original)
    assert result.fallback_reason == "optimizer_unavailable"


def test_polling_timeout_returns_fallback(tmp_path):
    original = tmp_path / "original.pdf"
    original.write_bytes(b"original")
    client = PDFOptimizerClient(
        "http://optimizer",
        timeout=0,
        poll_interval=0,
        session=FakeSession(),
    )

    result = client.optimize(str(original), "job-id")

    assert result.success is False
    assert result.path == str(original)
    assert result.fallback_reason == "timeout"


def test_error_status_uses_optimizer_reason(tmp_path):
    original = tmp_path / "original.pdf"
    original.write_bytes(b"original")
    session = FakeSession(
        get_responses=[
            FakeResponse(
                200,
                {
                    "status": "error",
                    "stats": {"fallback_reason": "larger_output"},
                },
            )
        ]
    )
    client = PDFOptimizerClient(
        "http://optimizer", timeout=1, poll_interval=0, session=session
    )

    result = client.optimize(str(original), "job-id")

    assert result.success is False
    assert result.path == str(original)
    assert result.fallback_reason == "larger_output"


def test_done_status_returns_valid_output(tmp_path):
    original = tmp_path / "original.pdf"
    output = tmp_path / "optimized.pdf"
    original.write_bytes(b"original-content")
    output.write_bytes(b"small")
    session = FakeSession(
        get_responses=[
            FakeResponse(
                200,
                {
                    "status": "done",
                    "output_path": str(output),
                    "stats": {
                        "original_mb": 1.0,
                        "final_mb": 0.5,
                        "time_ms": 123,
                        "thread_wait_ms": 10,
                    },
                },
            )
        ]
    )
    client = PDFOptimizerClient(
        "http://optimizer", timeout=1, poll_interval=0, session=session
    )

    result = client.optimize(str(original), "job-id")

    assert result.success is True
    assert result.path == str(output)
    assert result.fallback_reason is None
    assert result.original_mb == 1.0
    assert result.optimized_mb == 0.5
    assert result.optimization_time_ms == 123
    assert result.thread_wait_ms == 10
