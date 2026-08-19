from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import bluesky.plan_stubs as bps
import pytest
from dodal.beamlines import i24
from dodal.devices.attenuator.attenuator import ReadOnlyAttenuator
from dodal.devices.beamlines.i24.beam_center import DetectorBeamCenter
from dodal.devices.beamlines.i24.commissioning_jungfrau import (
    CommissioningJungfrauDetector,
)
from dodal.devices.zebra.zebra import Zebra
from ophyd_async.core import set_mock_value

from mx_bluesky.beamlines.i24.serial.detector_control import SerialDetectorControl
from mx_bluesky.beamlines.i24.serial.fixed_target.ft_utils import ChipType
from mx_bluesky.beamlines.i24.serial.parameters import (
    ExtruderParameters,
    FixedTargetParameters,
    get_chip_format,
)
from mx_bluesky.beamlines.i24.serial.parameters.constants import DetectorName
from mx_bluesky.beamlines.i24.serial.parameters.detector import EIGER

TEST_PATH = Path("tests/test_data/test_daq_configuration")

TEST_LUT = {
    DetectorName.EIGER: TEST_PATH / "lookup/test_det_dist_converter.txt",
}


def fake_generator(value):
    yield from bps.null()
    return value


@pytest.fixture
def jungfrau() -> CommissioningJungfrauDetector:
    return i24.jungfrau.build(connect_immediately=True, mock=True)


@pytest.fixture
def detector_control():
    """A stand in for however a collection drives whichever detector is in the beam.

    Every method on the interface is a plan, so each mock has to hand back a fresh
    generator every time it is called. What the Eiger's implementation does with the
    hardware is tested in test_detector_control.
    """
    control = MagicMock(spec=SerialDetectorControl)
    # Set on the instance rather than the class, so spec does not pick it up.
    control.detector = EIGER
    plan_results = {
        "start_new_file_series": None,
        "setup_for_collection": None,
        "collection_filename": "chip_01",
        "start_acquisition": None,
        "wait_for_completion": None,
        # None means "this detector cannot say", so the frame count check keeps quiet
        # unless a test asks it to report one.
        "frames_captured": None,
        "stop_acquisition": None,
        "abort_acquisition": None,
        "return_to_normal": None,
        "write_nexus_metadata": None,
    }
    for method, result in plan_results.items():
        getattr(control, method).side_effect = lambda *args, _result=result, **kwargs: (
            fake_generator(_result)
        )
    return control


@pytest.fixture
def dummy_params_without_pp():
    oxford_defaults = get_chip_format(ChipType.Oxford)
    params = {
        "visit": "/tmp/dls/i24/fixed/foo",
        "directory": "bar",
        "filename": "chip",
        "exposure_time_s": 0.01,
        "detector_distance_mm": 100,
        "detector_name": "eiger",
        "transmission": 1.0,
        "num_exposures": 1,
        "chip": oxford_defaults.model_dump(),
        "map_type": 1,
        "pump_repeat": 0,
        "checker_pattern": False,
        "chip_map": [1],
    }
    return FixedTargetParameters(**params)  # type: ignore


@pytest.fixture
def dummy_params_ex():
    params = {
        "visit": "/tmp/dls/i24/extruder/foo",
        "directory": "bar",
        "filename": "protein",
        "exposure_time_s": 0.1,
        "detector_distance_mm": 100,
        "detector_name": "eiger",
        "transmission": 1.0,
        "num_images": 10,
        "pump_status": False,
    }
    return ExtruderParameters(**params)  # type: ignore


@pytest.fixture
def zebra() -> Zebra:
    return i24.zebra.build(connect_immediately=True, mock=True)


@pytest.fixture
def eiger_beam_center() -> DetectorBeamCenter:
    bc: DetectorBeamCenter = i24.eiger_beam_center.build(
        connect_immediately=True, mock=True
    )
    set_mock_value(bc.beam_x, 1605)
    set_mock_value(bc.beam_y, 1702)
    return bc


@pytest.fixture
def attenuator() -> ReadOnlyAttenuator:
    attenuator: ReadOnlyAttenuator = i24.attenuator.build(
        connect_immediately=True, mock=True
    )
    set_mock_value(attenuator.actual_transmission, 1.0)
    return attenuator
