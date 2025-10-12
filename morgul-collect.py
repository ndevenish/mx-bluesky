#!/usr/bin/env -S uv run

import asyncio
import socket
import sys
from pathlib import Path, PurePath
from typing import Annotated

import pint
import typer
from bluesky.run_engine import RunEngine
from dodal.common.beamlines.beamline_utils import device_factory
from dodal.devices.i24.commissioning_jungfrau import CommissioningJungfrau
from dodal.utils import BeamlinePrefix, get_beamline_name
from ophyd_async.core import AutoMaxIncrementingPathProvider, init_devices
from ophyd_async.fastcs.jungfrau import GainMode
from rich import print

from mx_bluesky.beamlines.i24.jungfrau_commissioning.do_darks import (
    do_pedestal_darks,
    do_standard_darks,
)
from mx_bluesky.beamlines.i24.jungfrau_commissioning.plan_utils import (
    add_info_logs_to_stdout,
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


def parse_time_default_s(value: str) -> pint.Quantity:
    """Parse a string as a pint quantity, defaulting to seconds if no unit"""
    q = pint.Quantity(value)
    if q.unitless:
        q *= ureg.s
    return q


@app.command()
def pedestals(
    exposure_time: Annotated[
        pint.Quantity,
        typer.Argument(
            parser=parse_time_default_s,
            metavar="TIME",
            help="Exposure time per frame. Either seconds, or a shorthand e.g. '1ms'",
        ),
    ],
    storage_directory: Annotated[
        Path, typer.Option("-o", "--output", help="Output directory")
    ] = DEFAULT_STORAGE,
    pedestal_loops: Annotated[
        int, typer.Argument(metavar="N_LOOPS", help="Number of pedestal loops")
    ] = 20,
    pedestal_frames: Annotated[
        int,
        typer.Argument(metavar="N_FRAMES", help="Number of frames per pedestal loop."),
    ] = 200,
    period: Annotated[
        pint.Quantity | None,
        typer.Option(
            "-p",
            "--period",
            parser=parse_time_default_s,
            metavar="TIME",
            help="Separately specified period from exposure time. If set, this will be used as the gap between frames, instead of defaulting to the same as exposure time.",
        ),
    ] = None,
):
    """Run pedestal calibrations"""
    assert period is None, "Period not implemented yet"
    BL, PREFIX = do_common_bluesky_setup()

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
                exposure_time.to("seconds").m, pedestal_frames, pedestal_loops, jf
            )
        )

    print("Running pedestal calibration")
    print(f"Exposure time: {exposure_time.to_compact():~}")

    asyncio.run(do_plan())


@app.command()
def darks(
    exposure_time: Annotated[
        pint.Quantity,
        typer.Argument(
            parser=parse_time_default_s,
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
            parser=parse_time_default_s,
            metavar="TIME",
            help="Separately specified period from exposure time. If set, this will be used as the gap between frames, instead of defaulting to the same as exposure time.",
        ),
    ] = None,
):
    """Collect dark images, in a specific gain mode"""
    assert period is None, "Period not implemented yet"
    BL, PREFIX = do_common_bluesky_setup()

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
        RE(do_standard_darks(gainmode, exposure_time.to("seconds").m, frames, jf))

    asyncio.run(do_plan())


if __name__ == "__main__":
    hostname = socket.gethostname()
    if not hostname.startswith("i24-ws"):
        print(
            "[red]Error:[/red] For safety reasons, this may only be run on an I24 workstation"
        )
        sys.exit(1)

    app()
