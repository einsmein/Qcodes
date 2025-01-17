from typing import TYPE_CHECKING

from .private.DP8xx import _RigelDP8xx

if TYPE_CHECKING:
    from typing_extensions import Unpack

    from qcodes.instrument import VisaInstrumentKWArgs


class RigolDP831(_RigelDP8xx):
    """
    This is the qcodes driver for the Rigol DP831(A) Power Supply
    """

    def __init__(
        self, name: str, address: str, **kwargs: "Unpack[VisaInstrumentKWArgs]"
    ):
        channel_ranges = [(8.0, 5.0), (30.0, 2.0), (-30.0, 2.0)]

        ovp_ranges_std = [(0.01, 8.8), (0.01, 33.0), (-0.01, -33.0)]
        ocp_ranges_std = [(0.001, 5.5), (0.001, 2.2), (0.001, 2.2)]

        ovp_ranges_precision = [(0.001, 8.8), (0.001, 33.0), (-0.001, -33.0)]
        ocp_ranges_precision = [(0.0001, 5.5), (0.0001, 2.2), (0.0001, 2.2)]

        ovp = (ovp_ranges_std, ovp_ranges_precision)
        ocp = (ocp_ranges_std, ocp_ranges_precision)

        super().__init__(name, address, channel_ranges, ovp, ocp, **kwargs)
