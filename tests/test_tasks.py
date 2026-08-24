from src.tasks import TASKS, TaskManager
from scripts.robot import RobotBatchError


def test_task_manager_marks_robot_batch_error_as_failed():
    task_id = "robot-batch-error"
    TASKS[task_id] = {
        "status": "queued",
        "created_at": 0,
        "progress": "Task initialized",
        "result": None,
        "error": None,
    }

    manager = TaskManager()

    def failed_batch(_task_id):
        raise RobotBatchError("Robot batch completed with errors: FAILED=1")

    manager._wrapper(task_id, failed_batch, (), {})

    assert TASKS[task_id]["status"] == "error"
    assert TASKS[task_id]["progress"] == "Failed"
    assert TASKS[task_id]["error"] == "Robot batch completed with errors: FAILED=1"
    del TASKS[task_id]
