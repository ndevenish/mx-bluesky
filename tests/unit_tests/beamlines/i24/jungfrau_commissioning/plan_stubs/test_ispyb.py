from __future__ import annotations

import datetime
import json
from unittest.mock import patch

import pytest
import responses
from bluesky.run_engine import RunEngine
from dodal.devices.beamlines.i24.focus_mirrors import HFocusMode, VFocusMode
from ophyd_async.core import set_mock_value

from mx_bluesky.beamlines.i24.beam_center import JUNGFRAU_BEAM_CENTER_LUT
from mx_bluesky.beamlines.i24.dcserver import DCServerError
from mx_bluesky.beamlines.i24.jungfrau_commissioning.composites import (
    RotationScanComposite,
)
from mx_bluesky.beamlines.i24.jungfrau_commissioning.plan_stubs.ispyb import (
    MountedSample,
    UnknownVisitError,
    complete_rotation_data_collection,
    create_rotation_data_collection,
    read_mounted_sample,
)
from tests.unit_tests.beamlines.i24.conftest import TEST_DCSERVER_URL
from tests.unit_tests.beamlines.i24.jungfrau_commissioning.utils import (
    get_good_single_rotation_params,
)

STUBS = "mx_bluesky.beamlines.i24.jungfrau_commissioning.plan_stubs.ispyb"

TEST_DCID = 4567780

TEST_VISIT = "cm12345-6"
TEST_DIRECTORY = f"/dls/i24/data/2026/{TEST_VISIT}/jungfrau/0007_file_name"

# The fields of the dcserver DataCollectionIn schema, which rejects anything else -
# an unknown key means a 422 and no deposition at all, rather than a missing value.
DATA_COLLECTION_IN_FIELDS = {
    "axisRange",
    "beamSizeAtSampleX",
    "beamSizeAtSampleY",
    "detectorDistance",
    "detectorId",
    "endTime",
    "exposureTime",
    "numberOfImages",
    "resolution",
    "startImageNumber",
    "transmission",
    "wavelength",
    "fileTemplate",
    "startTime",
    "flux",
    "imageDirectory",
    "xtalSnapshotFullPath1",
    "xtalSnapshotFullPath2",
    "xtalSnapshotFullPath3",
    "xtalSnapshotFullPath4",
    "xBeam",
    "yBeam",
    "omegaStart",
    "phiStart",
    "kappaStart",
    "chiStart",
    "rotationAxis",
    "axisStart",
    "axisEnd",
    "overlap",
    "blSampleId",
    "group",
    "ssx",
    "visit",
    "runStatus",
}


@pytest.fixture
def dcserver(mock_requests: responses.RequestsMock):
    """A dcserver answering over HTTP, so the client that talks to it is under test too.

    Anything not registered here is refused, by the autouse fixture this builds on.
    """
    mock_requests.post(
        f"{TEST_DCSERVER_URL}/dc",
        json={
            "dataCollectionId": TEST_DCID,
            "dataCollectionGroupId": 5354200,
            "url": f"{TEST_DCSERVER_URL}/dc/{TEST_DCID}",
        },
        status=201,
    )
    mock_requests.patch(f"{TEST_DCSERVER_URL}/dc/{TEST_DCID}", json={}, status=200)
    return mock_requests


@pytest.fixture
def beam_center_lut():
    """The jungfrau's distance-to-beam-XY table, which does not exist yet on disk."""
    with patch(f"{STUBS}.beam_center_mm_from_lut", return_value=(122.75, 125.5)) as lut:
        yield lut


def deposited(mock_requests: responses.RequestsMock) -> dict:
    """The data collection the server was actually asked to create."""
    posts = [c for c in mock_requests.calls if c.request.method == "POST"]
    assert len(posts) == 1, f"expected one POST, got {len(posts)}"
    return json.loads(posts[0].request.body)  # type: ignore


@pytest.fixture
def start_time() -> datetime.datetime:
    return datetime.datetime(2026, 8, 18, 15, 57, 4).astimezone()


async def _set_up_readings(
    composite: RotationScanComposite, directory: str = TEST_DIRECTORY
) -> None:
    await composite.dcm.wavelength_in_a.set(1.1)
    await composite.detector_motion.z.set(300)
    await composite.jungfrau.writer.file_path.set(directory)
    await composite.jungfrau.writer.file_name.set("file_name")
    # A 30x10um focus, which the mirrors report in um and ISPyB wants in mm.
    await composite.focus_mirrors.horizontal.set(HFocusMode.FOCUS_3010D)
    await composite.focus_mirrors.vertical.set(VFocusMode.FOCUS_3010D)


async def test_a_data_collection_is_created_from_the_sweep_and_the_hardware(
    run_engine: RunEngine,
    tmp_path,
    rotation_composite: RotationScanComposite,
    dcserver,
    beam_center_lut,
    start_time,
):
    params = get_good_single_rotation_params(tmp_path)
    await _set_up_readings(rotation_composite)

    run_engine(create_rotation_data_collection(rotation_composite, params, start_time))

    data = deposited(dcserver)
    assert data["detectorId"] == 124
    assert data["visit"] == TEST_VISIT
    assert data["imageDirectory"] == TEST_DIRECTORY
    assert data["fileTemplate"] == "file_name.nxs"
    assert data["numberOfImages"] == params.num_images
    assert data["startImageNumber"] == 1
    assert data["startTime"] == start_time.isoformat()
    assert data["exposureTime"] == params.exposure_time_s
    assert data["transmission"] == pytest.approx(10)
    assert data["detectorDistance"] == 300
    assert data["wavelength"] == 1.1
    assert data["group"] == {"experimentType": params.ispyb_experiment_type.value}
    assert data["beamSizeAtSampleX"] == pytest.approx(0.03)
    assert data["beamSizeAtSampleY"] == pytest.approx(0.01)
    # 233.1mm of detector, 300mm away, at 1.1A
    assert data["resolution"] == pytest.approx(2.99, abs=0.01)


async def test_the_sweep_is_described_by_the_axis_it_actually_moves(
    run_engine: RunEngine,
    tmp_path,
    rotation_composite: RotationScanComposite,
    dcserver,
    beam_center_lut,
    start_time,
):
    params = get_good_single_rotation_params(tmp_path)
    await _set_up_readings(rotation_composite)

    run_engine(create_rotation_data_collection(rotation_composite, params, start_time))

    data = deposited(dcserver)
    # The server spells its rotation axes with a capital, the parameters do not.
    assert data["rotationAxis"] == "Omega"
    assert data["axisStart"] == 45
    assert data["omegaStart"] == 45
    assert data["axisRange"] == 0.1
    # Rotations default to the negative direction, so the sweep subtracts its width.
    assert data["axisEnd"] == pytest.approx(45 - params.scan_width_deg)


async def test_the_beam_centre_is_looked_up_at_the_distance_the_detector_is_at(
    run_engine: RunEngine,
    tmp_path,
    rotation_composite: RotationScanComposite,
    dcserver,
    beam_center_lut,
    start_time,
):
    params = get_good_single_rotation_params(tmp_path)
    await _set_up_readings(rotation_composite)

    run_engine(create_rotation_data_collection(rotation_composite, params, start_time))

    # The table is in mm already - the same units ISPyB wants.
    beam_center_lut.assert_called_once_with(JUNGFRAU_BEAM_CENTER_LUT, 300)
    data = deposited(dcserver)
    assert data["xBeam"] == 122.75
    assert data["yBeam"] == 125.5


async def test_a_collection_is_still_deposited_before_there_is_a_beam_centre_table(
    run_engine: RunEngine,
    tmp_path,
    rotation_composite: RotationScanComposite,
    dcserver,
    beam_center_lut,
    start_time,
):
    # The table is built from processed data, so it does not exist until the detector
    # has been collected on - which cannot then be conditional on having it.
    params = get_good_single_rotation_params(tmp_path)
    await _set_up_readings(rotation_composite)
    beam_center_lut.side_effect = FileNotFoundError("no such lookup table")

    run_engine(create_rotation_data_collection(rotation_composite, params, start_time))

    data = deposited(dcserver)
    assert "xBeam" not in data
    assert "yBeam" not in data
    assert data["detectorId"] == 124


async def test_only_fields_the_server_accepts_are_sent(
    run_engine: RunEngine,
    tmp_path,
    rotation_composite: RotationScanComposite,
    dcserver,
    beam_center_lut,
    start_time,
):
    params = get_good_single_rotation_params(tmp_path)
    await _set_up_readings(rotation_composite)

    run_engine(create_rotation_data_collection(rotation_composite, params, start_time))

    assert set(deposited(dcserver)) <= DATA_COLLECTION_IN_FIELDS


@pytest.mark.parametrize(
    "sample_id, puck, pin, expected",
    [
        (0, None, None, None),
        (123456, None, None, 123456),
        (0, 13, 1, {"puck": 13, "pin": 1}),
        # A sample the robot could name is named, rather than left to be looked up.
        (123456, 13, 1, 123456),
    ],
    ids=["no sample", "a sample", "a location", "both"],
)
async def test_a_sample_is_only_named_when_there_is_one(
    run_engine: RunEngine,
    tmp_path,
    rotation_composite: RotationScanComposite,
    dcserver,
    start_time,
    sample_id: int,
    puck: int | None,
    pin: int | None,
    expected: int | dict | None,
):
    # ISPyB has a foreign key on blSampleId, so sending the parameters' stand-in for
    # "no sample" fails the whole insert rather than leaving the column empty. Where
    # only the dewar location is known, the server resolves the sample from that.
    params = get_good_single_rotation_params(tmp_path)
    params.sample_id = sample_id
    params.sample_puck = puck
    params.sample_pin = pin
    await _set_up_readings(rotation_composite)

    run_engine(create_rotation_data_collection(rotation_composite, params, start_time))

    assert deposited(dcserver).get("blSampleId") == expected


@pytest.mark.parametrize(
    "sample_id, puck, pin, expected",
    [
        (123456, 0, 0, MountedSample(sample_id=123456)),
        # The id the robot holds is of the sample it loaded, so it wins over where it
        # loaded it from.
        (123456, 13, 1, MountedSample(sample_id=123456)),
        (0, 13, 1, MountedSample(puck=13, pin=1)),
        # Half a location resolves to nothing, so it is not worth sending.
        (0, 13, 0, MountedSample()),
        (0, 0, 1, MountedSample()),
        (0, 0, 0, MountedSample()),
    ],
    ids=["an id", "an id and a location", "a location", "no pin", "no puck", "neither"],
)
async def test_the_mounted_sample_is_whatever_the_robot_can_say_about_it(
    run_engine: RunEngine,
    rotation_composite: RotationScanComposite,
    sample_id: int,
    puck: int,
    pin: int,
    expected: MountedSample,
):
    set_mock_value(rotation_composite.robot.sample_id, sample_id)
    set_mock_value(rotation_composite.robot.next_puck, puck)
    set_mock_value(rotation_composite.robot.next_pin, pin)

    result = run_engine(read_mounted_sample(rotation_composite.robot)).plan_result  # type: ignore

    assert result == expected


async def test_a_robot_that_cannot_be_read_leaves_the_sample_unknown(
    run_engine: RunEngine, rotation_composite: RotationScanComposite
):
    # Which sample this is, is worth labelling a collection with, but not worth
    # losing one over.
    with patch.object(
        rotation_composite.robot.sample_id,
        "read",
        side_effect=TimeoutError("the robot IOC is down"),
    ):
        result = run_engine(read_mounted_sample(rotation_composite.robot)).plan_result  # type: ignore

    assert result == MountedSample()


async def test_the_visit_is_taken_from_where_the_data_is_written(
    run_engine: RunEngine,
    tmp_path,
    rotation_composite: RotationScanComposite,
    dcserver,
    beam_center_lut,
    start_time,
):
    # Until numtracker states the visit, /dls/<beamline>/data/<year>/<visit> is the only
    # thing that knows which beamtime this is.
    params = get_good_single_rotation_params(tmp_path)
    await _set_up_readings(rotation_composite, directory="/dls/i24/data/2027/mx98765-4")

    run_engine(create_rotation_data_collection(rotation_composite, params, start_time))

    assert deposited(dcserver)["visit"] == "mx98765-4"


async def test_a_visit_in_the_parameters_wins_over_the_data_path(
    run_engine: RunEngine,
    tmp_path,
    rotation_composite: RotationScanComposite,
    dcserver,
    beam_center_lut,
    start_time,
):
    params = get_good_single_rotation_params(tmp_path)
    params.visit = "cm98765-4"
    await _set_up_readings(rotation_composite)

    run_engine(create_rotation_data_collection(rotation_composite, params, start_time))

    assert deposited(dcserver)["visit"] == "cm98765-4"


async def test_nothing_is_deposited_when_no_visit_can_be_established(
    run_engine: RunEngine,
    tmp_path,
    rotation_composite: RotationScanComposite,
    dcserver,
    beam_center_lut,
    start_time,
):
    params = get_good_single_rotation_params(tmp_path)
    await _set_up_readings(rotation_composite, directory=str(tmp_path))

    with pytest.raises(UnknownVisitError, match="not under"):
        run_engine(
            create_rotation_data_collection(rotation_composite, params, start_time)
        )

    assert not dcserver.calls


async def test_a_rejected_deposition_raises_into_the_plan_with_the_servers_reason(
    run_engine: RunEngine,
    tmp_path,
    rotation_composite: RotationScanComposite,
    mock_requests: responses.RequestsMock,
    beam_center_lut,
    start_time,
):
    # Unlike a callback, a stub can stop the collection - which is the point of it
    # being one. The status alone does not say what was wrong with the request, so the
    # body has to travel with the error.
    params = get_good_single_rotation_params(tmp_path)
    await _set_up_readings(rotation_composite)
    mock_requests.post(
        f"{TEST_DCSERVER_URL}/dc",
        body="a foreign key constraint fails",
        status=500,
    )

    with pytest.raises(DCServerError, match="a foreign key constraint fails"):
        run_engine(
            create_rotation_data_collection(rotation_composite, params, start_time)
        )


async def test_the_deposition_is_authenticated_and_sent_where_it_is_told(
    run_engine: RunEngine,
    tmp_path,
    rotation_composite: RotationScanComposite,
    dcserver: responses.RequestsMock,
    beam_center_lut,
    start_time,
):
    params = get_good_single_rotation_params(tmp_path)
    await _set_up_readings(rotation_composite)

    run_engine(create_rotation_data_collection(rotation_composite, params, start_time))

    request = dcserver.calls[0].request
    # The server the environment names, not the production one compiled in.
    assert request.url == f"{TEST_DCSERVER_URL}/dc"
    assert request.headers["Authorization"] == "Bearer a-test-token"


@pytest.mark.parametrize(
    "aborted, status",
    [(False, "DataCollection Successful"), (True, "DataCollection Cancelled")],
    ids=["finished", "aborted"],
)
async def test_a_collection_is_marked_with_how_it_ended(
    run_engine: RunEngine, dcserver: responses.RequestsMock, aborted: bool, status: str
):
    run_engine(complete_rotation_data_collection(TEST_DCID, aborted=aborted))

    request = dcserver.calls[0].request
    assert request.url == f"{TEST_DCSERVER_URL}/dc/{TEST_DCID}"
    patch_data = json.loads(request.body)  # type: ignore
    assert patch_data["runStatus"] == status
    assert patch_data["endTime"]


async def test_failing_to_mark_a_collection_complete_does_not_fail_the_plan(
    run_engine: RunEngine, mock_requests: responses.RequestsMock
):
    # The data exists by this point; failing the plan would not bring the record back.
    mock_requests.patch(
        f"{TEST_DCSERVER_URL}/dc/{TEST_DCID}", body="the server is down", status=500
    )

    run_engine(complete_rotation_data_collection(TEST_DCID, aborted=False))

    assert len(mock_requests.calls) == 1
