"""
Cleaner abstractions of the PV table.

Takes the PV tables from I24's setup_beamline and wraps a slightly more
abstract wrapper around them.
"""

from mx_bluesky.beamlines.i24.serial.setup_beamline import pv


class EigerPVs:
    """The Eiger PVs the collection plans read back from.

    What the plans caput to lives in setup_beamline.eiger for now. Both belong on an
    ophyd-async device, see https://github.com/DiamondLightSource/mx-bluesky/issues/62.
    """

    detector_distance = pv.eiger_detdist
    wavelength = pv.eiger_wavelength
    transmission = "BL24I-EA-PILAT-01:cam1:FilterTransm"
    filename_rbv = pv.eiger_od_filename_rbv
    frames_captured = pv.eiger_od_num_captured_rbv
    file_name = pv.eiger_od_filename
    file_path = pv.eiger_od_filepath
    file_template = None
    sequence_id = pv.eiger_seq_id
    beamx = pv.eiger_beamx
    beamy = pv.eiger_beamy
    bit_depth = pv.eiger_bitdepthrbv
