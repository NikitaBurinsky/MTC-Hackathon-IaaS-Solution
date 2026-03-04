from enum import Enum


class InstanceStatus(str, Enum):
    PROVISIONING = "PROVISIONING"
    RUNNING = "RUNNING"
    STOPPED = "STOPPED"
    ERROR = "ERROR"
    TERMINATED = "TERMINATED"


class InstanceOperationType(str, Enum):
    CREATE = "create"
    DELETE = "delete"
    ACTION = "action"


class InstanceOperationStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class ActionType(str, Enum):
    START = "start"
    STOP = "stop"
    REBOOT = "reboot"


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class TaskRunStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class ScriptSourceType(str, Enum):
    BODY = "body"
    SCRIPT_ID = "script_id"
