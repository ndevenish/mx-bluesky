import pytest

from mx_bluesky.beamlines.i24.serial.parameters.constants import DetectorName
from mx_bluesky.beamlines.i24.serial.parameters.detector import (
    EIGER,
    JUNGFRAU,
    SERIAL_DETECTORS,
)


@pytest.mark.parametrize("detector", [EIGER, JUNGFRAU])
def test_detectors_are_looked_up_by_the_name_they_carry(detector):
    assert SERIAL_DETECTORS[detector.name] is detector
    assert str(detector) == detector.name.value


def test_eiger_image_size():
    assert EIGER.image_size_pixels == (3108, 3262)
    assert EIGER.image_size_mm == (233.1, 244.65)


def test_a_detector_is_found_for_every_name():
    assert set(SERIAL_DETECTORS) == set(DetectorName)


def test_eiger_pulse_is_a_hair_shorter_than_the_exposure():
    # It collects only while the signal is high, and stops on the falling edge.
    assert EIGER.zebra_pulse_width_s(0.01) == pytest.approx(0.0099)


def test_edge_triggered_detector_only_needs_a_clean_edge():
    assert JUNGFRAU.zebra_pulse_width_s(0.01) == pytest.approx(0.005)
