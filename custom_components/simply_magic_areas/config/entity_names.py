"""The various enums for use in the system."""

from enum import StrEnum


class EntityNames(StrEnum):
    """The names of the fixed entities to use."""

    STATE = "state"
    FAN_CONTROL = "fan_control"
    LIGHT_CONTROL = "light_control"
    SYSTEM_CONTROL = "system_control"
    LIGHT = "light"
    FAN = "fan"
    ILLUMINANCE = "illuminance"
    HUMIDITY = "humidity"
    MEDIA_PLAYER = "media"
    HUMIDITY_STATISTICS = "humidity_statistics"
