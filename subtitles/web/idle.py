"""Automatic shutdown of the interface after a period without activity (`[web] idle_min`, 0 = never).

Counts as activity: an action of the user (POST or PUT request, loading of the page or of data asked for by hand)
and all the time a task or a batch is working. Does not count: the regular polling by a tab left open, nor the
health check of the New Tab page, otherwise the service would never stop.
"""
import re
import time

_POLLING = re.compile(r"^/api/(health|status|tasks|batches)(/|$)")


def is_activity(method: str, path: str) -> bool:
    return method != "GET" or _POLLING.match(path.split("?", 1)[0]) is None


class IdleWatch:
    def __init__(self, timeout_s, busy, clock=time.monotonic):
        self.timeout_s = timeout_s             # function: read again at each check (a change in Settings applies at once)
        self.busy = busy                       # function: True as long as a task or a batch is working
        self.clock = clock
        self.last = clock()

    def touch(self):
        self.last = self.clock()

    def expired(self) -> bool:
        if self.busy():
            self.touch()                       # the countdown starts from the end of the work
            return False
        timeout = self.timeout_s()
        return timeout > 0 and self.clock() - self.last >= timeout

    def remaining_s(self) -> float | None:
        """Seconds before the shutdown; None if no countdown is running (work in progress, or shutdown disabled)."""
        timeout = self.timeout_s()
        if timeout <= 0 or self.busy():
            return None
        return max(0.0, timeout - (self.clock() - self.last))
