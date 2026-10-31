"""SDK imports use a disposable root; tests cannot open sockets."""

import os
import socket
from tempfile import TemporaryDirectory

import pytest

SDK_ROOT = pytest.StashKey[TemporaryDirectory]()
PREVIOUS_ROOT = pytest.StashKey[str | None]()


def pytest_configure(config):
    config.stash[PREVIOUS_ROOT] = os.environ.get("ASTRBOT_ROOT")
    config.stash[SDK_ROOT] = TemporaryDirectory(prefix="noreply-sdk-")
    os.environ["ASTRBOT_ROOT"] = config.stash[SDK_ROOT].name


def pytest_unconfigure(config):
    previous = config.stash[PREVIOUS_ROOT]
    if previous is None:
        os.environ.pop("ASTRBOT_ROOT", None)
    else:
        os.environ["ASTRBOT_ROOT"] = previous
    config.stash[SDK_ROOT].cleanup()


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        pytest.fail("Offline tests attempted to connect to a network socket")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)
