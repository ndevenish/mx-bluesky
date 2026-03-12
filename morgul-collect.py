#!/usr/bin/env -S uv run

import asyncio
import importlib
import json
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePath
from typing import Annotated

import pint
import typer
from bluesky.run_engine import RunEngine
from dodal.beamlines import i24
from dodal.common.beamlines.beamline_utils import device_factory
from dodal.devices.i24.commissioning_jungfrau import CommissioningJungfrau
from dodal.utils import BeamlinePrefix, get_beamline_name
from ophyd_async.core import AutoMaxIncrementingPathProvider, init_devices
from ophyd_async.fastcs.jungfrau import GainMode
from rich import print
from rich.table import Table

import mx_bluesky.beamlines.i24.jungfrau_commissioning
from mx_bluesky.beamlines.i24.jungfrau_commissioning.composites import (
    RotationScanComposite,
)
from mx_bluesky.beamlines.i24.jungfrau_commissioning.do_darks import (
    do_pedestal_darks,
    do_standard_darks,
)
from mx_bluesky.beamlines.i24.jungfrau_commissioning.plan_utils import (
    add_info_logs_to_stdout,
)
from mx_bluesky.beamlines.i24.jungfrau_commissioning.rotation_scan_plan import (
    multi_rotation_plan_varying_transmission,
)
from mx_bluesky.beamlines.i24.parameters.rotation import (
    MultiRotationScanByTransmissions,
)
from mx_bluesky.common.utils.log import LOGGER, do_default_logging_setup

DEFAULT_STORAGE = Path("/dls/i24/data/2025/cm40647-4/jungfrau")

ureg = pint.UnitRegistry()


class NaturalOrderGroup(typer.core.TyperGroup):
    """Custom grouping class for typer for ordered commands"""

    def list_commands(self, _ctx):
        return self.commands.keys()


app = typer.Typer(
    cls=NaturalOrderGroup,
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
    add_completion=False,
    rich_markup_mode="rich",
)


def do_common_bluesky_setup():
    BL = get_beamline_name("i24")
    PREFIX = BeamlinePrefix(BL)
    do_default_logging_setup("i24-bluesky.log", 12231)  # Dodal graylog stream
    add_info_logs_to_stdout(LOGGER)
    return BL, PREFIX


def parse_decimal(value: str) -> Decimal:
    try:
        return Decimal(value)
    except InvalidOperation as e:
        raise typer.BadParameter(f"'{value}' is not a valid decimal number.") from e


def parse_quantity(
    default_unit: str | pint.Unit | None, *, dimensionality: str
) -> Callable[[str], pint.Quantity]:
    """Parse a string as a pint quantity, checking dimensionality and optional default"""

    def _inner(value: str) -> pint.Quantity:
        try:
            q = pint.Quantity(value)
        except pint.UndefinedUnitError as e:
            raise typer.BadParameter(f"Unrecognised unit: {e}") from e
        if q.unitless and default_unit:
            q *= ureg.Unit(default_unit)
        if not q.check(dimensionality):
            raise typer.BadParameter(
                f"Unexpected units, provided {q.dimensionality} instead of {dimensionality}"
            )
        return q

    return _inner


def module_path(name) -> Path | None:
    spec = importlib.util.find_spec(name)
    if spec is None or spec.origin is None or spec.origin == "built-in":
        return None
    return Path(spec.origin)


def find_applicable_params(filename: str) -> Path | None:
    search = [
        Path.cwd(),
        Path(__file__).parent,
        # Bad, but we know this exists, and need will go away later
        Path(mx_bluesky.beamlines.i24.jungfrau_commissioning.__path__._path[0])
        / "plans_from_bash",
        Path(mx_bluesky.beamlines.i24.jungfrau_commissioning.__path__._path[0]),
    ]
    for path in search:
        if (path / filename).is_file():
            return path / filename
    breakpoint()
    return None


@app.command()
def pedestals(
    exposure_time: Annotated[
        pint.Quantity,
        typer.Argument(
            parser=parse_quantity("s", dimensionality="[time]"),
            metavar="TIME",
            help="Exposure time per frame. Either seconds, or a shorthand e.g. '1ms'",
        ),
    ],
    storage_directory: Annotated[
        Path, typer.Option("-o", "--output", help="Output directory")
    ] = DEFAULT_STORAGE,
    pedestal_loops: Annotated[
        int, typer.Argument(metavar="N_LOOPS", help="Number of pedestal loops")
    ] = 200,
    pedestal_frames: Annotated[
        int,
        typer.Argument(metavar="N_FRAMES", help="Number of frames per pedestal loop."),
    ] = 20,
    period: Annotated[
        pint.Quantity | None,
        typer.Option(
            "-p",
            "--period",
            parser=parse_quantity("s", dimensionality="[time]"),
            metavar="TIME",
            help="Separately specified period (time between frames) from exposure time. If set, this will be used as the gap between frames, instead of defaulting to the same as exposure time.",
        ),
    ] = None,
):
    """Run pedestal calibrations"""
    BL, PREFIX = do_common_bluesky_setup()

    deadtime = (period or exposure_time) - exposure_time

    @device_factory()
    def commissioning_jungfrau() -> CommissioningJungfrau:
        return CommissioningJungfrau(
            f"{PREFIX.beamline_prefix}-EA-JFRAU-01:",
            f"{PREFIX.beamline_prefix}-JUNGFRAU-META:FD:",
            AutoMaxIncrementingPathProvider(PurePath(storage_directory), dated=True),  # type: ignore
        )

    async def do_plan():
        RE = RunEngine()
        with init_devices():
            jf = commissioning_jungfrau()
        RE(
            do_pedestal_darks(
                exposure_time.to("seconds").m,
                pedestal_frames,
                pedestal_loops,
                jf,
                deadtime_s=deadtime.to("seconds").m,
            )
        )

    print("Running pedestal calibration")
    print(f"Exposure time: {exposure_time.to_compact():~}")
    if period:
        print(f"       Period: {period.to_compact():~}")

    asyncio.run(do_plan())


@app.command()
def darks(
    exposure_time: Annotated[
        pint.Quantity,
        typer.Argument(
            parser=parse_quantity("s", dimensionality="[time]"),
            metavar="TIME",
            help="Exposure time per frame. Either seconds, or a shorthand e.g. '1ms'",
        ),
    ],
    storage_directory: Annotated[
        Path, typer.Option("-o", "--output", help="Output directory")
    ] = DEFAULT_STORAGE,
    gainmode: GainMode = GainMode.DYNAMIC,
    frames: Annotated[int, typer.Argument()] = 1000,
    period: Annotated[
        pint.Quantity | None,
        typer.Option(
            "-p",
            "--period",
            parser=parse_quantity("s", dimensionality="[time]"),
            metavar="TIME",
            help="Separately specified period from exposure time. If set, this will be used as the gap between frames, instead of defaulting to the same as exposure time.",
        ),
    ] = None,
):
    """Collect dark images, in a specific gain mode"""
    BL, PREFIX = do_common_bluesky_setup()

    deadtime = (period or exposure_time) - exposure_time

    @device_factory()
    def commissioning_jungfrau() -> CommissioningJungfrau:
        return CommissioningJungfrau(
            f"{PREFIX.beamline_prefix}-EA-JFRAU-01:",
            f"{PREFIX.beamline_prefix}-JUNGFRAU-META:FD:",
            AutoMaxIncrementingPathProvider(PurePath(storage_directory), dated=True),  # type: ignore
        )

    print(f"{exposure_time.to(ureg.s).to_compact()=}")
    print(f"{deadtime.to(ureg.s).to_compact()}")

    async def do_plan():
        RE = RunEngine()
        with init_devices():
            jf = commissioning_jungfrau()
        RE(
            do_standard_darks(
                gainmode,
                exposure_time.to("seconds").m,
                frames,
                jf,
                deadtime_s=deadtime.to("seconds").m,
            )
        )

    print("Collecting dark frames")
    print(f"Exposure time: {exposure_time.to_compact():~}")
    if period:
        print(f"       Period: {period.to_compact():~}")

    asyncio.run(do_plan())


@app.command()
def rotation(
    name: Annotated[str, typer.Argument(help="Name for the dataset")],
    exposure_time: Annotated[
        pint.Quantity,
        typer.Option(
            "--exposure",
            "-e",
            parser=parse_quantity("s", dimensionality="[time]"),
            metavar="TIME",
            help="Exposure time per frame. Either seconds, or a shorthand e.g. '1ms'",
        ),
    ],
    storage_directory: Annotated[
        Path, typer.Option("-o", "--output", help="Output directory")
    ] = DEFAULT_STORAGE,
    transmission: Annotated[
        pint.Quantity | None,
        typer.Option(
            "-t",
            "--transmission",
            help="Beamline transmission, in percentage",
            parser=parse_quantity(None, dimensionality=""),
            metavar="FRAC",
        ),
    ] = None,
    distance: Annotated[
        pint.Quantity | None,
        typer.Option(
            "-d",
            "--distance",
            help="Detector distance, in mm",
            parser=parse_quantity("mm", dimensionality="[length]"),
        ),
    ] = None,
    scan_width: Annotated[
        Decimal,
        typer.Option(
            help="Total rotations scan width, in degrees",
            parser=parse_decimal,
            metavar="ANGLE",
        ),
    ] = Decimal(360),
    rotation_increment: Annotated[
        Decimal,
        typer.Option(
            help="Rotation increment per frame, in degrees",
            parser=parse_decimal,
            metavar="ANGLE",
        ),
    ] = Decimal("0.1"),
    dry_run: Annotated[
        bool,
        typer.Option(
            help="Print what would happen, without doing anything to the beamline"
        ),
    ] = False,
):
    # Read the bulk params file to get the defaults
    if not (params_file := find_applicable_params("rotation_scan_params.json")):
        print("Error: Could not find default parameters file rotation_scan_params.json")
        sys.exit(1)
    print(f"Loading defaults from {params_file}")
    defaults = json.loads(params_file.read_bytes())

    # Handle overriding defaults where we have requested values
    if transmission is not None:
        defaults["transmission_fractions"] = [transmission.to("").m]
    if exposure_time is not None:
        defaults["exposure_time_s"] = exposure_time.to("s").m
    if distance is not None:
        defaults["detector_distance_mm"] = distance.to("mm").m

    # Scan width... has two places?
    defaults["scan_width_deg"] = scan_width
    if "total_scan_width_deg" in defaults:
        del defaults["total_scan_width_deg"]
    defaults["rotation_increment_deg"] = rotation_increment
    defaults["storage_directory"] = str(storage_directory)
    defaults["file_name"] = name

    # Create the parameters object
    params = MultiRotationScanByTransmissions.model_validate(defaults)
    print(f"Created scan parameters object: {params!r}\n")

    frames = int(params.scan_width_deg / params.rotation_increment_deg)
    table = Table(title="Rotation Collection Parameters")
    table.add_column("Parameter", justify="right", style="cyan", no_wrap=True)
    table.add_column("Value for this collection")
    table.add_row(
        "Scan Width",
        f"{params.scan_width_deg}° in {params.rotation_increment_deg}° increments",
    )
    table.add_row("Frames", f"{frames}")
    table.add_row("Path", params.storage_directory)
    table.add_row("Name", params.file_name)
    table.add_row(
        "Transmission",
        f"{ureg.Quantity(params.transmission_fractions[0]).to(ureg.percent):3.0f~}",
    )
    table.add_row(
        "Exposure time",
        f"{ureg.Quantity(params.exposure_time_s, ureg.s).to_compact():~}",
    )
    table.add_row(
        "Detector Distance",
        f"{ureg.Quantity(params.detector_distance_mm, ureg.mm).to_compact():~}",
    )
    print(table)
    if dry_run:
        print("Requested dry-run, not doing any beamline actions")
        return

    BL, PREFIX = do_common_bluesky_setup()

    @device_factory()
    def commissioning_jungfrau() -> CommissioningJungfrau:
        return CommissioningJungfrau(
            f"{PREFIX.beamline_prefix}-EA-JFRAU-01:",
            f"{PREFIX.beamline_prefix}-JUNGFRAU-META:FD:",
            AutoMaxIncrementingPathProvider(
                PurePath(params.storage_directory),
                filename=params.file_name,
                dated=True,
            ),  # type: ignore
        )

    async def create_rotation_composite() -> RotationScanComposite:
        with init_devices():
            aperture = i24.aperture()
            attenuator = i24.attenuator()
            jungfrau = commissioning_jungfrau()
            gonio = i24.vgonio()
            synchrotron = i24.synchrotron()
            sample_shutter = i24.sample_shutter()
            zebra = i24.zebra()
            hutch_shutter = i24.shutter()
            beamstop = i24.beamstop()
            det_stage = i24.detector_motion()
            backlight = i24.backlight()
            dcm = i24.dcm()
        return RotationScanComposite(
            aperture,
            attenuator,
            jungfrau,
            gonio,
            synchrotron,
            sample_shutter,
            zebra,
            hutch_shutter,
            beamstop,
            det_stage,
            backlight,
            dcm,
        )

    async def do_plan():
        RE = RunEngine()
        devices = await create_rotation_composite()
        RE(
            multi_rotation_plan_varying_transmission(
                devices,
                params,
            )
        )

    asyncio.run(do_plan())

    print(table)

    # AutoMaxIncrementingPathProvider


# import re

# def _get_highest_number_from(path: Path) -> int:
#     highest_number = 0
#     candidates = [
#         x for x in path.iterdir() if x.is_dir() and re.match(r"^\d+_", x.name)
#     ]
#     if candidates:
#         highest_number = max(
#             int(x.name.split("_", maxsplit=1)[0]) for x in candidates
#         )
#     print(f"Found highest existing number: {highest_number} in {path}")
#     return highest_number

import shlex


def run(*args):
    cmd = [str(x) for x in args]
    print("+ " + shlex.join(cmd))
    subprocess.run(cmd, check=True)


@app.command()
def fudge_darks(
    exposure_time: Annotated[
        pint.Quantity,
        typer.Argument(
            parser=parse_quantity("s", dimensionality="[time]"),
            metavar="TIME",
            help="Exposure time per frame. Either seconds, or a shorthand e.g. '1ms'",
        ),
    ],
    storage_directory: Annotated[
        Path, typer.Option("-o", "--output", help="Output directory")
    ] = DEFAULT_STORAGE,
    gainmode: GainMode = GainMode.DYNAMIC,
    frames: Annotated[int, typer.Argument()] = 1000,
    period: Annotated[
        pint.Quantity | None,
        typer.Option(
            "-p",
            "--period",
            parser=parse_quantity("s", dimensionality="[time]"),
            metavar="TIME",
            help="Separately specified period from exposure time. If set, this will be used as the gap between frames, instead of defaulting to the same as exposure time.",
        ),
    ] = None,
    raw: bool = True,
):
    """Collect dark images, in a specific gain mode"""
    str_exp = f"{exposure_time:~}".replace(" ", "").replace("µ", "u")
    file_name = f"dark_{str_exp}_{str(gainmode).lower()}" + ("_raw" if raw else "")
    provider = AutoMaxIncrementingPathProvider(
        str(storage_directory),
        filename=file_name,
        dated=True,
    )

    target_info = provider()
    target = Path(target_info.directory_path) / target_info.filename
    print(target)
    run("morgul", "set", "path", target.parent)
    run("morgul", "set", "name", target.name)
    run("morgul", "set", "frames", frames)
    run("sls_detector_put", "frames", frames)
    run("sls_detector_put", "exptime", f"{exposure_time.to(ureg.s).m:.6f}")
    if period:
        run("sls_detector_put", "period", f"{period.to(ureg.s).m:.6f}")
    if raw:
        run("sls_detector_put", "rx_jsonpara", "raw", "true")
    else:
        run("sls_detector_put", "rx_jsonpara", "raw")
    run("sls_detector_put", "gainmode", str(gainmode).lower())

    run("morgul", "get")
    run("sls_detector_acquire")
    time.sleep(10)


if __name__ == "__main__":
    hostname = socket.gethostname()
    if not hostname.startswith("i24-ws"):
        print(
            "[red]Error:[/red] For safety reasons, this may only be run on an I24 workstation"
        )
        sys.exit(1)

    app()
