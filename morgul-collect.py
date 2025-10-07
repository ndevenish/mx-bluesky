#!/usr/bin/env python3

import asyncio
import socket
import sys
from pathlib import Path, PurePath
from typing import Annotated

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

BL = get_beamline_name("i24")
PREFIX = BeamlinePrefix(BL)

do_default_logging_setup("i24-bluesky.log", 12231)  # Dodal graylog stream
add_info_logs_to_stdout(LOGGER)


@app.command()
def pedestals(
    exposure_time_s: float,
    storage_directory: Annotated[
        Path, typer.Option("-o", "--output", help="Output directory")
    ] = DEFAULT_STORAGE,
    pedestal_loops: Annotated[int, typer.Argument()] = 20,
    pedestal_frames: Annotated[int, typer.Argument()] = 200,
):
    """Run pedestal calibrations"""

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
        RE(do_pedestal_darks(exposure_time_s, pedestal_frames, pedestal_loops, jf))

    asyncio.run(do_plan())


@app.command()
def darks(
    exposure_time_s: float,
    storage_directory: Annotated[
        Path, typer.Option("-o", "--output", help="Output directory")
    ] = DEFAULT_STORAGE,
    gainmode: GainMode = GainMode.DYNAMIC,
    frames: Annotated[int, typer.Argument()] = 1000,
):
    """Collect dark images, in a specific gain mode"""

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
        RE(do_standard_darks(gainmode, exposure_time_s, frames, jf))

    asyncio.run(do_plan())


if __name__ == "__main__":
    hostname = socket.gethostname()
    if not hostname.startswith("i24-ws"):
        print(
            "[red]Error:[/red] For safety reasons, this may only be run on an I24 workstation"
        )
        sys.exit(1)

    app()
