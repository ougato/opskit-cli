"""One monotonic budget shared by sequential operation stages."""
import math
import time


class Deadline:
    def __init__(self, timeout, label):
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        self.end = time.monotonic() + timeout
        self.label = label

    def remaining(self, cap=None):
        budget = self.end - time.monotonic()
        if budget <= 0:
            raise TimeoutError(self.label + " deadline exceeded")
        return min(budget, cap) if cap is not None else budget

    def call(self, operation, *args, **kwargs):
        self.remaining()
        result = operation(*args, **kwargs)
        self.remaining()
        return result
