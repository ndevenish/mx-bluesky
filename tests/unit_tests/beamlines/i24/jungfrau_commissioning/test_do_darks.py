from unittest.mock import AsyncMock, MagicMock, patch

import bluesky.plan_stubs as bps
import bluesky.preprocessors as bpp
import pytest
from bluesky.callbacks import CallbackBase
from bluesky.run_engine import RunEngine
from dodal.beamlines import i24
from dodal.beamlines.i24 import JUNGFRAU_FILENAME
from dodal.devices.beamlines.i24.commissioning_jungfrau import (
    CommissioningJungfrauDetector,
)
from ophyd_async.core import completed_status
from ophyd_async.fastcs.jungfrau import (
    AcquisitionType,
    GainMode,
    PedestalMode,
)

from mx_bluesky.beamlines.i24.jungfrau_commissioning.experiment_plans.do_darks import (
    do_non_pedestal_darks,
    do_pedestal_darks,
)


class CheckMonitor(CallbackBase):
    """Store the order and values of updates to specified signals

    Usage: Instantiate this callback with list of signals to track, and subscribe the run_engine to this
    callback. Run your plan using Bluesky's monitor_during decorator or wrapper, specifing the same signals
    in the monitor.
    """

    def __init__(self, signals_to_track: list[str]):
        self.signals_and_values = {signal: [] for signal in signals_to_track}

    def event(self, doc):
        key, value = next(iter(doc["data"].items()))
        self.signals_and_values[key].append(value)
        return doc


def fake_complete(_, group=None):
    yield from bps.null()
    return completed_status()


@patch(
    "mx_bluesky.beamlines.i24.jungfrau_commissioning.plan_stubs.plan_utils.log_on_percentage_complete",
    new=MagicMock(),
)
@patch(
    "mx_bluesky.beamlines.i24.jungfrau_commissioning.plan_stubs.plan_utils.bps.complete",
    new=MagicMock(side_effect=fake_complete),
)
async def test_full_do_pedestal_darks(
    jungfrau: CommissioningJungfrauDetector,
    run_engine: RunEngine,
):
    # Test that plan succeeds in RunEngine and pedestal-specific signals are changed as expected

    @bpp.run_decorator()
    def test_plan():
        yield from bps.monitor(jungfrau.acquisition_type, name="AT")
        yield from bps.monitor(jungfrau.detector.pedestal_mode_state, name="PM")
        yield from bps.monitor(jungfrau.detector.gain_mode, name="GM")
        yield from do_pedestal_darks(0.001, 2, 2, jungfrau=jungfrau)

    jungfrau._acquire_logic.start_acquiring = AsyncMock()  # type: ignore
    assert await jungfrau.acquisition_type.get_value() == AcquisitionType.STANDARD
    await jungfrau.detector.gain_mode.set(GainMode.FIX_G2)
    await jungfrau.detector.pedestal_mode_state.set(PedestalMode.OFF)
    monitor_tracker = CheckMonitor(
        [
            "jungfrau-acquisition_type",
            "jungfrau-detector-pedestal_mode_state",
            "jungfrau-detector-gain_mode",
        ]
    )
    run_engine.subscribe(monitor_tracker)
    run_engine(test_plan())

    assert monitor_tracker.signals_and_values["jungfrau-acquisition_type"] == [
        AcquisitionType.STANDARD,
        AcquisitionType.PEDESTAL,
        AcquisitionType.STANDARD,
    ]
    assert monitor_tracker.signals_and_values[
        "jungfrau-detector-pedestal_mode_state"
    ] == [
        PedestalMode.OFF,  # Repeated as staging JF also turns pedestals off
        PedestalMode.OFF,
        PedestalMode.ON,
        PedestalMode.OFF,
    ]

    # When using the real detector, the switching of gain mode is a bit more complicated,
    # see the docstring for the do_pedestal_darks plan.
    assert monitor_tracker.signals_and_values["jungfrau-detector-gain_mode"] == [
        GainMode.FIX_G2,
        GainMode.DYNAMIC,
    ]


class FakeError(Exception): ...


async def test_jungfrau_unstage_on_error(
    jungfrau: CommissioningJungfrauDetector, run_engine: RunEngine
):
    jungfrau.stage = MagicMock(side_effect=FakeError)
    jungfrau.unstage = MagicMock(side_effect=lambda: completed_status())

    def test_plan():
        yield from do_pedestal_darks(0.001, 2, 2, jungfrau=jungfrau)

    with pytest.raises(FakeError):
        run_engine(test_plan())
    assert jungfrau.unstage.call_count == 1


@pytest.mark.parametrize(
    "plan, requested_name",
    [
        (
            lambda name, jf: do_pedestal_darks(filename=name, jungfrau=jf),
            "a_named_pedestal",
        ),
        (
            lambda name, jf: do_non_pedestal_darks(
                GainMode.DYNAMIC, filename=name, jungfrau=jf
            ),
            "a_named_dark",
        ),
    ],
)
@patch(
    "mx_bluesky.beamlines.i24.jungfrau_commissioning.experiment_plans.do_darks.fly_jungfrau",
    new=MagicMock(),
)
def test_the_requested_filename_reaches_the_jungfrau_filewriter(
    plan,
    requested_name: str,
    jungfrau: CommissioningJungfrauDetector,
    run_engine: RunEngine,
):
    """i24 writes jungfrau data without numtracker, so nothing else carries the name the
    plan was given as far as the filewriter."""
    JUNGFRAU_FILENAME.filename = "jungfrau"

    run_engine(plan(requested_name, jungfrau))

    assert i24.path_provider()().filename == requested_name
