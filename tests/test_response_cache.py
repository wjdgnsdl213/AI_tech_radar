from concurrent.futures import ThreadPoolExecutor
from threading import Event
import pytest
from web.response_cache import cached


def test_coalescing_and_normalized_arguments():
    entered, release = Event(), Event()
    calls = []
    @cached()
    def read(q='AI'):
        calls.append(q)
        entered.set()
        assert release.wait(2)
        return q
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(read)
        assert entered.wait(2)
        second = pool.submit(read, q='AI')
        release.set()
        assert first.result() == second.result() == 'AI'
    assert calls == ['AI']


def test_failure_retry_expiry_and_capacity(monkeypatch):
    clock = [0]
    monkeypatch.setattr('web.response_cache.monotonic', lambda: clock[0])
    calls = []
    @cached(seconds=10, capacity=1)
    def read(key):
        calls.append(key)
        if len(calls) == 1:
            raise ValueError('temporary')
        return len(calls)
    with pytest.raises(ValueError):
        read('a')
    assert read('a') == read('a') == 2
    clock[0] = 11
    assert read('a') == 3
    assert read('b') == 4
    assert read('a') == 5
