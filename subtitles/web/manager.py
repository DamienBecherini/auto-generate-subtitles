"""The work of the service: the task queue and the batch manager, put together, and what concerns both of them."""
from .. import i18n
from . import timing
from .batch_manager import BatchManager
from .tasks import TaskQueue


class Manager:
    def __init__(self, queue: TaskQueue | None = None, start: bool = True):
        self.queue = queue or TaskQueue()
        self.batches = BatchManager(self.queue)
        if start:
            self.queue.start()

    def busy(self) -> bool:
        """A task runs or waits for its turn, or a batch chains its videos."""
        return self.queue.current() is not None or self.batches.running() is not None

    def activity(self) -> str:
        """What is moving forward, in a few words ("local service" contract): "batch 3/22" or the step of the task; empty if nothing."""
        batch = self.batches.running()
        if batch:
            return batch.progress_text()
        t = self.queue.current()
        return (t.step or i18n.t("activity.task")) if t else ""

    def remaining_s(self) -> int | None:
        """Seconds before the end of the running work (batch, otherwise single task), if it can be estimated."""
        batch = self.batches.running()
        if batch:
            return batch.remaining_s(timing.known_rate(batch.phase, batch.options))
        t = self.queue.current()
        return t.remaining_s() if t else None

    def close(self):
        self.queue.close()
