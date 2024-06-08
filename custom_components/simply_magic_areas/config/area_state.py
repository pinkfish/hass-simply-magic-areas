"""The various enums for use in the system."""

from enum import StrEnum


# Area States
class AreaState(StrEnum):
    """The states the area can be in."""

    AREA_STATE_CLEAR = "clear"  # Clear state, not occupied.
    AREA_STATE_OCCUPIED = "occupied"  # main occupied state
    AREA_STATE_EXTENDED = "extended"  # Timeout after main occupied
    AREA_STATE_BRIGHT = "bright"  # Bright state, high lumens
    AREA_STATE_SLEEP = "sleep"  # Sleep state (night)
    AREA_STATE_ACCENTED = "accented"  # If the state is accented
    AREA_STATE_MANUAL = "manual"  # Manual control enableds
