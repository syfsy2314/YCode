from ycode.ui.timer import ResponseTimer


class Clock:
    value = 0.0

    def __call__(self) -> float:
        return self.value


def test_timer_runs_freezes_and_restarts() -> None:
    clock = Clock()
    timer = ResponseTimer(clock)

    timer.start()
    clock.value = 1.25
    assert timer.running is True
    assert timer.elapsed == 1.25

    assert timer.stop() == 1.25
    clock.value = 9.0
    assert timer.running is False
    assert timer.elapsed == 1.25

    timer.start()
    assert timer.elapsed == 0.0
    clock.value = 10.0
    assert timer.elapsed == 1.0


def test_unstarted_timer_is_zero() -> None:
    timer = ResponseTimer(lambda: 10.0)
    assert timer.elapsed == 0.0
    assert timer.stop() == 0.0
