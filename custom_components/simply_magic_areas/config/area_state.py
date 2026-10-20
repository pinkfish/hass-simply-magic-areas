"""The various enums for use in the system."""

from enum import StrEnum


# Area States
class AreaState(StrEnum):
    """The presence states the area can be in."""

    AREA_STATE_CLEAR = "clear"  # Clear state, not occupied.
    AREA_STATE_OCCUPIED = "occupied"  # Main occupied state
    AREA_STATE_EXTENDED = "extended"  # Timeout after main occupied
    AREA_STATE_MANUAL = "manual"  # Manual control enabled
    # Backward compatibility aliases
    AREA_STATE_BRIGHT = "bright"
    AREA_STATE_SLEEP = "sleep"
    AREA_STATE_ACCENTED = "accented"


# Lighting Profiles
class LightingProfile(StrEnum):
    """The contextual lighting profile for an area."""

    STANDARD = "standard"  # Standard task/ambient lighting
    SLEEP = "sleep"  # Night/sleep mode
    BRIGHT = "bright"  # Daytime / high ambient lux
    ACCENTED = "accented"  # Accent scene / decorative lighting
