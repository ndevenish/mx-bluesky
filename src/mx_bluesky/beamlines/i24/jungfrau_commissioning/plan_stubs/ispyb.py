"""Recording a jungfrau rotation sweep in ISPyB.

Done from the plan, the way the serial collections do it, rather than from a callback
listening to its documents: a sweep ISPyB has no record of is not worth collecting, and
only the plan can call one off - the RunEngine discards whatever a callback raises.
"""

import datetime
from pathlib import Path

import bluesky.plan_stubs as bps
from bluesky.utils import MsgGenerator
from dodal.devices.detector.det_dim_constants import JUNGFRAU_9M_SIZE

from mx_bluesky.beamlines.i24.beam_center import (
    JUNGFRAU_BEAM_CENTER_LUT,
    beam_center_mm_from_lut,
)
from mx_bluesky.beamlines.i24.dcserver import (
    create_data_collection,
    resolution_at_detector_edge,
    update_data_collection,
)
from mx_bluesky.beamlines.i24.jungfrau_commissioning.composites import (
    RotationScanComposite,
)
from mx_bluesky.common.parameters.constants import USE_NUMTRACKER
from mx_bluesky.common.parameters.rotation import SingleRotationScan
from mx_bluesky.common.utils.log import LOGGER

# Data is written under /dls/<beamline>/data/<year>/<visit>, so the visit is the fifth
# thing in the path. Temporary: numtracker will state the visit outright, rather than
# leaving it to be read back out of where the data landed. See
# https://github.com/DiamondLightSource/mx-bluesky/issues/1527.
_VISIT_PATH_INDEX = 5


class UnknownVisitError(Exception):
    """There is no visit to record a collection under."""


def create_rotation_data_collection(
    composite: RotationScanComposite,
    params: SingleRotationScan,
    start_time: datetime.datetime,
) -> MsgGenerator[int]:
    """Record this sweep in ISPyB, returning the data collection's id.

    Raises rather than returning if the collection cannot be recorded, so call this
    before the sweep starts, while there is still one to call off.
    """
    wavelength_in_a = yield from bps.rd(composite.dcm.wavelength_in_a)
    detector_distance_mm = yield from bps.rd(composite.detector_motion.z)
    # Only known once the jungfrau has been prepared, which is what tells the writer
    # where to write.
    image_directory = yield from bps.rd(composite.jungfrau.writer.file_path)
    file_name = yield from bps.rd(composite.jungfrau.writer.file_name)
    detector_id = yield from bps.rd(composite.jungfrau.ispyb_detector_id)
    beam_size_x_um = yield from bps.rd(composite.focus_mirrors.beam_size_x)
    beam_size_y_um = yield from bps.rd(composite.focus_mirrors.beam_size_y)

    data = {
        "detectorId": detector_id,
        # The writer is given a name without an extension, and appends one itself.
        "fileTemplate": f"{file_name}.nxs",
        "imageDirectory": str(image_directory),
        "startTime": start_time.isoformat(),
        "visit": _visit_for(params, str(image_directory)),
        "group": {"experimentType": params.ispyb_experiment_type.value},
        "numberOfImages": params.num_images,
        "startImageNumber": 1,
        "exposureTime": params.exposure_time_s,
        "transmission": params.transmission_frac * 100,
        "detectorDistance": detector_distance_mm,
        "wavelength": wavelength_in_a,
        "resolution": resolution_at_detector_edge(
            JUNGFRAU_9M_SIZE.det_dimension.width, detector_distance_mm, wavelength_in_a
        ),
        "beamSizeAtSampleX": beam_size_x_um / 1000,
        "beamSizeAtSampleY": beam_size_y_um / 1000,
        # The sweep, as run: a negative direction subtracts the width from the start.
        "rotationAxis": params.rotation_axis.value.capitalize(),
        "axisStart": params.omega_start_deg,
        "axisEnd": params.omega_start_deg
        + params.scan_width_deg * params.rotation_direction.multiplier,
        "axisRange": params.rotation_increment_deg,
        "omegaStart": params.omega_start_deg,
        **_beam_center(detector_distance_mm),
    }
    if params.sample_id > 0:
        # ISPyB has a foreign key on this, so a sample that does not exist fails the
        # whole insert. The parameters demand a sample id, and the web UI defaults the
        # field to 0, so that is what "no sample" looks like.
        data["blSampleId"] = params.sample_id

    dcid = create_data_collection(data)
    LOGGER.info("Generated DCID %s", dcid)
    return dcid


def complete_rotation_data_collection(dcid: int, aborted: bool) -> MsgGenerator:
    """Mark a data collection finished.

    Unlike creating one, failing here is only logged: the data exists by this point, and
    failing the plan over the record would not bring it back.
    """
    end_time = datetime.datetime.now().astimezone()
    try:
        update_data_collection(
            dcid,
            {
                "endTime": end_time.isoformat(),
                "runStatus": "DataCollection Cancelled"
                if aborted
                else "DataCollection Successful",
            },
        )
        LOGGER.info("Completed DCID %s", dcid)
    except Exception as e:
        LOGGER.exception("Could not complete DCID %s: %s", dcid, e)
    yield from bps.null()


def _visit_for(params: SingleRotationScan, image_directory: str) -> str:
    """The visit to collect under.

    Numtracker will state it outright once it is configured for these plans (see
    https://github.com/DiamondLightSource/mx-bluesky/issues/1527); until then the
    parameters hold a placeholder rather than a visit, and the only thing that knows
    which beamtime this is is where the data is being written.
    """
    if params.visit != USE_NUMTRACKER:
        return params.visit
    parts = Path(image_directory).parts
    if len(parts) > _VISIT_PATH_INDEX and parts[1] == "dls" and parts[3] == "data":
        return parts[_VISIT_PATH_INDEX]
    raise UnknownVisitError(
        f"Cannot tell which visit {image_directory} belongs to: it is not under "
        "/dls/<beamline>/data/<year>/<visit>."
    )


def _beam_center(detector_distance_mm: float) -> dict[str, float]:
    """Where the beam lands on the jungfrau at this distance, if that is known yet.

    The lookup table is built from processed data, so it does not exist until the
    detector has been collected on. Deposit the collection without a beam centre rather
    than not at all while that is the case.
    """
    try:
        beam_x_mm, beam_y_mm = beam_center_mm_from_lut(
            JUNGFRAU_BEAM_CENTER_LUT, detector_distance_mm
        )
    except Exception as e:
        LOGGER.warning(
            "Depositing without a beam centre: could not read %s (%s)",
            JUNGFRAU_BEAM_CENTER_LUT,
            e,
        )
        return {}
    return {"xBeam": beam_x_mm, "yBeam": beam_y_mm}
