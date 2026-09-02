import threading

import pytest

from app import shutdown as shutdown_module


@pytest.fixture(autouse=True)
def reset_state():
    shutdown_module._reset_for_tests()
    yield
    shutdown_module._reset_for_tests()


def test_first_request_wins():
    assert shutdown_module.request_shutdown("application") is True
    assert shutdown_module.is_shutdown_requested() is True
    assert shutdown_module.shutdown_source() == "application"


def test_repeated_request_returns_false_and_preserves_source():
    assert shutdown_module.request_shutdown("first") is True
    assert shutdown_module.request_shutdown("second") is False
    assert shutdown_module.shutdown_source() == "first"


def test_read_api_without_request():
    assert shutdown_module.is_shutdown_requested() is False
    assert shutdown_module.shutdown_source() is None


def test_thread_safe_first_wins():
    barrier = threading.Barrier(8)
    results = []

    def worker(source):
        barrier.wait()
        results.append(shutdown_module.request_shutdown(source))

    threads = [threading.Thread(target=worker, args=(f"s{i}",)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count(True) == 1
    assert results.count(False) == 7
    assert shutdown_module.is_shutdown_requested() is True
    assert shutdown_module.shutdown_source() in {f"s{i}" for i in range(8)}
