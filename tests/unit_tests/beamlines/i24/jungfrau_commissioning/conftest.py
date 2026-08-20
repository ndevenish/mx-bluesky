from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import bluesky.plan_stubs as bps
import pytest
from dodal.beamlines import i24
from dodal.beamlines.i24 import VerticalGoniometer
from dodal.devices.attenuator.attenuator import EnumFilterAttenuator
from dodal.devices.beamlines.i24.aperture import Aperture
from dodal.devices.beamlines.i24.beamstop import Beamstop
from dodal.devices.beamlines.i24.commissioning_jungfrau import (
    CommissioningJungfrauDetector,
)
from dodal.devices.beamlines.i24.dcm import DCM
from dodal.devices.beamlines.i24.dual_backlight import DualBacklight
from dodal.devices.beamlines.i24.focus_mirrors import FocusMirrorsMode
from dodal.devices.hutch_shutter import InterlockedHutchShutter
from dodal.devices.interlocks import PSSInterlock
from dodal.devices.motors import YZStage
from dodal.devices.robot import BartRobot
from dodal.devices.synchrotron import Synchrotron
from dodal.devices.zebra.zebra import Zebra
from dodal.devices.zebra.zebra_controlled_shutter import MXZebraShutter
from ophyd_async.core import init_devices

from mx_bluesky.beamlines.i24.jungfrau_commissioning.experiment_plans.rotation_scan_plan import (
    RotationScanComposite,
)

TEST_DCID = 4567780

_PLAN = "mx_bluesky.beamlines.i24.jungfrau_commissioning.experiment_plans.rotation_scan_plan"


@pytest.fixture(autouse=True)
def ispyb_deposition():
    """The ISPyB deposition the rotation plan makes, stubbed out.

    Autouse, so that a test which runs the plan cannot post to a real dcserver by
    forgetting to say otherwise. Tests of the deposition itself call the stubs directly
    rather than through the plan.
    """
    created = MagicMock(return_value=TEST_DCID)
    completed = MagicMock()

    def _create(*args, **kwargs):
        yield from bps.null()
        return created(*args, **kwargs)

    def _complete(*args, **kwargs):
        completed(*args, **kwargs)
        yield from bps.null()

    with (
        patch(f"{_PLAN}.create_rotation_data_collection", _create),
        patch(f"{_PLAN}.complete_rotation_data_collection", _complete),
    ):
        yield SimpleNamespace(create=created, complete=completed)


@pytest.fixture
def zebra() -> Zebra:
    """Override the shared fixture, which builds i03's zebra.

    These are i24 plans, and the two beamlines wire their zebra outputs differently:
    i24 maps TTL_JUNGFRAU where i03 maps TTL_DETECTOR. Testing against i03's mapping
    hides exactly the mismatch that UnmappedZebraError exists to catch.
    """
    return i24.zebra.build(connect_immediately=True, mock=True)


@pytest.fixture
def rotation_composite(
    jungfrau: CommissioningJungfrauDetector,
    zebra: Zebra,
    enum_attenuator: EnumFilterAttenuator,
    mirrors: FocusMirrorsMode,
) -> RotationScanComposite:
    with init_devices(mock=True):
        aperture = Aperture("")
        vgonio = VerticalGoniometer("")
        synchrotron = Synchrotron("")
        sample_shutter = MXZebraShutter("")
        shutter = InterlockedHutchShutter("", PSSInterlock(""))
        beamstop = Beamstop("")
        detector_motion = YZStage("")
        backlight = DualBacklight("")
        dcm = DCM("", "")
        robot = BartRobot("")

    # By keyword: the field names must match the i24 dodal device names, since that is
    # how blueapi resolves them, so a rename here is a real change rather than a shuffle.
    composite = RotationScanComposite(
        aperture=aperture,
        attenuator=enum_attenuator,
        jungfrau=jungfrau,
        vgonio=vgonio,
        synchrotron=synchrotron,
        sample_shutter=sample_shutter,
        zebra=zebra,
        shutter=shutter,
        beamstop=beamstop,
        detector_motion=detector_motion,
        backlight=backlight,
        dcm=dcm,
        focus_mirrors=mirrors,
        robot=robot,
    )

    return composite


@pytest.fixture(autouse=True)
def always_use_i24_beamline(use_i24_beamline): ...
