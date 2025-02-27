import itertools
import textwrap
import warnings
from typing import TYPE_CHECKING

import qcodes.validators as vals
from qcodes.instrument import VisaInstrument, VisaInstrumentKWArgs
from qcodes.parameters import Parameter, create_on_off_val_mapping

if TYPE_CHECKING:
    from typing_extensions import Unpack


class Keithley3706AUnknownOrEmptySlot(Exception):
    pass


class Keithley3706AInvalidValue(Exception):
    pass


class Keithley3706A(VisaInstrument):
    """
    This is the QCoDeS instrument driver for the Keithley 3706A-SNFP
    System Switch.
    """

    default_terminator = "\n"

    def __init__(
        self,
        name: str,
        address: str,
        **kwargs: "Unpack[VisaInstrumentKWArgs]",
    ) -> None:
        """
        Args:
            name: Name to use internally in QCoDeS
            address: VISA resource address
            **kwargs: kwargs are forwarded to base class.

        """
        super().__init__(name, address, **kwargs)

        self.channel_connect_rule: Parameter = self.add_parameter(
            "channel_connect_rule",
            get_cmd=self._get_channel_connect_rule,
            set_cmd=self._set_channel_connect_rule,
            docstring=textwrap.dedent(
                """\
                                    Controls the connection rule for closing
                                    and opening channels when using
                                    `exclusive_close` and `exclusive_slot_close`
                                    parameters.

                                    If it is set to break before make,
                                    it is ensured that all channels open
                                    before any channels close.

                                    If it is set to make before break, it is
                                    ensured that all channels close before any
                                    channels open.

                                    If it is off, channels open and close
                                    simultaneously."""
            ),
            vals=vals.Enum("BREAK_BEFORE_MAKE", "MAKE_BEFORE_BREAK", "OFF"),
        )
        """
        Controls the connection rule for closing and opening channels when using
        `exclusive_close` and `exclusive_slot_close`
        parameters.

        If it is set to break before make, it is ensured that all channels open
        before any channels close.

        If it is set to make before break, it is ensured that all channels close
        before any channels open.

        If it is off, channels open and close simultaneously.
        """

        self.gpib_enabled: Parameter = self.add_parameter(
            "gpib_enabled",
            get_cmd=self._get_gpib_status,
            set_cmd=self._set_gpib_status,
            docstring="Enables or disables GPIB connection.",
            val_mapping=create_on_off_val_mapping(on_val="true", off_val="false"),
        )
        """Enables or disables GPIB connection."""

        self.gpib_address: Parameter = self.add_parameter(
            "gpib_address",
            get_cmd=self._get_gpib_address,
            get_parser=int,
            set_cmd=self._set_gpib_address,
            docstring="Sets and gets the GPIB address.",
            vals=vals.Ints(1, 30),
        )
        """Sets and gets the GPIB address."""

        self.lan_enabled: Parameter = self.add_parameter(
            "lan_enabled",
            get_cmd=self._get_lan_status,
            set_cmd=self._set_lan_status,
            docstring="Enables or disables LAN connection.",
            val_mapping=create_on_off_val_mapping(on_val="true", off_val="false"),
        )
        """Enables or disables LAN connection."""

        self.connect_message()

    def reset_channel(self, val: str) -> None:
        """
        Resets the specified channels to factory default settings.

        Args:
            val: A string representing the channels, channel ranges,
                backplane relays, slots or channel patterns to be queried.

        """
        if not self._validator(val):
            raise Keithley3706AInvalidValue(
                f"{val} is not a valid specifier. "
                "The specifier should be channels, channel "
                'ranges, slots, backplane relays or "allslots".'
            )
        self.write(f"channel.reset('{val}')")

    def open_channel(self, val: str) -> None:
        """
        Opens the specified channels and backplane relays.

        Args:
            val: A string representing the channels, channel ranges,
                backplane relays, slots or channel patterns to be queried.

        """

        if not self._validator(val):
            raise Keithley3706AInvalidValue(
                f"{val} is not a valid specifier. "
                "The specifier should be channels, channel "
                'ranges, slots, backplane relays or "allslots".'
            )
        self.write(f"channel.open('{val}')")

    def close_channel(self, val: str) -> None:
        """
        Closes the channels and backplane relays.

        Args:
            val: A string representing the channels, channel ranges,
                backplane relays to be queried.

        """
        slots = ["allslots", *self._get_slot_names()]
        forbidden_channels = self.get_forbidden_channels("allslots")
        if val in slots:
            raise Keithley3706AInvalidValue("Slots cannot be closed all together.")
        if not self._validator(val):
            raise Keithley3706AInvalidValue(
                f"{val} is not a valid specifier. "
                "The specifier should be channels or channel "
                "ranges and associated backplane relays."
            )
        if val in forbidden_channels.split(","):
            warnings.warn(
                "You are attempting to close channels that are forbidden to close.",
                UserWarning,
                2,
            )

        self._warn_on_disengaged_interlocks(val)

        self.write(f"channel.close('{val}')")

    def _warn_on_disengaged_interlocks(self, val: str) -> None:
        """
        Checks if backplance channels among the given specifiers can be
        energized dependening on respective hardware interlocks being
        engaged, and raises a warning for those backplane channels which
        cannot be energized.

        Args:
            val: A string representing the channels, channel ranges,
                backplane relays.

        """
        states = self.get_interlock_state()
        val_specifiers = val.split(",")
        for channel in val_specifiers:
            if self._is_backplane_channel(channel):
                slot = channel[0]
                interlock_state = next(
                    state for state in states if state["slot_no"] == slot
                )
                if (
                    interlock_state["state"]
                    == "Interlocks 1 and 2 are disengaged on the card"
                ):
                    warnings.warn(
                        f"The hardware interlocks in Slot "
                        f"{interlock_state['slot_no']} are disengaged. "
                        f"The analog backplane relay {channel} "
                        "cannot be energized.",
                        UserWarning,
                        2,
                    )

    def _is_backplane_channel(self, channel_id: str) -> bool:
        if len(channel_id) != 4:
            raise Keithley3706AInvalidValue(f"{channel_id} is not a valid channel id")
        if channel_id[1] == "9":
            return True
        return False

    def exclusive_close(self, val: str) -> None:
        """
        Closes the specified channels such that any presently closed channels
        opens if they are not in the specified by the parameter.

        Args:
            val: A string representing the channels, channel ranges,
                backplane relays to be queried.

        """
        slots = ["allslots", *self._get_slot_names()]
        if val in slots:
            raise Keithley3706AInvalidValue("Slots cannot be exclusively closed.")
        if val == "":
            raise Keithley3706AInvalidValue(
                "An empty string may cause all channels and "
                "associated backplane relays to open. Use "
                '"open_channel" parameter instead.'
            )
        if not self._validator(val):
            raise Keithley3706AInvalidValue(
                f"{val} is not a valid specifier. "
                "The specifier should be channels or channel "
                "ranges and associated backplane relays."
            )
        self.write(f"channel.exclusiveclose('{val}')")

    def exclusive_slot_close(self, val: str) -> None:
        """
        Closes the specified channels on the associated slots abd opens any
        other channels if they are not specified by the parameter.

        Args:
            val: A string representing the channels, channel ranges,
                backplane relays to be queried.

        """
        slots = ["allslots", *self._get_slot_names()]
        if val in slots:
            raise Keithley3706AInvalidValue("Slots cannot be exclusively closed.")
        if val == "":
            raise Keithley3706AInvalidValue("Argument cannot be an empty string.")
        if not self._validator(val):
            raise Keithley3706AInvalidValue(
                f"{val} is not a valid specifier. "
                "The specifier should be channels or channel "
                "ranges and associated backplane relays."
            )
        self.write(f"channel.exclusiveslotclose('{val}')")

    def _get_channel_connect_rule(self) -> str:
        connect_rule = {1: "BREAK_BEFORE_MAKE", 2: "MAKE_BEFORE_BREAK", 0: "OFF"}
        rule = self.ask("channel.connectrule")
        return connect_rule[int(float(rule))]

    def _set_channel_connect_rule(self, val: str) -> None:
        self.write(f"channel.connectrule = channel.{val}")

    def _get_gpib_status(self) -> str:
        return self.ask("comm.gpib.enable")

    def _set_gpib_status(self, val: str | bool) -> None:
        self.write(f"comm.gpib.enable = {val}")

    def _get_lan_status(self) -> str:
        return self.ask("comm.lan.enable")

    def _set_lan_status(self, val: str | bool) -> None:
        self.write(f"comm.lan.enable = {val}")

    def _get_gpib_address(self) -> int:
        return int(float(self.ask("gpib.address")))

    def _set_gpib_address(self, val: int) -> None:
        self.write(f"gpib.address = {val}")

    def get_closed_channels(self, val: str) -> list[str] | None:
        """
        Queries for the closed channels.

        Args:
            val: A string representing the channels,
                backplane relays or channel patterns to be queried.

        """
        if val == "":
            raise Keithley3706AInvalidValue("Argument cannot be an empty string.")
        if not self._validator(val):
            raise Keithley3706AInvalidValue(
                f"{val} is not a valid specifier. "
                "The specifier should be channels, channel "
                'ranges, slots, backplane relays or "allslots".'
            )
        data = self.ask(f"channel.getclose('{val}')")
        if data == "nil":
            return None
        return data.split(";")

    def set_forbidden_channels(self, val: str) -> None:
        """
        Prevents the closing of specified channels and backplane
        relays.

        Args:
            val: A string representing channels and backplane relays
                to make forbidden to close.

        """
        if not self._validator(val):
            raise Keithley3706AInvalidValue(
                f"{val} is not a valid specifier. "
                "The specifier should be channels, channel "
                'ranges, slots, backplane relays or "allslots".'
            )
        self.write(f"channel.setforbidden('{val}')")

    def get_forbidden_channels(self, val: str) -> str:
        """
        Returns a string that lists the channels and backplane relays
        that are forbidden to close.

        Args:
            val: A string representing the channels,
                backplane relays or channel patterns to be queried to see
                if they are forbidden to close.

        """
        if not self._validator(val):
            raise Keithley3706AInvalidValue(
                f"{val} is not a valid specifier. "
                "The specifier should be channels, channel "
                'ranges, slots, backplane relays or "allslots".'
            )
        return self.ask(f"channel.getforbidden('{val}')")

    def clear_forbidden_channels(self, val: str) -> None:
        """
        Clears the list of channels that are forbidden to close.

        Args:
            val: A string representing the channels that will no longer
                be listed as forbidden to close.

        """
        if not self._validator(val):
            raise Keithley3706AInvalidValue(
                f"{val} is not a valid specifier. "
                "The specifier should be channels, channel "
                'ranges, slots, backplane relays or "allslots".'
            )
        self.write(f"channel.clearforbidden('{val}')")

    def set_delay(self, val: str, delay_time: float) -> None:
        """
        Sets an additional delay time for the specified channels.

        Args:
            val: A string representing the channels for which there will
                be an additional delay time.
            delay_time: Delay time for the specified channels in seconds.

        """
        backplanes = self.get_analog_backplane_specifiers()
        specifiers = val.split(",")
        for element in specifiers:
            if element in backplanes:
                raise Keithley3706AInvalidValue(
                    "Additional delay times cannot be set for analog backplane relays."
                )
        if not self._validator(val):
            raise Keithley3706AInvalidValue(
                f"{val} is not a valid specifier. "
                "The specifier should be channels, channel "
                'ranges, slots, or "allslots".'
            )
        self.write(f"channel.setdelay('{val}', {delay_time})")

    def get_delay(self, val: str) -> list[float]:
        """
        Queries for the delay times.

        Args:
            val: A string representing the channels to query for
                additional delay times.

        """
        backplanes = self.get_analog_backplane_specifiers()
        specifiers = val.split(",")
        for element in specifiers:
            if element in backplanes:
                raise Keithley3706AInvalidValue(
                    "Additional delay times cannot be set for analog backplane relays."
                )
        if not self._validator(val):
            raise Keithley3706AInvalidValue(
                f"{val} is not a valid specifier. "
                "The specifier should be channels, channel "
                'ranges, slots, or "allslots".'
            )
        delay_times = [
            float(x) for x in self.ask(f"channel.getdelay('{val}')").split(",")
        ]
        return delay_times

    def set_backplane(self, val: str, backplane: str) -> None:
        """
        Sets the analog backplane relays to use with given channels
        when they are used in switching applications.

        Args:
            val: A string representing the list of channels to change.
            backplane: A string representing the list of analog backplane
                relays to set for the channels specified.

        """
        states = self.get_interlock_state()
        backplanes = self.get_analog_backplane_specifiers()
        plane_specifiers = backplane.split(",")
        val_specifiers = val.split(",")
        for element in states:
            if element["state"] == "Interlocks 1 and 2 are disengaged on the card":
                warnings.warn(
                    f"The hardware interlocks in Slot "
                    f"{element['slot_no']} are disengaged. "
                    "The corresponding analog backplane relays "
                    "cannot be energized.",
                    UserWarning,
                    2,
                )
        for elem in val_specifiers:
            if elem in backplanes:
                raise Keithley3706AInvalidValue(
                    f"{val} is not a valid specifier. "
                    "The specifier cannot be analog "
                    "backplane relay."
                )
        if not self._validator(val):
            raise Keithley3706AInvalidValue(
                f"{val} is not a valid specifier. "
                "The specifier should be channels, channel "
                'ranges, slots, or "allslots".'
            )
        for plane in plane_specifiers:
            if plane not in backplanes:
                raise Keithley3706AInvalidValue(
                    f"{backplane} is not a valid specifier. "
                    "The specifier should be analog "
                    "backplane relay."
                )
        self.write(f"channel.setbackplane('{val}', '{backplane}')")

    def get_backplane(self, val: str) -> str:
        """
        Lists the backplane relays that are controlled in switching
        applications for specified channels.

        Args:
            val: A string representing the channels being queried.

        """
        backplanes = self.get_analog_backplane_specifiers()
        specifiers = val.split(",")
        for element in specifiers:
            if element in backplanes:
                raise Keithley3706AInvalidValue(
                    f"{val} cannot be a analog backplane relay."
                )
        if not self._validator(val):
            raise Keithley3706AInvalidValue(
                f"{val} is not a valid specifier. "
                "The specifier should be channels, channel "
                'ranges, slots, or "allslots".'
            )
        return self.ask(f"channel.getbackplane('{val}')")

    def _get_slot_ids(self) -> list[str]:
        """
        Returns the slot ids of the installed cards.
        """
        cards = self.get_switch_cards()
        slot_id = [f"{card['slot_no']}" for card in cards]
        return slot_id

    def _get_slot_names(self) -> list[str]:
        """
        Returns the names of the slots as "slotX",
        where "X" is the slot id.
        """
        slot_id = self._get_slot_ids()
        slot_names = [f"slot{x}" for x in slot_id]
        return slot_names

    def _get_number_of_rows(self) -> list[int]:
        """
        Returns the total number of rows of the installed cards.
        """
        slot_id = self._get_slot_ids()
        total_number_of_rows = [
            int(float(self.ask(f"slot[{i}].rows.matrix"))) for i in slot_id
        ]
        return total_number_of_rows

    def _get_number_of_columns(self) -> list[int]:
        """
        Returns the total number of columns of the installed cards.
        """
        slot_id = self._get_slot_ids()
        total_number_of_columns = [
            int(float(self.ask(f"slot[{i}].columns.matrix"))) for i in slot_id
        ]
        return total_number_of_columns

    def _get_rows(self) -> list[list[str]]:
        """
        Returns the elements of each row.
        """
        total_number_of_rows = self._get_number_of_rows()
        row_list = []
        for item in total_number_of_rows:
            rows_in_each_slot = [str(i) for i in range(1, item + 1)]
            row_list.append(rows_in_each_slot)
        return row_list

    def _get_columns(self) -> list[list[str]]:
        """
        Returns the elements of each column.
        """
        total_number_of_columns = self._get_number_of_columns()
        column_list = []
        for item in total_number_of_columns:
            columns_in_each_slot = []
            for i in range(1, item + 1):
                if i < 10:
                    columns_in_each_slot.append("0" + str(i))
                else:
                    columns_in_each_slot.append(str(i))
            column_list.append(columns_in_each_slot)
        return column_list

    def _get_channel_ranges(self) -> list[str]:
        """
        A helper function that gets two channel names from the available
        channels list and join them via a colon to define a channel range.
        """
        range_list = []
        for i in self._get_slot_ids():
            channel = self.get_channels_by_slot(int(i))
            for element in itertools.combinations(channel, 2):
                range_list.append(":".join(element))
        return range_list

    def get_channels(self) -> list[str]:
        """
        This function returns the name of the matrix channels.
        User can call this function to see the names of the available
        channels, in case he/she is not familiar with the naming convention.
        However, note that, this is a standalone helper function and
        the usage of channel attributes of the instrument driver does
        not depend on the functionality of this method.
        """
        slot_id = self._get_slot_ids()
        row_list = self._get_rows()
        column_list = self._get_columns()
        matrix_channels = []
        for i, slot in enumerate(slot_id):
            for element in itertools.product(slot, row_list[i], column_list[i]):
                matrix_channels.append("".join(element))
        return matrix_channels

    def get_channels_by_slot(self, slot_no: int) -> list[str]:
        """
        Returns the channel names of a given slot.

        Args:
            slot_no: An integer value specifying the slot number.

        """
        slot_id = self._get_slot_ids()
        if str(slot_no) not in slot_id:
            raise Keithley3706AUnknownOrEmptySlot(
                "Please provide a valid slot identifier. "
                f"Available slots are {slot_id}."
            )
        row_list = self._get_rows()
        column_list = self._get_columns()
        matrix_channels_by_slot = []
        for element in itertools.product(str(slot_no), row_list[0], column_list[0]):
            matrix_channels_by_slot.append("".join(element))
        return matrix_channels_by_slot

    def get_analog_backplane_specifiers(self) -> list[str]:
        """
        Returns a list of comma separated strings representing available analog
        backplane relays. This function should not be mixed with the
        `get_backplane` method. The latter returns backplane relays which are
        associated with a channel by using `set_backplane` method.
        """
        backplane_common_number = "9"
        backplane_relay_common_numbers = ["11", "12", "13", "14", "15", "16"]
        slot_id = self._get_slot_ids()
        analog_backplane_relays = []
        for element in itertools.product(
            slot_id, backplane_common_number, backplane_relay_common_numbers
        ):
            analog_backplane_relays.append("".join(element))
        return analog_backplane_relays

    def _connect_or_disconnect_row_to_columns(
        self, action: str, slot_id: int, row_id: int, columns: list[int]
    ) -> list[str]:
        """
        A private function that connects or (disconnects) given columns
        to (from) a row of a slot and opens (closes) the formed channels.
        """
        if action not in ["connect", "disconnect"]:
            raise ValueError(
                "The action should be identified as either 'connect' or 'disconnect'."
            )
        slots = self._get_slot_ids()
        slot = str(slot_id)
        if slot not in slots:
            raise Keithley3706AUnknownOrEmptySlot(
                f"Please provide a valid slot identifier. Available slots are {slots}."
            )
        row = str(row_id)
        columns_list = []
        for i in columns:
            if i < 10:
                columns_list.append("0" + str(i))
            else:
                columns_list.append(str(i))
        channels_to_connect_or_disconnect = []
        for element in itertools.product(slot, row, columns_list):
            channels_to_connect_or_disconnect.append("".join(element))
        for channel in channels_to_connect_or_disconnect:
            if action == "connect":
                self.open_channel(channel)
            else:
                self.close_channel(channel)
        return channels_to_connect_or_disconnect

    def _connect_or_disconnect_column_to_rows(
        self, action: str, slot_id: int, column_id: int, rows: list[int]
    ) -> list[str]:
        """
        A private function that connects (disconnects) given rows
        to (from) a column of a slot and opens (closes) the formed channels.
        """
        if action not in ["connect", "disconnect"]:
            raise ValueError(
                "The action should be identified as either 'connect' or 'disconnect'."
            )
        slots = self._get_slot_ids()
        slot = str(slot_id)
        if slot not in slots:
            raise Keithley3706AUnknownOrEmptySlot(
                f"Please provide a valid slot identifier. Available slots are {slots}."
            )
        column = []
        if column_id < 10:
            column.append("0" + str(column_id))
        else:
            column.append(str(column_id))
        rows_list = [str(x) for x in rows]
        channels_to_connect_or_disconnect = []
        for element in itertools.product(slot, rows_list, column):
            channels_to_connect_or_disconnect.append("".join(element))
        for channel in channels_to_connect_or_disconnect:
            if action == "connect":
                self.open_channel(channel)
            else:
                self.close_channel(channel)
        return channels_to_connect_or_disconnect

    def connect_row_to_columns(
        self, slot_id: int, row_id: int, columns: list[int]
    ) -> list[str]:
        """
        A convenient function that connects given columns to a row of a
        slot and opens the formed channels.

        Args:
            slot_id: The specifier for the slot from which the row and columns
                will be selected.
            row_id: The specifier for the row to which the provided columns
                will be connected.
            columns: The specifiers of the columns will be connected to the
                provided row.

        """
        return self._connect_or_disconnect_row_to_columns(
            "connect", slot_id, row_id, columns
        )

    def disconnect_row_from_columns(
        self, slot_id: int, row_id: int, columns: list[int]
    ) -> list[str]:
        """
        A convenient function that disconnects given columns to a row of a
        slot and closes the formed channels.

        Args:
            slot_id: The specifier for the slot from which the row and columns
                will be selected.
            row_id: The specifier for the row to which the provided columns
                will be disconnected.
            columns: The specifiers of the columns will be disconnected from the
                provided row.

        """
        return self._connect_or_disconnect_row_to_columns(
            "disconnect", slot_id, row_id, columns
        )

    def connect_column_to_rows(
        self, slot_id: int, column_id: int, rows: list[int]
    ) -> list[str]:
        """
        A convenient function that connects given rows to a column of a
        slot and opens the formed channels.

        Args:
            slot_id: The specifier for the slot from which the row and columns
                will be selected.
            column_id: The specifier for the column to which the provided rows
                will be connected.
            rows: The specifiers of the rows will be connected to the
                provided column.

        """
        return self._connect_or_disconnect_column_to_rows(
            "connect", slot_id, column_id, rows
        )

    def disconnect_column_from_rows(
        self, slot_id: int, column_id: int, rows: list[int]
    ) -> list[str]:
        """
        A convenient function that disconnects given rows to a column of a
        slot and closes the formed channels.

        Args:
            slot_id: The specifier for the slot from which the row and columns
                will be selected.
            column_id: The specifier for the column to which the provided rows
                will be disconnected.
            rows: The specifiers of the rows will be disconnected from the
                provided column.

        """
        return self._connect_or_disconnect_column_to_rows(
            "disconnect", slot_id, column_id, rows
        )

    def get_idn(self) -> dict[str, str | None]:
        """
        Overwrites the generic QCoDeS get IDN method. Returns
        a dictionary including the vendor, model, serial number and
        firmware version of the instrument.
        """
        idnstr = self.ask_raw("*IDN?")
        vendor, model, serial, firmware = map(str.strip, idnstr.split(","))
        model = model[6:]

        idn: dict[str, str | None] = {
            "vendor": vendor,
            "model": model,
            "serial": serial,
            "firmware": firmware,
        }
        return idn

    def get_switch_cards(self) -> tuple[dict[str, str], ...]:
        """
        Returns a list of dictionaries listing the properties of the installed
        switch cards including the slot number tha it is installed, model,
        firmware version and serial number.
        """
        switch_cards: list[dict[str, str]] = []
        for i in range(1, 7):
            scard = self.ask(f"slot[{i}].idn")
            if scard != "Empty Slot":
                model, mtype, firmware, serial = map(str.strip, scard.split(","))
                sdict = {
                    "slot_no": str(i),
                    "model": model,
                    "mtype": mtype,
                    "firmware": firmware,
                    "serial": serial,
                }
                switch_cards.append(sdict)
        return tuple(switch_cards)

    def get_available_memory(self) -> dict[str, str | None]:
        """
        Returns the amount of memory that is currently available for
        storing scripts, configurations and channel patterns.
        """
        memstring = self.ask("memory.available()")
        system_memory, script_memory, pattern_memory, config_memory = map(
            str.strip, memstring.split(",")
        )

        memory_available: dict[str, str | None] = {
            "System Memory  (%)": system_memory,
            "Script Memory  (%)": script_memory,
            "Pattern Memory (%)": pattern_memory,
            "Config Memory  (%)": config_memory,
        }
        return memory_available

    def get_interlock_state(self) -> tuple[dict[str, str], ...]:
        """
        A function that collects the interlock status of the installed cards.
        The channel relays can continue to operate even if the interlock
        in the corresponding slot is disengaged, one cannot perform
        measurements through the switching card, as the analog backplanes
        cannot be energized.
        """
        slot_id = self._get_slot_ids()
        interlock_status = {
            None: (
                "No card is installed or the installed card does not support interlocks"
            ),
            0: "Interlocks 1 and 2 are disengaged on the card",
            1: "Interlock 1 is engaged, interlock 2 (if it exists) is disengaged",
            2: "Interlock 2 in engaged, interlock 1 is disengaged",
            3: "Both interlock 1 and 2 are engaged",
        }
        states: list[dict[str, str]] = []
        for i in slot_id:
            state = self.get_interlock_state_by_slot(i)
            states.append({"slot_no": i, "state": interlock_status[state]})
        return tuple(states)

    def get_interlock_state_by_slot(self, slot: str | int) -> int | None:
        state = self.ask(f"slot[{int(slot)}].interlock.state")
        if state == "nil":
            return None
        else:
            return int(float(state))

    def get_ip_address(self) -> str:
        """
        Returns the current IP address of the instrument.
        """
        return self.ask("lan.status.ipaddress")

    def reset_local_network(self) -> None:
        """
        Resets the local network (LAN).
        """
        self.write("lan.reset()")

    def save_setup(self, val: str | None = None) -> None:
        """
        Saves the present setup.

        Args:
            val: An optional string representing the path and the file name
                to which the setup shall be saved on a USB flash drive. If not
                provided, the setup will be saved to the nonvolatile memory
                of the instrument, any previous saves will be overwritten.

        """
        if val is not None:
            self.write(f"setup.save('{val}')")
        else:
            self.write("setup.save()")

    def load_setup(self, val: int | str) -> None:
        """
        Loads the settings from a saved setup.

        Args:
            val: An integer or a string that specifies the location of saved
                setup. If it is `0`, factory defaults load. If it is `1`,
                the saved setup from the nonvolatile memory is recalled.
                Otherwise, a string specifying the relative path to the saved
                setup on a USB drive should be passed in.

        """
        self.write(f"setup.recall('{val}')")

    def _validator(self, val: str) -> bool:
        """
        Instrument specific validator. As the number of validation points
        are around 15k, to avoid QCoDeS parameter validation to print them all,
        we shall raise a custom exception.
        """
        ch = self.get_channels()
        ch_range = self._get_channel_ranges()
        slots = ["allslots", *self._get_slot_names()]
        backplanes = self.get_analog_backplane_specifiers()
        specifier = val.split(",")
        for element in specifier:
            if element not in (*ch, *ch_range, *slots, *backplanes):
                return False
        return True

    def connect_message(
        self, idn_param: str = "IDN", begin_time: float | None = None
    ) -> None:
        """
        Overwrites the generic QCoDeS instrument connect message.
        Here, additionally, we provide information about
        which slots of the system switch is occupied with what
        kind of matrix, as well.
        """
        idn = self.get_idn()
        cards = self.get_switch_cards()
        states = self.get_interlock_state()

        con_msg = (
            "Connected to: {vendor} {model} SYSTEM SWITCH "
            "(serial:{serial}, firmware:{firmware})".format(**idn)
        )
        print(con_msg)
        self.log.info(f"Connected to instrument: {idn}")

        for _, item in enumerate(cards):
            card_info = (
                "Slot {slot_no}- Model:{model}, Matrix Type:{mtype}, "
                "Firmware:{firmware}, Serial:{serial}".format(**item)
            )
            print(card_info)
            self.log.info(f"Switch Cards: {item}")

        for _, item in enumerate(states):
            state_info = "{state} in Slot {slot_no}.".format(**item)
            print(state_info)

    def ask(self, cmd: str) -> str:
        """
        Override of normal ask. This is important, since queries to the
        instrument must be wrapped in 'print()'
        """
        return super().ask(f"print({cmd:s})")
