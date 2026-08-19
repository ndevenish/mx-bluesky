"""
Utilities for defining the detector in use, and moving the stage.
"""

from collections.abc import Generator
from enum import IntEnum
from math import isclose

import bluesky.plan_stubs as bps
from bluesky.utils import Msg, MsgGenerator
from dodal.common import inject
from dodal.devices.motors import YZStage

from mx_bluesky.beamlines.i24.serial.log import SSX_LOGGER
from mx_bluesky.beamlines.i24.serial.parameters import (
    SERIAL_DETECTORS,
    DetectorName,
    SSXType,
)
from mx_bluesky.beamlines.i24.serial.parameters.detector import SerialDetector
from mx_bluesky.beamlines.i24.serial.setup_beamline import pv
from mx_bluesky.beamlines.i24.serial.setup_beamline.ca import caget, caput

# How close the carriage has to be to a detector's parked position to count as having
# it in the beam already.
CARRIAGE_IN_POSITION_MM = 1.0

EXPT_TYPE_DETECTOR_PVS = {
    SSXType.FIXED: pv.ioc13_gp101,
    SSXType.EXTRUDER: pv.ioc13_gp15,
}


class DetRequest(IntEnum):
    eiger = 0
    # 1 is deliberately skipped: it meant the pilatus until it was removed in
    # 5eee6dc4e, and the extruder's request PV still holds it. Reusing the number
    # would turn a stale pilatus selection into a jungfrau collection, where leaving
    # it unclaimed keeps that a loud error.
    jungfrau = 2

    def __str__(self) -> str:
        return self.name


class UnknownDetectorTypeError(Exception):
    pass


def get_detector_type(detector_stage: YZStage) -> Generator[Msg, None, SerialDetector]:
    """Which detector the carriage currently has in the beam.

    Whichever detector is parked nearest to where the carriage is. The positions are
    hundreds of millimetres apart, so nearest is unambiguous, and unlike a threshold it
    does not need revisiting for each detector added. See
    https://github.com/DiamondLightSource/mx_bluesky/issues/51.
    """
    det_y = float((yield from bps.rd(detector_stage.y)))
    detector = min(
        SERIAL_DETECTORS.values(),
        key=lambda candidate: abs(candidate.det_y_target_mm - det_y),
    )
    SSX_LOGGER.info(f"{detector} detector in use, with the carriage at {det_y}.")
    return detector


def move_detector_into_beam_plan(
    detector_stage: YZStage, detector: SerialDetector
) -> MsgGenerator:
    """Put a detector in the beam, moving the carriage only if it is not there already.

    The carriage carries every detector, so which one is in the beam is a question of
    where it is parked. Asking for a collection on a detector is therefore also asking
    for it to be moved into the beam.
    """
    det_y = float((yield from bps.rd(detector_stage.y)))
    if isclose(det_y, detector.det_y_target_mm, abs_tol=CARRIAGE_IN_POSITION_MM):
        SSX_LOGGER.info(f"{detector} is already in the beam, at {det_y}.")
        return
    yield from _move_detector_stage(detector_stage, detector.det_y_target_mm)


def _move_detector_stage(detector_stage: YZStage, target: float) -> MsgGenerator:
    SSX_LOGGER.info(f"Moving detector stage to target position: {target}.")
    yield from bps.mv(detector_stage.y, target)


# Workaround in case the PV value has been set to the detector name
def _get_requested_detector(det_type_pv: str) -> str:
    """Get the requested detector name from the PV value.

    Args:
        det_type_pv (str): PV associated to the detector request. This is usually a \
            general purpose PV set up for the serial collection which could contain \
            a string or and int.

    Returns:
        str: The detector name as a string, currently "eiger".
    """
    det_type = caget(det_type_pv)
    if det_type in list(DetectorName):
        return det_type
    else:
        try:
            det_type = int(det_type)
            return str(DetRequest(det_type))
        except ValueError:
            raise


def setup_detector_stage(
    expt_type: SSXType, detector_stage: YZStage = inject("detector_motion")
) -> MsgGenerator:
    # Grab the correct PV depending on experiment
    # Its value is set with MUX on edm screen
    det_type_pv = EXPT_TYPE_DETECTOR_PVS[expt_type]
    requested_detector = _get_requested_detector(det_type_pv)
    SSX_LOGGER.info(f"Requested detector: {requested_detector}.")

    yield from move_detector_into_beam_plan(
        detector_stage, SERIAL_DETECTORS[DetectorName(requested_detector)]
    )
    caput(det_type_pv, requested_detector)
    SSX_LOGGER.info("Detector setup done.")
