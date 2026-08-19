"""How a serial collection drives whichever detector is in the beam.

Both collection plans want the same handful of things from their detector: set it up
for the collection, tell them the filename it will actually write, stop it, and put it
back the way GDA expects to find it. What those mean in hardware terms is entirely
different per detector - the Eiger is a pile of raw caputs into its IOC, the Jungfrau
is an ophyd-async flyer - so the plans ask through this interface rather than branching
on the detector name.

Note that nothing here decides when a collection is over. Fixed target ends when the
PMAC motion program finishes, the extruder when the zebra disarms; both are properties
of the motion driving the sample, not of the detector. `wait_for_completion` exists for
a detector that must additionally be waited on - a flyer that has to be `complete`d -
and not to determine that the collection has finished.

Static facts about each detector live on `SerialDetector` in parameters.detector; this
module is only the operations that talk to hardware.
"""

from abc import ABC, abstractmethod
from collections.abc import Generator
from datetime import datetime
from enum import StrEnum

import bluesky.plan_stubs as bps
from bluesky.utils import Msg, MsgGenerator
from dodal.beamlines.i24 import JUNGFRAU_DATA_DIR, JUNGFRAU_FILENAME
from dodal.devices.beamlines.i24.commissioning_jungfrau import (
    CommissioningJungfrauDetector,
)
from dodal.devices.beamlines.i24.dcm import DCM
from dodal.devices.motors import YZStage
from ophyd_async.fastcs.jungfrau import (
    GainMode,
    create_jungfrau_external_triggering_info,
    create_jungfrau_internal_triggering_info,
)

from mx_bluesky.beamlines.i24.serial.log import SSX_LOGGER
from mx_bluesky.beamlines.i24.serial.parameters import (
    DetectorName,
    ExtruderParameters,
    FixedTargetParameters,
    SerialDetector,
)
from mx_bluesky.beamlines.i24.serial.parameters.detector import EIGER, JUNGFRAU
from mx_bluesky.beamlines.i24.serial.setup_beamline import (
    EigerPVs,
    caget,
    caget_once,
    cagetstring,
    caput,
    pv,
)
from mx_bluesky.beamlines.i24.serial.setup_beamline import setup_beamline as sup
from mx_bluesky.beamlines.i24.serial.setup_beamline.setup_detector import (
    UnknownDetectorTypeError,
)
from mx_bluesky.beamlines.i24.serial.write_nexus import call_nexgen


class AcquisitionMode(StrEnum):
    """How the detector is told to take each image in a collection.

    SOFTWARE: the detector is armed, and one software trigger then runs it through the
        whole series on its own timing. Used by a static extruder collection.
    HARDWARE: the zebra sends one trigger per image. Used by fixed target, and by a
        pump probe extruder collection.

    The values are the action names setup_beamline.eiger takes, so that the Eiger's
    implementation can pass them straight through.
    """

    SOFTWARE = "quickshot"
    HARDWARE = "triggered"


class SerialDetectorControl(ABC):
    """The operations a serial collection plan performs on its detector.

    Every method is a plan, even where a given detector's implementation needs no
    messages to do it, so that the plans call them the same way regardless of which
    detector answered.
    """

    def __init__(self, detector: SerialDetector):
        self.detector = detector

    @abstractmethod
    def start_new_file_series(self) -> MsgGenerator:
        """Move the detector on to a new set of files.

        Only the extruder calls this, which is why two extruder collections in a row
        do not overwrite each other and two fixed target ones do. That difference
        looks like an oversight rather than a decision, but changing it is a change
        to what users get, so it is left as it is here.
        """

    @abstractmethod
    def setup_for_collection(
        self,
        filepath: str,
        filename: str,
        num_images: int,
        exposure_time_s: float,
        mode: AcquisitionMode,
    ) -> MsgGenerator:
        """Configure the detector for this collection and arm it."""

    @abstractmethod
    def collection_filename(self) -> Generator[Msg, None, str]:
        """The filename the detector will actually write.

        Only valid once `setup_for_collection` has run, as a detector may decide part
        of its own filename - the Eiger appends a sequence id.
        """

    @abstractmethod
    def start_acquisition(self) -> MsgGenerator:
        """Start the detector acquiring.

        Only needed where the detector has to be told to start on top of being armed.
        Fixed target does not call this, because the PMAC motion program and the zebra
        gating between them drive the whole collection.
        """

    @abstractmethod
    def wait_for_completion(self) -> MsgGenerator:
        """Wait for a detector that must be waited on in its own right.

        This does not decide that the collection has ended - see the module docstring.
        """

    @abstractmethod
    def frames_captured(self) -> Generator[Msg, None, int | None]:
        """How many frames the detector has written, or None if it cannot say."""

    @abstractmethod
    def stop_acquisition(self) -> MsgGenerator:
        """Stop the detector and close its file at the end of a good collection."""

    @abstractmethod
    def abort_acquisition(self) -> MsgGenerator:
        """Stop the detector after a collection that failed part way through."""

    @abstractmethod
    def return_to_normal(self) -> MsgGenerator:
        """Put the detector back into the state other users expect to find it in."""

    @abstractmethod
    def write_nexus_metadata(
        self,
        chip_prog_dict: dict | None,
        parameters: ExtruderParameters | FixedTargetParameters,
        wavelength_in_a: float,
        beam_center_in_pix: tuple[float, float],
        start_time: datetime,
    ) -> MsgGenerator:
        """Have the nexus file for this collection written."""


class EigerControl(SerialDetectorControl):
    """The Eiger, driven by raw caputs into its IOC and Odin.

    This is what the collection plans have always done, moved behind the interface
    unchanged. It should collapse into a thin wrapper over an ophyd-async device once
    one exists, see https://github.com/DiamondLightSource/mx-bluesky/issues/62.
    """

    def __init__(self, dcm: DCM, detector_stage: YZStage):
        super().__init__(EIGER)
        self._dcm = dcm
        self._detector_stage = detector_stage

    def start_new_file_series(self) -> MsgGenerator:
        caput(EigerPVs.sequence_id, int(caget(EigerPVs.sequence_id)) + 1)
        yield from bps.null()

    def setup_for_collection(
        self,
        filepath: str,
        filename: str,
        num_images: int,
        exposure_time_s: float,
        mode: AcquisitionMode,
    ) -> MsgGenerator:
        SSX_LOGGER.info("Using Eiger detector")
        SSX_LOGGER.debug(f"Creating the directory for the collection in {filepath}.")
        SSX_LOGGER.info(f"{mode} Eiger setup: filepath {filepath}")
        SSX_LOGGER.info(f"{mode} Eiger setup: filename {filename}")
        SSX_LOGGER.info(f"{mode} Eiger setup: number of images {num_images}")
        SSX_LOGGER.info(f"{mode} Eiger setup: exposure time {exposure_time_s}")
        yield from sup.eiger(
            str(mode),
            [filepath, filename, num_images, exposure_time_s],
            self._dcm,
            self._detector_stage,
        )

    def collection_filename(self) -> Generator[Msg, None, str]:
        # A plain caget, but a plan because the interface is - see the class docstring.
        yield from bps.null()
        # cagetstring is untyped, and its inferred type includes bytes.
        return str(cagetstring(EigerPVs.filename_rbv))

    def start_acquisition(self) -> MsgGenerator:
        SSX_LOGGER.info("Triggering Eiger NOW")
        caput(pv.eiger_trigger, 1)
        yield from bps.null()

    def wait_for_completion(self) -> MsgGenerator:
        # Nothing to wait on: the collection ends with the motion that gates it.
        yield from bps.null()

    def frames_captured(self) -> Generator[Msg, None, int | None]:
        yield from bps.null()
        # Only ever used to report on a collection that has already ended, so a read
        # that does not work should be given up on rather than blocking the plan.
        captured = caget_once(EigerPVs.frames_captured)
        return int(captured) if captured is not None else None

    def stop_acquisition(self) -> MsgGenerator:
        SSX_LOGGER.debug("Eiger Acquire STOP")
        caput(pv.eiger_acquire, 0)
        caput(pv.eiger_od_capture, "Done")
        yield from bps.sleep(0.5)

    def abort_acquisition(self) -> MsgGenerator:
        # Deliberately not the same as stopping cleanly: aborting has only ever
        # stopped the detector, leaving Odin still in capture. That looks wrong, but
        # it is what has always happened, so it is preserved rather than quietly
        # changed here.
        SSX_LOGGER.debug("Eiger Acquire STOP")
        caput(pv.eiger_acquire, 0)
        yield from bps.sleep(0.5)

    def return_to_normal(self) -> MsgGenerator:
        yield from sup.eiger("return-to-normal", None, self._dcm, self._detector_stage)
        SSX_LOGGER.debug(f"Eiger sequence id is now {caget(EigerPVs.sequence_id)}")

    def write_nexus_metadata(
        self,
        chip_prog_dict: dict | None,
        parameters: ExtruderParameters | FixedTargetParameters,
        wavelength_in_a: float,
        beam_center_in_pix: tuple[float, float],
        start_time: datetime,
    ) -> MsgGenerator:
        SSX_LOGGER.debug("Start nexus writing service.")
        yield from call_nexgen(
            chip_prog_dict,
            parameters,
            wavelength_in_a,
            beam_center_in_pix,
            start_time,
        )


class JungfrauControl(SerialDetectorControl):
    """The commissioning Jungfrau, driven as the ophyd-async flyer it already is.

    WARNING. Where the data lands is not yet the collection directory the rest of a
    serial collection uses. The commissioning jungfrau writes under dodal's hardcoded
    JUNGFRAU_DATA_DIR, in a numbered subdirectory per acquisition, because i24 does not
    have numtracker yet; only the filename is ours to choose. DCID is therefore told a
    directory the images are not in. Removed along with the rest of the temporary path
    handling by https://github.com/DiamondLightSource/mx-bluesky/issues/1527.
    """

    def __init__(self, jungfrau: CommissioningJungfrauDetector):
        super().__init__(JUNGFRAU)
        self._jungfrau = jungfrau
        self._filename = ""
        self._mode: AcquisitionMode | None = None

    def start_new_file_series(self) -> MsgGenerator:
        # Nothing to do: the jungfrau's path provider gives every acquisition its own
        # numbered subdirectory, so collections cannot overwrite each other.
        yield from bps.null()

    def setup_for_collection(
        self,
        filepath: str,
        filename: str,
        num_images: int,
        exposure_time_s: float,
        mode: AcquisitionMode,
    ) -> MsgGenerator:
        SSX_LOGGER.info("Using Jungfrau detector")
        SSX_LOGGER.warning(
            f"Jungfrau data will be written under {JUNGFRAU_DATA_DIR}, not {filepath}. "
            "See JungfrauControl."
        )
        self._filename = filename
        self._mode = mode
        # Read by the filewriter when the jungfrau is prepared, below. Only has an
        # effect while i24 writes without numtracker; see JUNGFRAU_FILENAME.
        JUNGFRAU_FILENAME.filename = filename

        if mode is AcquisitionMode.HARDWARE:
            trigger_info = create_jungfrau_external_triggering_info(
                num_images, exposure_time_s
            )
        else:
            trigger_info = create_jungfrau_internal_triggering_info(
                num_images, exposure_time_s
            )

        yield from bps.stage(self._jungfrau, wait=True)
        yield from bps.mv(self._jungfrau.detector.gain_mode, GainMode.DYNAMIC)
        yield from bps.prepare(self._jungfrau, trigger_info, wait=True)

        if mode is AcquisitionMode.HARDWARE:
            # Kicking off an externally triggered collection only means starting to
            # listen for edges, so it can happen now - and has to, because a fixed
            # target collection has no separate go signal to hang it off. The PMAC
            # motion program and the zebra between them run the whole thing.
            yield from bps.kickoff(self._jungfrau, wait=True)

    def collection_filename(self) -> Generator[Msg, None, str]:
        yield from bps.null()
        return self._filename

    def start_acquisition(self) -> MsgGenerator:
        if self._mode is AcquisitionMode.SOFTWARE:
            # Internally triggered, so the detector runs from the moment it is kicked
            # off. That has to be now, with the shutter open, rather than at setup.
            yield from bps.kickoff(self._jungfrau, wait=True)
        else:
            yield from bps.null()

    def wait_for_completion(self) -> MsgGenerator:
        yield from bps.complete(self._jungfrau, wait=True)

    def frames_captured(self) -> Generator[Msg, None, int | None]:
        captured = yield from bps.rd(self._jungfrau.writer.frame_counter)
        return int(captured)

    def stop_acquisition(self) -> MsgGenerator:
        yield from bps.unstage(self._jungfrau, wait=True)

    def abort_acquisition(self) -> MsgGenerator:
        yield from bps.unstage(self._jungfrau, wait=True)

    def return_to_normal(self) -> MsgGenerator:
        # Nothing to put back: unlike the Eiger, nothing else expects to find the
        # commissioning jungfrau in a particular state.
        yield from bps.null()

    def write_nexus_metadata(
        self,
        chip_prog_dict: dict | None,
        parameters: ExtruderParameters | FixedTargetParameters,
        wavelength_in_a: float,
        beam_center_in_pix: tuple[float, float],
        start_time: datetime,
    ) -> MsgGenerator:
        # nexgen reads the collection back off the Eiger's PVs, so it cannot describe a
        # jungfrau collection. The commissioning plans write a collection_info.json
        # through JsonMetadataWriter instead; nothing equivalent is wired up for serial.
        SSX_LOGGER.warning(
            "No nexus file will be written for this jungfrau collection."
        )
        yield from bps.null()


def get_detector_control(
    detector_name: DetectorName,
    dcm: DCM,
    detector_stage: YZStage,
    jungfrau: CommissioningJungfrauDetector | None = None,
) -> SerialDetectorControl:
    """How to drive the detector a collection has been asked to run on."""
    match detector_name:
        case DetectorName.EIGER:
            return EigerControl(dcm, detector_stage)
        case DetectorName.JUNGFRAU:
            if jungfrau is None:
                raise UnknownDetectorTypeError(
                    "A jungfrau collection needs the jungfrau device."
                )
            return JungfrauControl(jungfrau)
        case _:
            raise UnknownDetectorTypeError(
                f"Cannot run a serial collection on {detector_name}."
            )


def check_all_frames_written(
    detector_control: SerialDetectorControl, expected_num_images: int
) -> MsgGenerator:
    """Warn if the detector wrote fewer frames than the collection asked for.

    The collection has already ended by this point - it is the motion that decides
    that - so this only reports on what landed, it does not wait for anything. A
    detector that cannot report a frame count is skipped silently.
    """
    captured = yield from detector_control.frames_captured()
    if captured is None:
        return
    if captured < expected_num_images:
        SSX_LOGGER.warning(
            f"Detector wrote {captured} frames, expected {expected_num_images}. "
            f"{expected_num_images - captured} frames appear to have been lost."
        )
    else:
        SSX_LOGGER.info(f"Detector wrote all {captured} expected frames.")
