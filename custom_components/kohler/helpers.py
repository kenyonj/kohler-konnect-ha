"""Shared helpers for building Kohler Anthem valve commands.

The valve protocol notes live with :func:`build_off_control`; the constants
here are shared by the water_heater, select, and number platforms so they all
speak the same 4-byte-per-valve dialect.
"""

from __future__ import annotations

from kohler_anthem import encode_valve_command
from kohler_anthem.models import DeviceState, ValveControlModel, ValveMode, ValvePrefix

# Maps the API's valveIndex names to the solowritesystem payload field and the
# valve-prefix byte the firmware expects in each 4-byte command.
VALVE_FIELD_AND_PREFIX = {
    "Valve1": ("primary_valve1", ValvePrefix.PRIMARY),
    "Valve2": ("secondary_valve1", ValvePrefix.SECONDARY_1),
    "Valve3": ("secondary_valve2", ValvePrefix.SECONDARY_2),
    "Valve4": ("secondary_valve3", ValvePrefix.SECONDARY_3),
    "Valve5": ("secondary_valve4", ValvePrefix.SECONDARY_4),
    "Valve6": ("secondary_valve5", ValvePrefix.SECONDARY_5),
    "Valve7": ("secondary_valve6", ValvePrefix.SECONDARY_6),
    "Valve8": ("secondary_valve7", ValvePrefix.SECONDARY_7),
}

# encode_valve_command's accepted Celsius range.
ENCODE_TEMP_MIN_C, ENCODE_TEMP_MAX_C = 15.0, 49.0


def to_celsius(value: float, unit: str) -> float:
    """Convert an account-unit temperature to Celsius for library writes."""
    if unit == "Fahrenheit":
        return (value - 32.0) * 5.0 / 9.0
    return value


def clamp_encode_temp(temp_c: float) -> float:
    """Clamp a Celsius value into encode_valve_command's accepted range."""
    return min(max(temp_c, ENCODE_TEMP_MIN_C), ENCODE_TEMP_MAX_C)


def build_off_control(state: DeviceState | None, temp_c: float) -> ValveControlModel:
    """Build a solowritesystem payload that actually turns the water off.

    The library's ``turn_off()`` sends an all-zero ``primaryValve1``
    (``"00000000"``). The firmware ignores that command because its prefix
    byte (0x00) doesn't address any valve — which is why users could turn
    the shower on but never off. The mobile app instead sends
    ``[prefix][temp][flow]`` with mode ``0x00`` per valve (e.g.
    ``"0179c800"``); reproduce that here for every valve the device reports.
    """
    temp_c = clamp_encode_temp(temp_c)
    kwargs: dict[str, str] = {}
    valves = state.state.valve_state if state is not None else []
    for valve in valves:
        mapping = VALVE_FIELD_AND_PREFIX.get(valve.valve_index)
        if mapping is None:
            continue
        field, prefix = mapping
        # Temp/flow bytes are ignored for OFF; they just need to be valid.
        flow = min(max(valve.flow_setpoint, 0), 100) or 100
        kwargs[field] = encode_valve_command(
            temperature_celsius=temp_c,
            flow_percent=flow,
            mode=ValveMode.OFF,
            prefix=prefix,
        )
    if "primary_valve1" not in kwargs:
        kwargs["primary_valve1"] = encode_valve_command(
            temperature_celsius=temp_c,
            flow_percent=100,
            mode=ValveMode.OFF,
            prefix=ValvePrefix.PRIMARY,
        )
    return ValveControlModel(**kwargs)
