# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Guided BSL button check of Maui v1.2 (ec_bsl_invoke GPIO).

The operator is led through three phases while the GPIO is polled in one
console session:

1. "Do not touch the BSL button": every sample must read released (0). A 1
   here means the line floats or is pulled up (e.g. R0714 missing), which a
   single "pressed" read could not tell apart from a real press.
2. "Press and HOLD the BSL button now": a press within the timeout, then
   pressed (1) for a minimum time of consecutive samples.
3. "Release the button": released within the timeout, then released (0) for
   a minimum time of consecutive samples.

BslButtonCheck.run() yields a BslUpdate per sample (live prompt, countdown)
and a final update with the verdict; summary() describes the samples of all
phases for the log.
"""

import dataclasses
import time
from typing import Callable
from typing import Iterator
from typing import List
from typing import Optional
from typing import Tuple

from server import maui_v12_profile as profile


PHASE_IDLE = 1
PHASE_PRESS = 2
PHASE_RELEASE = 3
PHASE_NAMES = {
    PHASE_IDLE: "Do not touch",
    PHASE_PRESS: "Press and hold",
    PHASE_RELEASE: "Release",
}

PROMPT_IDLE = "Do not touch the BSL button"
PROMPT_PRESS = "Press and HOLD the BSL button now"
PROMPT_HOLD = "Keep holding the BSL button"
PROMPT_RELEASE = "Release the BSL button"
PROMPT_RELEASED = "Keep the BSL button released"

UNKNOWN = -1


@dataclasses.dataclass
class BslUpdate:  # pylint: disable=too-many-instance-attributes
    """Progress of the check (one per sample) or its final verdict."""

    phase: int
    prompt: str
    seconds_left: float = 0.0
    level: int = UNKNOWN
    done: bool = False
    success: bool = False
    message: str = ""
    failed_phase: int = 0


@dataclasses.dataclass
class BslSample:
    """One GPIO reading; |t| in seconds since the start of the check."""

    t: float
    level: int


@dataclasses.dataclass
class PhaseResult:
    """Samples and verdict of one phase."""

    phase: int
    samples: List[BslSample] = dataclasses.field(default_factory=list)
    passed: bool = False
    detail: str = ""


def run_lengths(samples: List[BslSample]) -> str:
    """E.g. "0x17 [0.00-2.03 s], 1x13 [2.15-3.71 s]" ('?' = unreadable)."""
    runs: List[Tuple[int, int, float, float]] = []
    for s in samples:
        if runs and runs[-1][0] == s.level:
            level, count, start, _ = runs[-1]
            runs[-1] = (level, count + 1, start, s.t)
        else:
            runs.append((s.level, 1, s.t, s.t))
    return ", ".join(
        f"{'?' if level == UNKNOWN else level}x{count} [{start:.2f}-{end:.2f} s]"
        for level, count, start, end in runs
    )


class BslButtonCheck:  # pylint: disable=too-many-instance-attributes
    """Runs the 3-phase check on a GPIO reader.

    |read_level| returns (level, raw_output) of one `gpio get` (level -1 if
    the reply could not be parsed). |is_missing(raw)| tells whether the
    firmware has no such GPIO, which ends the check at once.
    """

    def __init__(  # pylint: disable=too-many-arguments
        self,
        read_level: Callable[[], Tuple[int, str]],
        *,
        is_missing: Callable[[str], bool] = lambda raw: False,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        interval_s: float = profile.BSL_POLL_INTERVAL_S,
    ) -> None:
        self._read_level = read_level
        self._is_missing = is_missing
        self._clock = clock
        self._sleep = sleep
        self._interval_s = interval_s
        self._t0 = 0.0
        self._last_sample_at: Optional[float] = None
        self.phases: List[PhaseResult] = []
        self.last_raw = ""
        self.success = False
        self.message = ""
        self.failed_phase = 0
        self.gpio_missing = False

    def _now(self) -> float:
        return self._clock() - self._t0

    def _sample(self, phase: PhaseResult) -> BslSample:
        """Waits for the poll interval and reads the GPIO once."""
        if self._last_sample_at is not None:
            wait = self._interval_s - (self._clock() - self._last_sample_at)
            if wait > 0:
                self._sleep(wait)
        self._last_sample_at = self._clock()
        level, raw = self._read_level()
        self.last_raw = raw
        if level not in (profile.BSL_GPIO_PRESSED, profile.BSL_GPIO_RELEASED):
            level = UNKNOWN
            if self._is_missing(raw):
                self.gpio_missing = True
        sample = BslSample(t=round(self._last_sample_at - self._t0, 3), level=level)
        phase.samples.append(sample)
        return sample

    def _fail(self, phase: PhaseResult, reason: str) -> BslUpdate:
        phase.passed = False
        phase.detail = reason
        self.success = False
        self.failed_phase = phase.phase
        self.message = (
            f"BSL Button Test FAILED (step {phase.phase}, "
            f"{PHASE_NAMES[phase.phase]}): {reason}"
        )
        return BslUpdate(
            phase=phase.phase,
            prompt=reason,
            level=phase.samples[-1].level if phase.samples else UNKNOWN,
            done=True,
            success=False,
            message=self.message,
            failed_phase=phase.phase,
        )

    def _missing_reason(self) -> str:
        return (
            f"this MCU firmware has no '{profile.BSL_GPIO_NAME}' GPIO - update "
            "the MCU App"
        )

    def _phase_idle(self) -> Iterator[BslUpdate]:
        phase = PhaseResult(PHASE_IDLE)
        self.phases.append(phase)
        start = self._now()
        readable = 0
        while True:
            sample = self._sample(phase)
            if self.gpio_missing:
                yield self._fail(phase, self._missing_reason())
                return
            if sample.level == profile.BSL_GPIO_PRESSED:
                yield self._fail(
                    phase,
                    f"BSL signal reads {profile.BSL_GPIO_PRESSED} without pressing "
                    "- R0714 missing or line floating?",
                )
                return
            if sample.level != UNKNOWN:
                readable += 1
            elapsed = sample.t - start
            if elapsed >= profile.BSL_IDLE_S:
                break
            yield BslUpdate(
                phase=PHASE_IDLE,
                prompt=PROMPT_IDLE,
                seconds_left=max(profile.BSL_IDLE_S - elapsed, 0.0),
                level=sample.level,
            )
        if not readable:
            yield self._fail(
                phase, f"could not read GPIO {profile.BSL_GPIO_NAME} from the MCU"
            )
            return
        phase.passed = True
        phase.detail = (
            f"{readable} sample(s) read {profile.BSL_GPIO_RELEASED} "
            f"in {sample.t - start:.2f} s"
        )

    def _wait_stable(  # pylint: disable=too-many-arguments
        self,
        phase: PhaseResult,
        level: int,
        timeout_s: float,
        stable_s: float,
        prompts: Tuple[str, str],
    ) -> Iterator[BslUpdate]:
        """Waits up to |timeout_s| for |level|, then for |stable_s| of it.

        A run of |level| that started before the timeout may finish after
        it; another level resets the run. Sets phase.detail. The final
        verdict is phase.passed (no failure update is yielded here).
        """
        start = self._now()
        run_start: Optional[float] = None
        first_seen: Optional[float] = None
        longest = 0.0
        while True:
            sample = self._sample(phase)
            if self.gpio_missing:
                phase.detail = self._missing_reason()
                return
            if sample.level == level:
                if run_start is None:
                    run_start = sample.t
                    if first_seen is None:
                        first_seen = sample.t - start
                held = sample.t - run_start
                longest = max(longest, held)
                if held >= stable_s:
                    phase.passed = True
                    phase.detail = (
                        f"{level} after {first_seen:.2f} s, stable for {held:.2f} s"
                    )
                    return
                yield BslUpdate(
                    phase=phase.phase,
                    prompt=prompts[1],
                    seconds_left=max(stable_s - held, 0.0),
                    level=sample.level,
                )
                continue
            if sample.level != UNKNOWN:
                run_start = None
            waited = sample.t - start
            if waited >= timeout_s and run_start is None:
                phase.passed = False
                phase.detail = (
                    "no change"
                    if first_seen is None
                    else f"unstable (longest {longest:.2f} s, needs {stable_s:.1f} s)"
                )
                return
            yield BslUpdate(
                phase=phase.phase,
                prompt=prompts[0] if run_start is None else prompts[1],
                seconds_left=max(timeout_s - waited, 0.0),
                level=sample.level,
            )

    def _phase_press(self) -> Iterator[BslUpdate]:
        phase = PhaseResult(PHASE_PRESS)
        self.phases.append(phase)
        yield from self._wait_stable(
            phase,
            profile.BSL_GPIO_PRESSED,
            profile.BSL_PRESS_TIMEOUT_S,
            profile.BSL_HOLD_S,
            (PROMPT_PRESS, PROMPT_HOLD),
        )
        if phase.passed:
            return
        if self.gpio_missing:
            reason = self._missing_reason()
        elif phase.detail == "no change":
            reason = (
                f"no press detected within {profile.BSL_PRESS_TIMEOUT_S:.0f} s "
                f"({profile.BSL_GPIO_NAME} stayed {profile.BSL_GPIO_RELEASED})"
            )
        else:
            reason = (
                f"signal not stable while pressed ({phase.detail}) - hold the "
                "button down"
            )
        yield self._fail(phase, reason)

    def _phase_release(self) -> Iterator[BslUpdate]:
        phase = PhaseResult(PHASE_RELEASE)
        self.phases.append(phase)
        yield from self._wait_stable(
            phase,
            profile.BSL_GPIO_RELEASED,
            profile.BSL_RELEASE_TIMEOUT_S,
            profile.BSL_RELEASED_STABLE_S,
            (PROMPT_RELEASE, PROMPT_RELEASED),
        )
        if phase.passed:
            return
        if self.gpio_missing:
            reason = self._missing_reason()
        elif phase.detail == "no change":
            reason = (
                f"signal stays {profile.BSL_GPIO_PRESSED} after release "
                f"({profile.BSL_RELEASE_TIMEOUT_S:.0f} s) - button stuck?"
            )
        else:
            reason = f"signal not stable after release ({phase.detail})"
        yield self._fail(phase, reason)

    def run(self) -> Iterator[BslUpdate]:
        """Runs all phases; the last update has done=True."""
        self._t0 = self._clock()
        for phase_run in (self._phase_idle, self._phase_press, self._phase_release):
            for update in phase_run():
                yield update
                if update.done:
                    return
        self.success = True
        self.message = (
            f"BSL Button Test PASSED! GPIO {profile.BSL_GPIO_NAME} read "
            f"{profile.BSL_GPIO_RELEASED} -> {profile.BSL_GPIO_PRESSED} -> "
            f"{profile.BSL_GPIO_RELEASED} (released, pressed and held, released)."
        )
        yield BslUpdate(
            phase=0,
            prompt="BSL button OK",
            level=profile.BSL_GPIO_RELEASED,
            done=True,
            success=True,
            message=self.message,
        )

    def summary(self) -> str:
        """Per-phase verdicts and samples for bsl_button_test.log."""
        lines = [
            f"Polling GPIO {profile.BSL_GPIO_NAME} every "
            f"{self._interval_s:.2f} s in one console session.",
        ]
        for phase in self.phases:
            verdict = "PASS" if phase.passed else "FAIL"
            lines.append(
                f"Step {phase.phase} ({PHASE_NAMES[phase.phase]}): {verdict} - "
                f"{phase.detail} ({len(phase.samples)} sample(s))"
            )
            lines.append(f"  Samples: {run_lengths(phase.samples) or '-'}")
        for number in range(len(self.phases) + 1, PHASE_RELEASE + 1):
            lines.append(f"Step {number} ({PHASE_NAMES[number]}): NOT RUN")
        return "\n".join(lines)
