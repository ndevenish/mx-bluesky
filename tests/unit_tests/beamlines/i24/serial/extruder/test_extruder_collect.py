from pathlib import Path
from unittest.mock import ANY, MagicMock, call, patch

import pytest
from dodal.beamlines.i24 import I24_ZEBRA_MAPPING
from dodal.devices.zebra.zebra import ArmDemand, Zebra
from ophyd.sim import NullStatus
from ophyd_async.core import get_mock_put, init_devices, set_mock_value

from mx_bluesky.beamlines.i24.serial.detector_control import AcquisitionMode
from mx_bluesky.beamlines.i24.serial.extruder.i24ssx_extruder_collect_py3v2 import (
    collection_aborted_plan,
    collection_complete_plan,
    enter_hutch,
    initialise_extruder,
    laser_check,
    main_extruder_plan,
    read_parameters,
    run_extruder_plan,
    tidy_up_at_collection_end_plan,
)
from mx_bluesky.beamlines.i24.serial.parameters import BeamSettings, ExtruderParameters
from mx_bluesky.beamlines.i24.serial.parameters.constants import DetectorName
from mx_bluesky.beamlines.i24.serial.parameters.detector import EIGER

from ..conftest import TEST_LUT, fake_generator


@pytest.fixture
def zebra():
    with init_devices(mock=True):
        i24_zebra = Zebra(
            prefix="",
            mapping=I24_ZEBRA_MAPPING,
        )

    def mock_side(demand: ArmDemand):
        set_mock_value(i24_zebra.pc.arm.armed, demand.value)
        return NullStatus()

    i24_zebra.pc.arm.set = MagicMock(side_effect=mock_side)
    return i24_zebra


@pytest.fixture
def dummy_params():
    params = ExtruderParameters(
        visit=Path("/tmp/dls/i24/extruder/foo"),
        directory="bar",
        filename="protein",
        exposure_time_s=0.1,
        detector_distance_mm=100,
        detector_name=DetectorName("eiger"),
        transmission=1.0,
        num_images=10,
        pump_status=False,
    )
    return params


@pytest.fixture
def dummy_params_with_pump():
    params = ExtruderParameters(
        visit=Path("/tmp/dls/i24/extruder/foo"),
        directory="bar",
        filename="protein",
        exposure_time_s=0.1,
        detector_distance_mm=100,
        detector_name=DetectorName("eiger"),
        transmission=1.0,
        num_images=10,
        pump_status=True,
        laser_dwell_s=0.01,
        laser_delay_s=0.005,
    )
    return params


@pytest.fixture
def dummy_beam_settings():
    return BeamSettings(
        wavelength_in_a=0.6, beam_size_in_um=(7, 7), beam_center_in_mm=(120.4, 127.6)
    )


@patch(
    "mx_bluesky.beamlines.i24.serial.extruder.i24ssx_extruder_collect_py3v2.get_detector_type"
)
@patch("mx_bluesky.beamlines.i24.serial.extruder.i24ssx_extruder_collect_py3v2.caget")
@patch(
    "mx_bluesky.beamlines.i24.serial.extruder.i24ssx_extruder_collect_py3v2._read_visit_directory_from_file"
)
@patch(
    "mx_bluesky.beamlines.i24.serial.extruder.i24ssx_extruder_collect_py3v2.SSX_LOGGER"
)
@patch("mx_bluesky.beamlines.i24.serial.extruder.i24ssx_extruder_collect_py3v2.bps.rd")
def test_read_parameters(
    fake_rd,
    fake_log,
    mock_read_visit,
    fake_caget,
    fake_det,
    detector_stage,
    run_engine,
):
    mock_attenuator = MagicMock()
    fake_det.side_effect = [fake_generator(EIGER)]
    fake_rd.side_effect = [fake_generator(0.3)]
    with patch(
        "mx_bluesky.beamlines.i24.serial.extruder.i24ssx_extruder_collect_py3v2.ExtruderParameters",
    ):
        run_engine(read_parameters(detector_stage, mock_attenuator))

    assert fake_caget.call_count == 8
    fake_log.warning.assert_called_once()
    assert fake_log.info.call_count == 3


@patch("mx_bluesky.beamlines.i24.serial.extruder.i24ssx_extruder_collect_py3v2.caget")
@patch("mx_bluesky.beamlines.i24.serial.extruder.i24ssx_extruder_collect_py3v2.caput")
@patch(
    "mx_bluesky.beamlines.i24.serial.extruder.i24ssx_extruder_collect_py3v2.get_detector_type"
)
@patch(
    "mx_bluesky.beamlines.i24.serial.extruder.i24ssx_extruder_collect_py3v2.SSX_LOGGER"
)
def test_initialise_extruder(
    fake_log,
    fake_det,
    fake_caput,
    fake_caget,
    detector_stage,
    run_engine,
):
    fake_caget.return_value = "/path/to/visit"
    fake_det.side_effect = [fake_generator(EIGER)]
    run_engine(initialise_extruder(detector_stage))
    assert fake_caput.call_count == 9
    assert fake_caget.call_count == 1


async def test_enterhutch(detector_stage, run_engine):
    run_engine(enter_hutch(detector_stage))
    assert await detector_stage.z.user_setpoint.get_value() == 1480


@pytest.mark.parametrize(
    "laser_mode, det_type, expected_in1, expected_out",
    [
        ("laseron", EIGER, "Yes", I24_ZEBRA_MAPPING.sources.SOFT_IN3),
        ("laseroff", EIGER, "No", I24_ZEBRA_MAPPING.sources.DISCONNECT),
    ],
)
@patch(
    "mx_bluesky.beamlines.i24.serial.extruder.i24ssx_extruder_collect_py3v2.get_detector_type"
)
async def test_laser_check(
    fake_det,
    laser_mode,
    expected_in1,
    expected_out,
    det_type,
    zebra,
    detector_stage,
    run_engine,
):
    fake_det.side_effect = [fake_generator(det_type)]
    run_engine(laser_check(laser_mode, zebra, detector_stage))

    ttl = I24_ZEBRA_MAPPING.outputs.TTL_JUNGFRAU

    assert await zebra.inputs.soft_in_1.get_value() == expected_in1
    assert await zebra.output.out_pvs[ttl].get_value() == expected_out


@patch(
    "mx_bluesky.beamlines.i24.serial.extruder.i24ssx_extruder_collect_py3v2.bps.sleep"
)
@patch("mx_bluesky.beamlines.i24.serial.extruder.i24ssx_extruder_collect_py3v2.DCID")
@patch("mx_bluesky.beamlines.i24.serial.extruder.i24ssx_extruder_collect_py3v2.caput")
@patch("mx_bluesky.beamlines.i24.serial.extruder.i24ssx_extruder_collect_py3v2.caget")
@patch("mx_bluesky.beamlines.i24.serial.extruder.i24ssx_extruder_collect_py3v2.sup")
@patch(
    "mx_bluesky.beamlines.i24.serial.extruder.i24ssx_extruder_collect_py3v2.setup_zebra_for_quickshot_plan"
)
@patch("mx_bluesky.beamlines.i24.serial.extruder.i24ssx_extruder_collect_py3v2.bps.rd")
@patch(
    "mx_bluesky.beamlines.i24.serial.extruder.i24ssx_extruder_collect_py3v2.read_beam_info_from_hardware"
)
def test_run_extruder_quickshot(
    mock_read_beam_info,
    fake_read,
    mock_quickshot_plan,
    fake_sup,
    fake_caget,
    fake_caput,
    fake_dcid,
    fake_sleep,
    detector_control,
    run_engine,
    zebra,
    shutter,
    aperture,
    backlight,
    beamstop,
    detector_stage,
    dcm,
    mirrors,
    eiger_beam_center,
    dummy_params,
    dummy_beam_settings,
):
    fake_start_time = MagicMock()
    mock_read_beam_info.side_effect = [fake_generator(dummy_beam_settings)]
    # Mock end of data collection (zebra disarmed)
    fake_read.side_effect = [
        fake_generator(1605),  # beam center
        fake_generator(1702),
        fake_generator(0),  # zebra disarm
    ]
    detector_control.collection_filename.side_effect = lambda: fake_generator(
        "filename"
    )
    with patch(
        "mx_bluesky.beamlines.i24.serial.extruder.i24ssx_extruder_collect_py3v2.BEAM_CENTER_LUT_FILES",
        new=TEST_LUT,
    ):
        run_engine(
            main_extruder_plan(
                zebra,
                aperture,
                backlight,
                beamstop,
                detector_stage,
                shutter,
                dcm,
                mirrors,
                eiger_beam_center,
                dummy_params,
                detector_control,
                fake_dcid,
                fake_start_time,
            )
        )
    # A static extruder collection arms the detector and sends it one trigger, then
    # lets it run through the series on its own timing.
    detector_control.setup_for_collection.assert_called_once_with(
        dummy_params.collection_directory.as_posix(),
        dummy_params.filename,
        dummy_params.num_images,
        dummy_params.exposure_time_s,
        AcquisitionMode.SOFTWARE,
    )
    detector_control.start_new_file_series.assert_called_once()
    detector_control.start_acquisition.assert_called_once()
    detector_control.write_nexus_metadata.assert_called_once_with(
        None, dummy_params, 0.6, (1605, 1702), fake_start_time
    )
    assert fake_dcid.generate_dcid.call_count == 1
    assert fake_dcid.notify_start.call_count == 1
    assert fake_sup.setup_beamline_for_collection_plan.call_count == 1
    mock_quickshot_plan.assert_called_once()
    mock_read_beam_info.assert_called_once()


@patch(
    "mx_bluesky.beamlines.i24.serial.extruder.i24ssx_extruder_collect_py3v2.bps.sleep"
)
@patch("mx_bluesky.beamlines.i24.serial.extruder.i24ssx_extruder_collect_py3v2.DCID")
@patch("mx_bluesky.beamlines.i24.serial.extruder.i24ssx_extruder_collect_py3v2.caput")
@patch("mx_bluesky.beamlines.i24.serial.extruder.i24ssx_extruder_collect_py3v2.caget")
@patch("mx_bluesky.beamlines.i24.serial.extruder.i24ssx_extruder_collect_py3v2.sup")
@patch(
    "mx_bluesky.beamlines.i24.serial.extruder.i24ssx_extruder_collect_py3v2.setup_zebra_for_extruder_with_pump_probe_plan"
)
@patch("mx_bluesky.beamlines.i24.serial.extruder.i24ssx_extruder_collect_py3v2.bps.rd")
@patch(
    "mx_bluesky.beamlines.i24.serial.extruder.i24ssx_extruder_collect_py3v2.read_beam_info_from_hardware"
)
def test_run_extruder_pump_probe_triggers_image_by_image(
    mock_read_beam_info,
    fake_read,
    mock_pump_probe_plan,
    fake_sup,
    fake_caget,
    fake_caput,
    fake_dcid,
    fake_sleep,
    detector_control,
    run_engine,
    zebra,
    shutter,
    aperture,
    backlight,
    beamstop,
    detector_stage,
    dcm,
    mirrors,
    eiger_beam_center,
    dummy_params_with_pump,
    dummy_beam_settings,
):
    mock_read_beam_info.side_effect = [fake_generator(dummy_beam_settings)]
    fake_read.side_effect = [
        fake_generator(1605),  # beam center
        fake_generator(1702),
        fake_generator(0),  # zebra disarm
    ]
    with patch(
        "mx_bluesky.beamlines.i24.serial.extruder.i24ssx_extruder_collect_py3v2.BEAM_CENTER_LUT_FILES",
        new=TEST_LUT,
    ):
        run_engine(
            main_extruder_plan(
                zebra,
                aperture,
                backlight,
                beamstop,
                detector_stage,
                shutter,
                dcm,
                mirrors,
                eiger_beam_center,
                dummy_params_with_pump,
                detector_control,
                fake_dcid,
                MagicMock(),
            )
        )
    # Pump probe gates every image off the zebra, unlike a static collection.
    detector_control.setup_for_collection.assert_called_once_with(
        dummy_params_with_pump.collection_directory.as_posix(),
        dummy_params_with_pump.filename,
        dummy_params_with_pump.num_images,
        dummy_params_with_pump.exposure_time_s,
        AcquisitionMode.HARDWARE,
    )
    mock_pump_probe_plan.assert_called_once()


@patch("mx_bluesky.beamlines.i24.serial.extruder.i24ssx_extruder_collect_py3v2.DCID")
@patch(
    "mx_bluesky.beamlines.i24.serial.extruder.i24ssx_extruder_collect_py3v2.disarm_zebra"
)
def test_collection_aborted_plan(
    mock_disarm, fake_dcid, zebra, detector_control, run_engine
):
    run_engine(collection_aborted_plan(zebra, detector_control, fake_dcid))

    mock_disarm.assert_called_once()
    detector_control.abort_acquisition.assert_called_once()
    fake_dcid.collection_complete.assert_called_once_with(ANY, aborted=True)


@patch(
    "mx_bluesky.beamlines.i24.serial.extruder.i24ssx_extruder_collect_py3v2.reset_zebra_when_collection_done_plan"
)
@patch("mx_bluesky.beamlines.i24.serial.extruder.i24ssx_extruder_collect_py3v2.DCID")
@patch("mx_bluesky.beamlines.i24.serial.extruder.i24ssx_extruder_collect_py3v2.caput")
@patch("mx_bluesky.beamlines.i24.serial.extruder.i24ssx_extruder_collect_py3v2.caget")
@patch("mx_bluesky.beamlines.i24.serial.extruder.i24ssx_extruder_collect_py3v2.sup")
def test_tidy_up_at_collection_end_plan(
    fake_sup,
    fake_caget,
    fake_caput,
    fake_dcid,
    mock_reset_zebra_plan,
    detector_control,
    run_engine,
    zebra,
    shutter,
    detector_stage,
    dummy_params,
    dcm,
):
    run_engine(
        tidy_up_at_collection_end_plan(
            zebra, shutter, dummy_params, detector_control, fake_dcid
        )
    )

    mock_reset_zebra_plan.assert_called_once()
    mock_shutter = get_mock_put(shutter.control)
    mock_shutter.assert_has_calls([call("Close")])

    assert fake_dcid.notify_end.call_count == 1

    detector_control.return_to_normal.assert_called_once()


@patch("mx_bluesky.beamlines.i24.serial.extruder.i24ssx_extruder_collect_py3v2.DCID")
@patch(
    "mx_bluesky.beamlines.i24.serial.extruder.i24ssx_extruder_collect_py3v2.bps.sleep"
)
@patch("mx_bluesky.beamlines.i24.serial.extruder.i24ssx_extruder_collect_py3v2.caput")
def test_collection_complete_plan(
    fake_caput, fake_sleep, fake_dcid, dummy_params, detector_control, run_engine
):
    run_engine(
        collection_complete_plan(
            dummy_params.collection_directory, detector_control, fake_dcid
        )
    )

    detector_control.stop_acquisition.assert_called_once()

    fake_dcid.collection_complete.assert_called_once_with(ANY, aborted=False)


@patch(
    "mx_bluesky.beamlines.i24.serial.extruder.i24ssx_extruder_collect_py3v2.Path.mkdir"
)
@patch(
    "mx_bluesky.beamlines.i24.serial.extruder.i24ssx_extruder_collect_py3v2.read_parameters"
)
def test_setup_tasks_in_run_extruder_plan(
    fake_read,
    fake_mkdir,
    zebra,
    aperture,
    backlight,
    beamstop,
    detector_stage,
    shutter,
    dcm,
    mirrors,
    attenuator,
    eiger_beam_center,
    run_engine,
    dummy_params,
):
    fake_read.side_effect = [fake_generator(dummy_params)]
    with patch(
        "mx_bluesky.beamlines.i24.serial.extruder.i24ssx_extruder_collect_py3v2.bpp.contingency_wrapper"
    ):
        run_engine(
            run_extruder_plan(
                zebra,
                aperture,
                backlight,
                beamstop,
                detector_stage,
                shutter,
                dcm,
                mirrors,
                attenuator,
                eiger_beam_center,
            )
        )
        fake_mkdir.assert_called_once()
