from datetime import datetime
from unittest.mock import ANY, MagicMock, call, patch

import pytest
from dodal.beamlines.i24 import JUNGFRAU_FILENAME
from ophyd_async.core import DetectorTrigger, set_mock_value

from mx_bluesky.beamlines.i24.serial.detector_control import (
    AcquisitionMode,
    EigerControl,
    JungfrauControl,
    check_all_frames_written,
    get_detector_control,
)
from mx_bluesky.beamlines.i24.serial.parameters.constants import DetectorName
from mx_bluesky.beamlines.i24.serial.parameters.detector import EIGER, JUNGFRAU
from mx_bluesky.beamlines.i24.serial.setup_beamline.setup_detector import (
    UnknownDetectorTypeError,
)

from .conftest import fake_generator


@pytest.fixture
def eiger_control(dcm, detector_stage):
    return EigerControl(dcm, detector_stage)


def test_get_detector_control_for_eiger(dcm, detector_stage):
    control = get_detector_control(DetectorName.EIGER, dcm, detector_stage)
    assert isinstance(control, EigerControl)
    assert control.detector is EIGER


def test_get_detector_control_rejects_a_detector_serial_cannot_run_on(
    dcm, detector_stage
):
    with pytest.raises(UnknownDetectorTypeError):
        get_detector_control(DetectorName.JUNGFRAU, dcm, detector_stage)


@patch("mx_bluesky.beamlines.i24.serial.detector_control.sup")
def test_eiger_setup_for_collection_passes_the_mode_through(
    fake_sup, eiger_control, dcm, detector_stage, run_engine
):
    fake_sup.eiger.side_effect = lambda *args, **kwargs: fake_generator(0)

    run_engine(
        eiger_control.setup_for_collection(
            "/some/path", "chip", 800, 0.01, AcquisitionMode.HARDWARE
        )
    )

    fake_sup.eiger.assert_called_once_with(
        "triggered", ["/some/path", "chip", 800, 0.01], dcm, detector_stage
    )


@patch("mx_bluesky.beamlines.i24.serial.detector_control.sup")
def test_eiger_return_to_normal(
    fake_sup, eiger_control, dcm, detector_stage, run_engine
):
    fake_sup.eiger.side_effect = lambda *args, **kwargs: fake_generator(0)

    run_engine(eiger_control.return_to_normal())

    fake_sup.eiger.assert_called_once_with(
        "return-to-normal", None, dcm, detector_stage
    )


@patch("mx_bluesky.beamlines.i24.serial.detector_control.cagetstring")
def test_eiger_collection_filename_is_the_one_odin_will_write(
    fake_cagetstring, eiger_control, run_engine
):
    # The Eiger appends its own sequence id, so the plans have to ask for the name
    # rather than assume the one they requested.
    fake_cagetstring.return_value = "chip_0001"

    result = run_engine(eiger_control.collection_filename())

    assert result.plan_result == "chip_0001"
    fake_cagetstring.assert_called_once()


@patch("mx_bluesky.beamlines.i24.serial.detector_control.bps.sleep")
@patch("mx_bluesky.beamlines.i24.serial.detector_control.caput")
def test_eiger_stop_acquisition_stops_the_detector_and_closes_the_file(
    fake_caput, fake_sleep, eiger_control, run_engine
):
    fake_sleep.side_effect = lambda *args, **kwargs: fake_generator(None)

    run_engine(eiger_control.stop_acquisition())

    fake_caput.assert_has_calls([call(ANY, 0), call(ANY, "Done")])


@patch("mx_bluesky.beamlines.i24.serial.detector_control.bps.sleep")
@patch("mx_bluesky.beamlines.i24.serial.detector_control.caput")
def test_eiger_abort_acquisition_leaves_odin_alone(
    fake_caput, fake_sleep, eiger_control, run_engine
):
    # Aborting has only ever stopped the detector, not closed Odin's capture.
    fake_sleep.side_effect = lambda *args, **kwargs: fake_generator(None)

    run_engine(eiger_control.abort_acquisition())

    fake_caput.assert_called_once_with(ANY, 0)


@patch("mx_bluesky.beamlines.i24.serial.detector_control.caget")
@patch("mx_bluesky.beamlines.i24.serial.detector_control.caput")
def test_eiger_start_new_file_series_advances_the_sequence_id(
    fake_caput, fake_caget, eiger_control, run_engine
):
    fake_caget.return_value = "7"

    run_engine(eiger_control.start_new_file_series())

    fake_caput.assert_called_once_with(ANY, 8)


@patch("mx_bluesky.beamlines.i24.serial.detector_control.caput")
def test_eiger_start_acquisition_sends_the_manual_trigger(
    fake_caput, eiger_control, run_engine
):
    run_engine(eiger_control.start_acquisition())

    fake_caput.assert_called_once_with(ANY, 1)


@patch("mx_bluesky.beamlines.i24.serial.detector_control.caget_once")
def test_eiger_frames_captured_reads_back_from_odin(
    fake_caget, eiger_control, run_engine
):
    fake_caget.return_value = "800"

    result = run_engine(eiger_control.frames_captured())

    assert result.plan_result == 800


@patch("mx_bluesky.beamlines.i24.serial.detector_control.caget_once")
def test_eiger_frames_captured_gives_up_rather_than_blocking_a_finished_collection(
    fake_caget, eiger_control, run_engine
):
    fake_caget.return_value = None

    result = run_engine(eiger_control.frames_captured())

    assert result.plan_result is None


@patch("mx_bluesky.beamlines.i24.serial.detector_control.SSX_LOGGER")
def test_check_all_frames_written_warns_about_lost_frames(fake_log, run_engine):
    control = MagicMock()
    control.frames_captured.side_effect = lambda: fake_generator(798)

    run_engine(check_all_frames_written(control, 800))

    fake_log.warning.assert_called_once()
    assert "2 frames appear to have been lost" in fake_log.warning.call_args.args[0]


@patch("mx_bluesky.beamlines.i24.serial.detector_control.SSX_LOGGER")
def test_check_all_frames_written_is_quiet_when_everything_landed(fake_log, run_engine):
    control = MagicMock()
    control.frames_captured.side_effect = lambda: fake_generator(800)

    run_engine(check_all_frames_written(control, 800))

    fake_log.warning.assert_not_called()


@patch("mx_bluesky.beamlines.i24.serial.detector_control.SSX_LOGGER")
def test_check_all_frames_written_skips_a_detector_that_cannot_count(
    fake_log, run_engine
):
    control = MagicMock()
    control.frames_captured.side_effect = lambda: fake_generator(None)

    run_engine(check_all_frames_written(control, 800))

    fake_log.warning.assert_not_called()
    fake_log.info.assert_not_called()


@pytest.fixture
def jungfrau_control(jungfrau):
    return JungfrauControl(jungfrau)


def test_get_detector_control_for_jungfrau(dcm, detector_stage, jungfrau):
    control = get_detector_control(DetectorName.JUNGFRAU, dcm, detector_stage, jungfrau)
    assert isinstance(control, JungfrauControl)
    assert control.detector is JUNGFRAU


def test_get_detector_control_for_jungfrau_needs_the_device(dcm, detector_stage):
    with pytest.raises(UnknownDetectorTypeError):
        get_detector_control(DetectorName.JUNGFRAU, dcm, detector_stage)


@patch("mx_bluesky.beamlines.i24.serial.detector_control.bps.prepare")
@patch("mx_bluesky.beamlines.i24.serial.detector_control.bps.kickoff")
def test_jungfrau_hardware_triggering_listens_for_edges_from_setup(
    fake_kickoff, fake_prepare, jungfrau_control, run_engine
):
    # A fixed target collection has no separate go signal, so the detector has to be
    # kicked off while it is being set up.
    fake_prepare.side_effect = lambda *args, **kwargs: fake_generator(None)
    fake_kickoff.side_effect = lambda *args, **kwargs: fake_generator(None)

    run_engine(
        jungfrau_control.setup_for_collection(
            "/some/path", "chip", 800, 0.01, AcquisitionMode.HARDWARE
        )
    )
    fake_kickoff.assert_called_once()

    trigger_info = fake_prepare.call_args.args[1]
    assert trigger_info.trigger is DetectorTrigger.EXTERNAL_EDGE
    assert trigger_info.number_of_events == 800
    assert trigger_info.livetime == 0.01

    # ...and not again when the extruder sends its go signal
    run_engine(jungfrau_control.start_acquisition())
    fake_kickoff.assert_called_once()


@patch("mx_bluesky.beamlines.i24.serial.detector_control.bps.prepare")
@patch("mx_bluesky.beamlines.i24.serial.detector_control.bps.kickoff")
def test_jungfrau_software_triggering_starts_when_the_shutter_is_open(
    fake_kickoff, fake_prepare, jungfrau_control, run_engine
):
    # Internally triggered, so the detector runs from the moment it is kicked off.
    # That has to be when the collection starts, not when it is set up.
    fake_prepare.side_effect = lambda *args, **kwargs: fake_generator(None)
    fake_kickoff.side_effect = lambda *args, **kwargs: fake_generator(None)

    run_engine(
        jungfrau_control.setup_for_collection(
            "/some/path", "protein", 10, 0.1, AcquisitionMode.SOFTWARE
        )
    )
    fake_kickoff.assert_not_called()

    trigger_info = fake_prepare.call_args.args[1]
    assert trigger_info.trigger is DetectorTrigger.INTERNAL
    assert trigger_info.collections_per_event == 10

    run_engine(jungfrau_control.start_acquisition())
    fake_kickoff.assert_called_once()


@patch("mx_bluesky.beamlines.i24.serial.detector_control.bps.prepare")
def test_jungfrau_collection_filename_is_the_one_it_was_asked_for(
    fake_prepare, jungfrau_control, run_engine
):
    fake_prepare.side_effect = lambda *args, **kwargs: fake_generator(None)

    run_engine(
        jungfrau_control.setup_for_collection(
            "/some/path", "protein", 10, 0.1, AcquisitionMode.SOFTWARE
        )
    )
    result = run_engine(jungfrau_control.collection_filename())

    assert result.plan_result == "protein"
    assert JUNGFRAU_FILENAME.filename == "protein"


async def test_jungfrau_frames_captured_reads_the_writer(
    jungfrau_control, jungfrau, run_engine
):
    set_mock_value(jungfrau.writer.frame_counter, 798)

    result = run_engine(jungfrau_control.frames_captured())

    assert result.plan_result == 798


@patch("mx_bluesky.beamlines.i24.serial.detector_control.bps.unstage")
def test_jungfrau_is_stopped_at_the_end_of_a_collection(
    fake_unstage, jungfrau_control, run_engine
):
    fake_unstage.side_effect = lambda *args, **kwargs: fake_generator(None)

    run_engine(jungfrau_control.stop_acquisition())
    run_engine(jungfrau_control.abort_acquisition())

    assert fake_unstage.call_count == 2


@patch("mx_bluesky.beamlines.i24.serial.detector_control.SSX_LOGGER")
def test_jungfrau_says_no_nexus_file_is_written(
    fake_log, jungfrau_control, dummy_params_without_pp, run_engine
):
    run_engine(
        jungfrau_control.write_nexus_metadata(
            None, dummy_params_without_pp, 0.6, (1605, 1702), datetime.now()
        )
    )

    fake_log.warning.assert_called_once()
