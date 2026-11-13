"""States of the tasks, batches, videos of a batch and folder inventories.

StrEnum: each member is its own text ("running"), so the JSON files, the API and the page keep the same values, and a
state read back from a file (plain text) compares equal to its member. A typo in the code is an AttributeError
instead of a comparison that is silently always false.
"""
from enum import StrEnum


class TaskState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"                  # between the two phases (estimate, names to validate)
    DONE = "done"
    ERROR = "error"
    CANCELLED = "cancelled"


ACTIVE_TASK_STATES = (TaskState.QUEUED, TaskState.RUNNING)
FINISHED_TASK_STATES = (TaskState.DONE, TaskState.ERROR, TaskState.CANCELLED)


class BatchState(StrEnum):
    RUNNING = "running"
    SUSPENDED = "suspended"
    AWAITING_TRANSLATION = "awaiting_translation"
    DONE = "done"
    CANCELLED = "cancelled"


FINISHED_BATCH_STATES = (BatchState.DONE, BatchState.CANCELLED)


class VideoState(StrEnum):
    TODO = "todo"
    RUNNING = "running"
    PREPARED = "prepared"              # phase 1 done, its task paused before the translation
    TRANSLATED = "translated"          # subtitles done, waiting for the dubbing (phase 3)
    DONE = "done"
    ERROR = "error"
    CANCELLED = "cancelled"


class ScanState(StrEnum):
    SCANNING = "scanning"
    LANGUAGES = "languages"            # list known, reading the language of the audio tracks
    DONE = "done"
    CANCELLED = "cancelled"
    ERROR = "error"
