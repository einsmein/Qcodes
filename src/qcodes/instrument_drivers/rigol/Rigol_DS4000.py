from __future__ import annotations

import logging
import re
import time
import warnings
from typing import TYPE_CHECKING, NamedTuple

import numpy as np
from packaging import version

from qcodes import validators as vals
from qcodes.instrument import (
    ChannelList,
    InstrumentBaseKWArgs,
    InstrumentChannel,
    VisaInstrument,
    VisaInstrumentKWArgs,
)
from qcodes.parameters import ArrayParameter, ParamRawDataType

if TYPE_CHECKING:
    from typing_extensions import Unpack

    from qcodes.parameters import Parameter

log = logging.getLogger(__name__)


class RigolDS4000TraceNotReady(Exception):
    pass


class ScopeArray(ArrayParameter):
    def __init__(
        self,
        name: str,
        instrument: RigolDS4000Channel,
        channel: int,
        raw: bool = False,
    ):
        super().__init__(
            name=name,
            shape=(1400,),
            label="Voltage",
            unit="V",
            setpoint_names=("Time",),
            setpoint_labels=("Time",),
            setpoint_units=("s",),
            docstring="holds an array from scope",
            instrument=instrument,
        )
        self.channel = channel
        self.raw = raw
        self.max_read_step = 50
        self.trace_ready = False

    def prepare_curvedata(self) -> None:
        """
        Prepare the scope for returning curve data
        """
        assert isinstance(self.instrument, RigolDS4000Channel)
        if self.raw:
            self.instrument.write(":STOP")  # Stop acquisition
            self.instrument.write(":WAVeform:MODE RAW")  # Set RAW mode
        else:
            self.instrument.write(":WAVeform:MODE NORM")  # Set normal mode

        self.get_preamble()
        p = self.preamble

        # Generate time axis data
        xdata = np.linspace(
            p.xorigin, p.xorigin + p.xincrement * p.points, int(p.points)
        )
        self.setpoints = (tuple(xdata),)
        self.shape = (int(p.points),)

        self.trace_ready = True

    def get_raw(self) -> ParamRawDataType:
        assert isinstance(self.instrument, RigolDS4000Channel)
        assert isinstance(self.root_instrument, RigolDS4000)
        if not self.trace_ready:
            raise RigolDS4000TraceNotReady(
                "Please run prepare_curvedata to prepare the scope for giving a trace."
            )
        else:
            self.trace_ready = False

        # Set the data type for waveforms to "BYTE"
        self.instrument.write(":WAVeform:FORMat BYTE")
        # Set read channel
        self.instrument.write(f":WAVeform:SOURce CHAN{self.channel}")

        data_bin = bytearray()
        if self.raw:
            log.info("Readout of raw waveform started, %g points", self.shape[0])
            # Ask for the right number of points
            self.instrument.write(f":WAVeform:POINts {self.shape[0]}")
            # Resets the waveform data reading
            self.instrument.write(":WAVeform:RESet")
            # Starts the waveform data reading
            self.instrument.write(":WAVeform:BEGin")

            for i in range(self.max_read_step):
                status = self.instrument.ask(":WAVeform:STATus?").split(",")[0]

                # Ask and retrieve waveform data
                # It uses .read_raw() to get a byte
                # string since our data is binary
                self.instrument.write(":WAVeform:DATA?")
                data_chunk = self.root_instrument.visa_handle.read_raw()
                data_chuck = self._validate_strip_block(data_chunk)
                data_bin.extend(data_chuck)

                if status == "IDLE":
                    self.instrument.write(":WAVeform:END")
                    break
                else:
                    # Wait some time to have the buffer re-filled
                    time.sleep(0.3)
                log.info(
                    "chucks read: %d, last chuck points: %g, total read size: %g",
                    i,
                    len(data_chuck),
                    len(data_bin),
                )
            else:
                raise ValueError("Communication error")
        else:
            # Ask and retrieve waveform data
            # It uses .read_raw() to get a byte string since our data is binary
            log.info("Readout of display waveform started, %d points", self.shape[0])
            self.instrument.write(":WAVeform:DATA?")  # Query data
            data_chunk = self.root_instrument.visa_handle.read_raw()
            data_bin.extend(self._validate_strip_block(data_chunk))

        log.info("Readout ended, total read size: %g", len(data_bin))

        log.info("Data conversion")
        # Convert data to byte array
        data_raw = np.frombuffer(data_bin, dtype=np.uint8).astype(float)

        # Convert byte array to real data
        p = self.preamble
        data = (data_raw - p.yreference - p.yorigin) * p.yincrement
        log.info("Data conversion done")

        return data

    @staticmethod
    def _validate_strip_block(block: bytes) -> bytes:
        """
        Given a block of raw data from the instrument, validate and
        then strip the header with
        size information. Raise ValueError if the sizes don't match.

        Args:
            block: The data block
        Returns:
            The stripped data

        """
        # Validate header
        header = block[:11].decode("ascii")
        match = re.match(r"#9(\d{9})", header)
        if match:
            size = int(match[1])
            block_nh = block[11:]  # Strip header
            block_nh = block_nh.strip()  # Strip \n

            if size == len(block_nh):
                return block_nh

        raise ValueError("Malformed data")

    def get_preamble(self) -> None:
        assert isinstance(self.instrument, RigolDS4000Channel)

        # count unfortunately overrides a method on tuple
        # we leave it as is for backwards compatibility
        class PreambleNT(NamedTuple):
            format: float
            mode: float
            points: float
            count: float  # type: ignore[assignment]
            xincrement: float
            xorigin: float
            xreference: float
            yincrement: float
            yorigin: float
            yreference: float

        def conv(x: str) -> float:
            return int(x) if x.isdigit() else float(x)

        preamble_raw = self.instrument.ask(":WAVeform:PREamble?")
        preamble_num = [conv(x) for x in preamble_raw.strip().split(",")]
        self.preamble = PreambleNT(*preamble_num)


class RigolDS4000Channel(InstrumentChannel):
    def __init__(
        self,
        parent: RigolDS4000,
        name: str,
        channel: int,
        **kwargs: Unpack[InstrumentBaseKWArgs],
    ):
        super().__init__(parent, name, **kwargs)

        self.amplitude: Parameter = self.add_parameter(
            "amplitude", get_cmd=f":MEASure:VAMP? chan{channel}", get_parser=float
        )
        """Parameter amplitude"""
        self.vertical_scale: Parameter = self.add_parameter(
            "vertical_scale",
            get_cmd=f":CHANnel{channel}:SCALe?",
            set_cmd=":CHANnel{}:SCALe {}".format(channel, "{}"),
            get_parser=float,
        )
        """Parameter vertical_scale"""

        # Return the waveform displayed on the screen
        self.curvedata: ScopeArray = self.add_parameter(
            "curvedata", channel=channel, parameter_class=ScopeArray, raw=False
        )
        """Parameter curvedata"""

        # Return the waveform in the internal memory
        self.curvedata_raw: ScopeArray = self.add_parameter(
            "curvedata_raw", channel=channel, parameter_class=ScopeArray, raw=True
        )
        """Parameter curvedata_raw"""


class RigolDS4000(VisaInstrument):
    """
    This is the QCoDeS driver for the Rigol DS4000 series oscilloscopes.
    """

    default_timeout = 20

    def __init__(
        self,
        name: str,
        address: str,
        **kwargs: Unpack[VisaInstrumentKWArgs],
    ):
        """
        Initialises the DS4000.

        Args:
            name: Name of the instrument used by QCoDeS
            address: Instrument address as used by VISA
            **kwargs: kwargs are forwarded to base class.

        """

        # Init VisaInstrument. device_clear MUST NOT be issued, otherwise communications hangs
        # due a bug in firmware
        kwargs["device_clear"] = False

        super().__init__(name, address, **kwargs)
        self.connect_message()

        self._check_firmware_version()

        # functions
        self.add_function("run", call_cmd=":RUN", docstring="Start acquisition")
        self.add_function("stop", call_cmd=":STOP", docstring="Stop acquisition")
        self.add_function(
            "single", call_cmd=":SINGle", docstring="Single trace acquisition"
        )
        self.add_function(
            "force_trigger", call_cmd="TFORce", docstring="Force trigger event"
        )
        self.add_function(
            "auto_scale", call_cmd=":AUToscale", docstring="Perform autoscale"
        )

        # general parameters
        self.trigger_type: Parameter = self.add_parameter(
            "trigger_type",
            label="Type of the trigger",
            get_cmd=":TRIGger:MODE?",
            set_cmd=":TRIGger:MODE {}",
            vals=vals.Enum(
                "EDGE",
                "PULS",
                "RUNT",
                "NEDG",
                "SLOP",
                "VID",
                "PATT",
                "RS232",
                "IIC",
                "SPI",
                "CAN",
                "FLEX",
                "USB",
            ),
        )
        """Parameter trigger_type"""
        self.trigger_mode: Parameter = self.add_parameter(
            "trigger_mode",
            label="Mode of the trigger",
            get_cmd=":TRIGger:SWEep?",
            set_cmd=":TRIGger:SWEep {}",
            vals=vals.Enum("AUTO", "NORM", "SING"),
        )
        """Parameter trigger_mode"""
        self.time_base: Parameter = self.add_parameter(
            "time_base",
            label="Horizontal time base",
            get_cmd=":TIMebase:MAIN:SCALe?",
            set_cmd=":TIMebase:MAIN:SCALe {}",
            get_parser=float,
            unit="s/div",
        )
        """Parameter time_base"""
        self.sample_point_count: Parameter = self.add_parameter(
            "sample_point_count",
            label="Number of the waveform points",
            get_cmd=":WAVeform:POINts?",
            set_cmd=":WAVeform:POINts {}",
            get_parser=int,
            vals=vals.Ints(min_value=1),
        )
        """Parameter sample_point_count"""
        self.enable_auto_scale: Parameter = self.add_parameter(
            "enable_auto_scale",
            label="Enable or disable autoscale",
            get_cmd=":SYSTem:AUToscale?",
            set_cmd=":SYSTem:AUToscale {}",
            get_parser=bool,
            vals=vals.Bool(),
        )
        """Parameter enable_auto_scale"""

        channels = ChannelList(self, "Channels", RigolDS4000Channel, snapshotable=False)

        for channel_number in range(1, 5):
            channel = RigolDS4000Channel(self, f"ch{channel_number}", channel_number)
            channels.append(channel)

        self.add_submodule("channels", channels.to_channel_tuple())

    def _check_firmware_version(self) -> None:
        # Require version 00.02.03

        idn = self.get_idn()
        verstr = idn["firmware"]
        if verstr is None:
            raise RuntimeError("Could not determine firmware version of DS4000.")
        ver = version.parse(verstr)
        if ver < version.parse("00.02.03"):
            warnings.warn(
                "Firmware version should be at least 00.02.03,"
                "data transfer may not work correctly"
            )
