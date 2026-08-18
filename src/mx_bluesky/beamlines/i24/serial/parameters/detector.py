"""The detectors a serial collection can run on, and the static facts about each.

Some of these are properties of the detector itself - its pixel size, the id ISPyB knows
it by - and some are properties of how it is wired into I24, such as which zebra output
triggers it and where the detector stage parks to put it in the beam. Both kinds live
here rather than on the dodal device, because the wiring ones are not facts about the
detector at all, and splitting the table across two homes would make neither readable.

Anything that needs to talk to hardware belongs on the device, not here.
"""

from dataclasses import dataclass
from pathlib import Path

from dodal.devices.detector.det_dim_constants import (
    EIGER2_X_9M_SIZE,
    JUNGFRAU_9M_SIZE,
    DetectorSizeConstants,
)

from mx_bluesky.beamlines.i24.beam_center import (
    JUNGFRAU_BEAM_CENTER_LUT,
    LUT_FILES_PATH,
)
from mx_bluesky.beamlines.i24.serial.parameters.constants import DetectorName


@dataclass(frozen=True)
class SerialDetector:
    """A detector a serial collection can be run on.

    Attributes:
        name: How this detector is named in the parameter models and the GUIs.
        ispyb_id: The detectorId ISPyB records collections against.
        pixel_size_mm: Size of one pixel, (x, y).
        size_constants: The detector's dimensions, in mm and in pixels.
        beam_centre_lut: Lookup table of beam centre against detector distance. These
            are built per detector from processed data, so a newly commissioned detector
            has no table until it has been collected on.
        det_y_target_mm: Where the detector stage y parks to put this detector in the
            beam.
        zebra_ttl_out: Which zebra TTL output triggers this detector.
        zebra_pulse_width_drop_s: How much shorter than the exposure time the zebra's
            pulse should be. The Eiger collects only while the signal is high and stops
            on a falling edge, so its pulse must span the exposure less a small drop; an
            edge-triggered detector times its own exposure and only needs a clean edge,
            so half the exposure does.
    """

    name: DetectorName
    ispyb_id: int
    pixel_size_mm: tuple[float, float]
    size_constants: DetectorSizeConstants
    beam_centre_lut: Path
    det_y_target_mm: float
    zebra_ttl_out: int
    zebra_pulse_width_drop_s: float | None

    @property
    def image_size_pixels(self) -> tuple[int, int]:
        return (
            self.size_constants.det_size_pixels.width,
            self.size_constants.det_size_pixels.height,
        )

    @property
    def image_size_mm(self) -> tuple[float, float]:
        return (
            self.size_constants.det_dimension.width,
            self.size_constants.det_dimension.height,
        )

    def zebra_pulse_width_s(self, exposure_time_s: float) -> float:
        """How wide the zebra pulse triggering this detector should be."""
        if self.zebra_pulse_width_drop_s is None:
            return exposure_time_s / 2
        return exposure_time_s - self.zebra_pulse_width_drop_s

    def __str__(self) -> str:
        return str(self.name)


EIGER = SerialDetector(
    name=DetectorName.EIGER,
    ispyb_id=94,
    pixel_size_mm=(0.075, 0.075),
    size_constants=EIGER2_X_9M_SIZE,
    beam_centre_lut=LUT_FILES_PATH / "DetDistToBeamXYConverterE9M.txt",
    # TODO: Move to separate configuration file in daq_configuration #1779
    det_y_target_mm=209,  # 59.0
    zebra_ttl_out=1,
    # 100us, empirically enough to end the exposure cleanly
    zebra_pulse_width_drop_s=0.0001,
)

JUNGFRAU = SerialDetector(
    name=DetectorName.JUNGFRAU,
    # The id the commissioning jungfrau's device defaults to, see
    # dodal.devices.beamlines.i24.commissioning_jungfrau.
    ispyb_id=124,
    pixel_size_mm=(0.075, 0.075),
    size_constants=JUNGFRAU_9M_SIZE,
    beam_centre_lut=JUNGFRAU_BEAM_CENTER_LUT,
    # Should be read from a config file, see
    # https://github.com/DiamondLightSource/mx-bluesky/issues/1502
    det_y_target_mm=730,
    # WARNING. Zebra TTL 2 is also the laser output that serial pump probe uses, so a
    # jungfrau pump probe collection cannot trigger both as things are currently cabled.
    # Static extruder and fixed target collections are unaffected.
    zebra_ttl_out=2,
    # Edge triggered, so it times its own exposure and only needs a clean edge.
    zebra_pulse_width_drop_s=None,
)

SERIAL_DETECTORS: dict[DetectorName, SerialDetector] = {
    detector.name: detector for detector in (EIGER, JUNGFRAU)
}

BEAM_CENTER_LUT_FILES = {
    name: detector.beam_centre_lut for name, detector in SERIAL_DETECTORS.items()
}
