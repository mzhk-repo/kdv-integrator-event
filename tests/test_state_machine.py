# Скелет тестів для state machine — можна розширити з моками TASKS


def test_task_manager_interface():
    from src.tasks import task_manager
    tid = task_manager.start_task(lambda tid, x: x, 1)
    assert isinstance(tid, str)
    info = task_manager.get_status(tid)
    assert info is not None
