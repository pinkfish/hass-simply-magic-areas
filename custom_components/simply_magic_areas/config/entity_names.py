"""The various enums for use in the system."""

from enum import StrEnum


class EntityNames(StrEnum):
    """The names of the fixed entities to use."""

    STATE = "state"
    HUMIDITY_OCCUPIED = "humidity_occupancy"
    HUMIDITY_EMPTY = "humidity_empty"
    MANUAL_OVERRIDE = "manual_override"
    LIGHT_CONTROL = "light_control"
    LIGHT = "light"
    FAN = "fan"
    ILLUMINANCE = "illuminance"
    HUMIDITY = "humidity"
    MEDIA_PLAYER = "media"
