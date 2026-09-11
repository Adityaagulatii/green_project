import pytest
from remote import TRANSPORTS


@pytest.fixture(params=TRANSPORTS)
def transport(request):
    """Each binding in turn: "tcp" (JSON lines) and "ws" (WebSocket)."""
    return request.param
