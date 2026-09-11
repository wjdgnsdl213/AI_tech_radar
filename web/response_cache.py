"""Bounded short-lived public read cache with per-key request coalescing."""
from collections import OrderedDict
from concurrent.futures import Future
from functools import wraps
from inspect import signature
from threading import Lock
from time import monotonic


def cached(seconds=30, capacity=128):
    def decorate(fn):
        values, pending, lock = OrderedDict(), {}, Lock()
        sig = signature(fn, eval_str=True)

        @wraps(fn)
        def wrapped(*args, **kwargs):
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            key = tuple(bound.arguments.items())
            with lock:
                entry = values.get(key)
                if entry and entry[0] > monotonic():
                    values.move_to_end(key)
                    return entry[1]
                owner = key not in pending
                future = pending.setdefault(key, Future())
            if not owner:
                return future.result()
            try:
                result = fn(*args, **kwargs)
                with lock:
                    values[key] = (monotonic() + seconds, result)
                    values.move_to_end(key)
                    while len(values) > capacity:
                        values.popitem(last=False)
                future.set_result(result)
                return result
            except BaseException as exc:
                future.set_exception(exc)
                raise
            finally:
                with lock:
                    pending.pop(key, None)
        wrapped.__signature__ = sig
        return wrapped
    return decorate
