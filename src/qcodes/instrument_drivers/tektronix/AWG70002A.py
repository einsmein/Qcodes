from typing import TYPE_CHECKING

from .AWG70000A import AWG70000A

if TYPE_CHECKING:
    from typing_extensions import Unpack

    from qcodes.instrument import VisaInstrumentKWArgs


class TektronixAWG70002A(AWG70000A):
    """
    The QCoDeS driver for Tektronix AWG70002A series AWG's.

    All the actual driver meat is in the superclass AWG70000A.
    """

    default_timeout = 10

    def __init__(
        self,
        name: str,
        address: str,
        **kwargs: "Unpack[VisaInstrumentKWArgs]",
    ) -> None:
        """
        Args:
            name: The name used internally by QCoDeS in the DataSet
            address: The VISA resource name of the instrument
            timeout: The VISA timeout time (in seconds).
            **kwargs: kwargs are forwarded to base class.
        """

        super().__init__(name, address, num_channels=2, **kwargs)


class AWG70002A(TektronixAWG70002A):
    """
    Alias with non-conformant name left for backwards compatibility
    """

    pass
