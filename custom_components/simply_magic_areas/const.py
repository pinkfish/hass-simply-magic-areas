"""Constants for the magic areas code."""

from itertools import chain

import voluptuous as vol

from homeassistant.components.binary_sensor import (
    DOMAIN as BINARY_SENSOR_DOMAIN,
    BinarySensorDeviceClass,
)
from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN
from homeassistant.components.cover import DOMAIN as COVER_DOMAIN
from homeassistant.components.fan import DOMAIN as FAN_DOMAIN
from homeassistant.components.input_boolean import DOMAIN as INPUT_BOOLEAN_DOMAIN
from homeassistant.components.light import DOMAIN as LIGHT_DOMAIN
from homeassistant.components.media_player import DOMAIN as MEDIA_PLAYER_DOMAIN
from homeassistant.components.remote import DOMAIN as REMOTE_DOMAIN
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN, SensorDeviceClass
from homeassistant.components.sun import DOMAIN as SUN_DOMAIN
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import (
    STATE_ALARM_TRIGGERED,
    STATE_HOME,
    STATE_ON,
    STATE_OPEN,
    STATE_PLAYING,
    STATE_PROBLEM,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.helpers import config_validation as cv

from .config.area_state import AreaState
from .config.light_entity_config import LightEntityConf

DOMAIN = "simply_magic_areas"
MODULE_DATA = f"{DOMAIN}_data"

# Magic Areas Events
EVENT_MAGICAREAS_STARTED = "magicareas_start"
EVENT_MAGICAREAS_READY = "magicareas_ready"
EVENT_MAGICAREAS_AREA_READY = "magicareas_area_ready"

ALL_BINARY_SENSOR_DEVICE_CLASSES = [cls.value for cls in BinarySensorDeviceClass]

# Data Items
DATA_AREA_OBJECT = "area_object"
DATA_UNDO_UPDATE_LISTENER = "undo_update_listener"
DATA_ENTITY_LISTENER = "entity_listener"

# Attributes
ATTR_STATE = "state"
ATTR_AREAS = "areas"
ATTR_ACTIVE_AREAS = "active_areas"
ATTR_TYPE = "type"
ATTR_UPDATE_INTERVAL = "update_interval"
ATTR_CLEAR_TIMEOUT = "clear_timeout"
ATTR_EXTENDED_TIMEOUT = "extended_timeout"
ATTR_ACTIVE_SENSORS = "active_sensors"
ATTR_LAST_ACTIVE_SENSORS = "last_active_sensors"
ATTR_FEATURES = "features"
ATTR_PRESENCE_SENSORS = "presence_sensors"
ATTR_LAST_UPDATE_FROM_ENTITY: str = "last_update_from_entity"

# Icons
ICON_SYSTEM_CONTROL = "mdi:head-cog"

# MagicAreas Components
MAGIC_AREAS_COMPONENTS = [
    SWITCH_DOMAIN,
    BINARY_SENSOR_DOMAIN,
    SENSOR_DOMAIN,
    COVER_DOMAIN,
    LIGHT_DOMAIN,
    FAN_DOMAIN,
]

MAGIC_AREAS_COMPONENTS_META = [
    BINARY_SENSOR_DOMAIN,
    COVER_DOMAIN,
    SENSOR_DOMAIN,
    LIGHT_DOMAIN,
]

MAGIC_DEVICE_ID_PREFIX = "simply_magic_areas_"

# Meta Areas
META_AREA_GLOBAL = "Global"
META_AREA_INTERIOR = "Interior"
META_AREA_EXTERIOR = "Exterior"
META_AREAS = [META_AREA_GLOBAL, META_AREA_INTERIOR, META_AREA_EXTERIOR]

# Area Types
AREA_TYPE_META = "meta"
AREA_TYPE_INTERIOR = "interior"
AREA_TYPE_EXTERIOR = "exterior"
AREA_TYPES = [AREA_TYPE_INTERIOR, AREA_TYPE_EXTERIOR, AREA_TYPE_META]

AVAILABLE_ON_STATES = [STATE_ON, STATE_HOME, STATE_PLAYING, STATE_OPEN]

INVALID_STATES = [STATE_UNAVAILABLE, STATE_UNKNOWN]

# Configuration parameters
CONF_ID = "id"
CONF_NAME, DEFAULT_NAME = "name", ""  # cv.string
CONF_TYPE, DEFAULT_TYPE = "type", AREA_TYPE_INTERIOR  # cv.string
CONF_ENABLED_FEATURES, DEFAULT_ENABLED_FEATURES = "features", {}  # cv.ensure_list
CONF_INCLUDE_ENTITIES = "include_entities"  # cv.entity_ids
CONF_EXCLUDE_ENTITIES = "exclude_entities"  # cv.entity_ids
(
    CONF_PRESENCE_DEVICE_PLATFORMS,
    DEFAULT_PRESENCE_DEVICE_PLATFORMS,
) = (
    "presence_device_platforms",
    [
        MEDIA_PLAYER_DOMAIN,
        BINARY_SENSOR_DOMAIN,
    ],
)  # cv.ensure_list
ALL_PRESENCE_DEVICE_PLATFORMS = [
    MEDIA_PLAYER_DOMAIN,
    BINARY_SENSOR_DOMAIN,
    REMOTE_DOMAIN,
]
(
    CONF_PRESENCE_SENSOR_DEVICE_CLASS,
    DEFAULT_PRESENCE_DEVICE_SENSOR_CLASS,
) = (
    "presence_sensor_device_class",
    [
        BinarySensorDeviceClass.MOTION,
        BinarySensorDeviceClass.OCCUPANCY,
        BinarySensorDeviceClass.PRESENCE,
    ],
)  # cv.ensure_list
ILLUMINANCE_DEVICE_PLATFORMS = [
    SENSOR_DOMAIN,
]
(
    CONF_ILLUMINANCE_DEVICE_CLASS,
    DEFAULT_ILLUMINANCE_DEVICE_SENSOR_CLASS,
) = (
    "illuminance_sensor_device_class",
    [
        SensorDeviceClass.ILLUMINANCE,
    ],
)  # cv.ensure_list
CONF_ON_STATES, DEFAULT_ON_STATES = (
    "on_states",
    [
        STATE_ON,
        STATE_OPEN,
    ],
)  # cv.ensure_list
CONF_AGGREGATES_MIN_ENTITIES, DEFAULT_AGGREGATES_MIN_ENTITIES = (
    "aggregates_min_entities",
    2,
)  # cv.positive_int
CONF_CLEAR_TIMEOUT, DEFAULT_CLEAR_TIMEOUT = "clear_timeout", 360  # cv.positive_int
CONF_EXTENDED_TIMEOUT, DEFAULT_EXTENDED_TIMEOUT = (
    "extended_timeout",
    360,
)  # cv.positive_int
CONF_MANUAL_TIMEOUT, DEFAULT_MANUAL_TIMEOUT = "manual_timeout", 60  # cv.positive_int
CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL = (
    "update_interval",
    1800,
)  # cv.positive_int
CONF_ICON, DEFAULT_ICON = "icon", "mdi:texture-box"  # cv.string
CONF_NOTIFICATION_DEVICES, DEFAULT_NOTIFICATION_DEVICES = (
    "notification_devices",
    [],
)  # cv.entity_ids
CONF_NOTIFY_STATES, DEFAULT_NOTIFY_STATES = (
    "notification_states",
    [
        AreaState.AREA_STATE_EXTENDED,
    ],
)  # cv.ensure_list
# When to start dimmin the lights.
CONF_MIN_BRIGHTNESS_LEVEL, DEFAULT_MIN_BRIGHTNESS_LEVEL = ("min_brightness_level", 100)
# When to turn the lights off entirely.
CONF_MAX_BRIGHTNESS_LEVEL, DEFAULT_MAX_BRIGHTNESS_LEVEL = ("max_brightness_level", 200)
# Controlling the lights and the fan.
CONF_LIGHT_CONTROL, DEFAULT_LIGHT_CONTROL = ("light_control", True)
CONF_FAN_CONTROL, DEFAULT_FAN_CONTROL = ("fan_control", True)
# Controls for the humidity stats sensor.
CONF_HUMIDITY_TREND_DOWN_CUT_OFF, DEFAULT_HUMIDITY_TREND_DOWN_CUT_OFF = (
    "humidity_down",
    -0.015,
)
CONF_HUMIDITY_TREND_UP_CUT_OFF, DEFAULT_HUMIDITY_TREND_UP_CUT_OFF = (
    "humidity_up",
    0.03,
)
CONF_HUMIDITY_ZERO_WAIT_TIME, DEFAULT_HUMIDITY_ZERO_WAIT_TIME = (
    "humidity_wait_s",
    20 * 60,
)
# Mqtt room control
CONF_MQTT_ROOM_PRESENCE, DEFAULT_MQTT_ROOM_PRESENCE = ("mqqt_room", False)

# Setups to control all the lights, items to create
clear_lights = LightEntityConf(
    name="clear",
    is_advanced=True,
    default_dim_level=0.0,
    enable_state=AreaState.AREA_STATE_CLEAR,
    icon="mdi:off",
    has_entity=False,
)
bright_lights = LightEntityConf(
    name="bright",
    default_dim_level=0.0,
    enable_state=AreaState.AREA_STATE_BRIGHT,
    icon="mdi:ceiling-light",
    has_entity=True,
    is_advanced=False,
)
sleep_lights = LightEntityConf(
    name="sleep",
    default_dim_level=30.0,
    enable_state=AreaState.AREA_STATE_SLEEP,
    icon="mdi:sleep",
    has_entity=True,
    is_advanced=False,
)
occupied_lights = LightEntityConf(
    name="occupied",
    is_advanced=False,
    default_dim_level=100.0,
    enable_state=AreaState.AREA_STATE_OCCUPIED,
    icon="mdi:desk-lamp",
    has_entity=False,
)
extended_lights = LightEntityConf(
    name="extended",
    is_advanced=True,
    default_dim_level=0.0,
    enable_state=AreaState.AREA_STATE_EXTENDED,
    has_entity=False,
    icon="mdi:desk-lamp",
)
accented_lights = LightEntityConf(
    name="accented",
    is_advanced=True,
    default_dim_level=0.0,
    enable_state=AreaState.AREA_STATE_ACCENTED,
    has_entity=True,
    icon="mdi:desk-lamp",
)

# All the light setup pieces.
ALL_LIGHT_ENTITIES = [
    clear_lights,
    sleep_lights,
    bright_lights,
    extended_lights,
    occupied_lights,
    accented_lights,
]

# features
CONF_FEATURE_CLIMATE_GROUPS = "climate_groups"
CONF_FEATURE_GROUP_CREATION = "group_creation"
CONF_FEATURE_ADVANCED_LIGHT_GROUPS = "advanced_light_groups"
CONF_FEATURE_AREA_AWARE_MEDIA_PLAYER = "area_aware_media_player"
CONF_FEATURE_HEALTH = "health"
CONF_FEATURE_HUMIDITY = "humidity"

# Features of the group type
CONF_MEDIA_PLAYER_GROUPS = "media_player_groups"
CONF_COVER_GROUPS = "cover_groups"
CONF_AGGREGATION = "aggregates"

CONF_FEATURE_LIST_META = [
    CONF_FEATURE_ADVANCED_LIGHT_GROUPS,
    CONF_FEATURE_HUMIDITY,
    CONF_FEATURE_GROUP_CREATION,
    CONF_FEATURE_CLIMATE_GROUPS,
    CONF_FEATURE_HEALTH,
]

CONF_FEATURE_LIST = [
    *CONF_FEATURE_LIST_META,
    CONF_FEATURE_AREA_AWARE_MEDIA_PLAYER,
]

CONF_FEATURE_LIST_GLOBAL = CONF_FEATURE_LIST_META

# Climate Group Options
CONF_CLIMATE_GROUPS_TURN_ON_STATE, DEFAULT_CLIMATE_GROUPS_TURN_ON_STATE = (
    "turn_on_state",
    AreaState.AREA_STATE_EXTENDED,
)

LIGHT_GROUP_DEFAULT_ICON = "mdi:lightbulb-group"

LIGHT_GROUP_ICONS = {}


AGGREGATE_BINARY_SENSOR_CLASSES = [
    BinarySensorDeviceClass.WINDOW,
    BinarySensorDeviceClass.DOOR,
    BinarySensorDeviceClass.MOTION,
    BinarySensorDeviceClass.MOISTURE,
    BinarySensorDeviceClass.LIGHT,
]

AGGREGATE_MODE_ALL = [
    BinarySensorDeviceClass.CONNECTIVITY,
    BinarySensorDeviceClass.PLUG,
]

# Health related
DISTRESS_SENSOR_CLASSES = [
    BinarySensorDeviceClass.PROBLEM,
    BinarySensorDeviceClass.SMOKE,
    BinarySensorDeviceClass.MOISTURE,
    BinarySensorDeviceClass.SAFETY,
    BinarySensorDeviceClass.GAS,
]  # @todo make configurable
DISTRESS_STATES = [STATE_ALARM_TRIGGERED, STATE_ON, STATE_PROBLEM]

# Aggregates
AGGREGATE_SENSOR_CLASSES = (
    SensorDeviceClass.CURRENT,
    SensorDeviceClass.ENERGY,
    SensorDeviceClass.HUMIDITY,
    SensorDeviceClass.ILLUMINANCE,
    SensorDeviceClass.POWER,
    SensorDeviceClass.TEMPERATURE,
)

AGGREGATE_MODE_SUM = [
    SensorDeviceClass.POWER,
    SensorDeviceClass.CURRENT,
    SensorDeviceClass.ENERGY,
]

# Config Schema
GROUP_CREATION_FEATURE_SCHEMA = vol.Schema(
    {
        vol.Optional(
            CONF_AGGREGATES_MIN_ENTITIES, default=DEFAULT_AGGREGATES_MIN_ENTITIES
        ): cv.positive_int,
        vol.Optional(CONF_AGGREGATION): bool,
        vol.Optional(CONF_MEDIA_PLAYER_GROUPS): bool,
        vol.Optional(CONF_COVER_GROUPS): bool,
    },
)


CLIMATE_GROUP_FEATURE_SCHEMA = vol.Schema(
    {
        vol.Optional(
            CONF_CLIMATE_GROUPS_TURN_ON_STATE,
            default=DEFAULT_CLIMATE_GROUPS_TURN_ON_STATE,
        ): str,
    }
)

HUMIDITY_GROUP_FEATURE_SCHEMA = vol.Schema(
    {
        vol.Optional(
            CONF_HUMIDITY_TREND_UP_CUT_OFF,
            default=DEFAULT_HUMIDITY_TREND_UP_CUT_OFF,
        ): float,
        vol.Optional(
            CONF_HUMIDITY_TREND_DOWN_CUT_OFF,
            default=DEFAULT_HUMIDITY_TREND_DOWN_CUT_OFF,
        ): float,
        vol.Optional(
            CONF_HUMIDITY_ZERO_WAIT_TIME,
            default=DEFAULT_HUMIDITY_ZERO_WAIT_TIME,
        ): int,
    }
)


ADVANCED_LIGHT_GROUP_FEATURE_SCHEMA: vol.Schema = vol.Schema(
    {
        k: v
        for lg in ALL_LIGHT_ENTITIES
        for k, v in lg.advanced_config_flow_schema().items()
    }
).extend(
    {
        vol.Optional(CONF_INCLUDE_ENTITIES, default=[]): cv.entity_ids,
        vol.Optional(CONF_EXCLUDE_ENTITIES, default=[]): cv.entity_ids,
        vol.Optional(
            CONF_UPDATE_INTERVAL, default=DEFAULT_UPDATE_INTERVAL
        ): cv.positive_int,
        vol.Optional(
            CONF_PRESENCE_DEVICE_PLATFORMS,
            default=DEFAULT_PRESENCE_DEVICE_PLATFORMS,
        ): cv.ensure_list,
        vol.Optional(
            CONF_PRESENCE_SENSOR_DEVICE_CLASS,
            default=DEFAULT_PRESENCE_DEVICE_SENSOR_CLASS,
        ): cv.ensure_list,
        vol.Optional(CONF_ON_STATES, default=DEFAULT_ON_STATES): cv.ensure_list,
    }
)


AREA_AWARE_MEDIA_PLAYER_FEATURE_SCHEMA: vol.Schema = vol.Schema(
    {
        vol.Optional(CONF_NOTIFICATION_DEVICES, default=[]): cv.entity_ids,
        vol.Optional(CONF_NOTIFY_STATES, default=DEFAULT_NOTIFY_STATES): cv.ensure_list,
    }
)

ALL_FEATURES = set(CONF_FEATURE_LIST) | set(CONF_FEATURE_LIST_GLOBAL)

CONFIGURABLE_FEATURES: dict[str, vol.Schema] = {
    CONF_FEATURE_ADVANCED_LIGHT_GROUPS: ADVANCED_LIGHT_GROUP_FEATURE_SCHEMA,
    CONF_FEATURE_CLIMATE_GROUPS: CLIMATE_GROUP_FEATURE_SCHEMA,
    CONF_FEATURE_GROUP_CREATION: GROUP_CREATION_FEATURE_SCHEMA,
    CONF_FEATURE_AREA_AWARE_MEDIA_PLAYER: AREA_AWARE_MEDIA_PLAYER_FEATURE_SCHEMA,
    CONF_FEATURE_HUMIDITY: HUMIDITY_GROUP_FEATURE_SCHEMA,
}

NON_CONFIGURABLE_FEATURES_META = [
    CONF_FEATURE_ADVANCED_LIGHT_GROUPS,
    CONF_FEATURE_CLIMATE_GROUPS,
]

NON_CONFIGURABLE_FEATURES: dict[str, vol.Schema] = {
    feature: vol.Schema({})
    for feature in ALL_FEATURES
    if feature not in CONFIGURABLE_FEATURES
}

FEATURES_SCHEMA: vol.Schema = vol.Schema(
    {
        vol.Optional(feature): feature_schema
        for feature, feature_schema in chain(
            CONFIGURABLE_FEATURES.items(), NON_CONFIGURABLE_FEATURES.items()
        )
    }
)

# Simply Magic Areas
REGULAR_AREA_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_TYPE, default=DEFAULT_TYPE): vol.In(
            [AREA_TYPE_INTERIOR, AREA_TYPE_EXTERIOR]
        ),
        vol.Optional(CONF_ENABLED_FEATURES, default={}): FEATURES_SCHEMA,
        vol.Optional(
            CONF_CLEAR_TIMEOUT, default=DEFAULT_CLEAR_TIMEOUT
        ): cv.positive_int,
        vol.Optional(
            CONF_EXTENDED_TIMEOUT, default=DEFAULT_EXTENDED_TIMEOUT
        ): cv.positive_int,
        vol.Optional(CONF_ICON, default=DEFAULT_ICON): cv.string,
        vol.Optional(CONF_LIGHT_CONTROL, default=DEFAULT_LIGHT_CONTROL): bool,
        vol.Optional(CONF_FAN_CONTROL, default=DEFAULT_FAN_CONTROL): bool,
        vol.Optional(CONF_MQTT_ROOM_PRESENCE, default=DEFAULT_MQTT_ROOM_PRESENCE): bool,
        vol.Optional(CONF_MIN_BRIGHTNESS_LEVEL, default=DEFAULT_MIN_BRIGHTNESS_LEVEL): val.positive_int,
        vol.Optional(CONF_MAX_BRIGHTNESS_LEVEL, default=DEFAULT_MAX_BRIGHTNESS_LEVEL): val,positive_int,
    }
).extend(
    {k: v for lg in ALL_LIGHT_ENTITIES for k, v in lg.config_flow_schema().items()}
)

META_AREA_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_TYPE, default=AREA_TYPE_META): AREA_TYPE_META,
        vol.Optional(
            CONF_CLEAR_TIMEOUT, default=DEFAULT_CLEAR_TIMEOUT
        ): cv.positive_int,
        vol.Optional(CONF_ICON, default=DEFAULT_ICON): cv.string,
    }
)

AREA_SCHEMA = vol.Any(REGULAR_AREA_SCHEMA, META_AREA_SCHEMA)

_DOMAIN_SCHEMA = vol.Schema({cv.slug: AREA_SCHEMA})

# VALIDATION_TUPLES
OPTIONS_AREA = [
    (CONF_TYPE, DEFAULT_TYPE, vol.In([AREA_TYPE_INTERIOR, AREA_TYPE_EXTERIOR])),
    (CONF_CLEAR_TIMEOUT, DEFAULT_CLEAR_TIMEOUT, int),
    (CONF_EXTENDED_TIMEOUT, DEFAULT_EXTENDED_TIMEOUT, int),
    (CONF_ICON, DEFAULT_ICON, str),
    (CONF_LIGHT_CONTROL, DEFAULT_LIGHT_CONTROL, bool),
    (CONF_FAN_CONTROL, DEFAULT_FAN_CONTROL, bool),
    (CONF_MQTT_ROOM_PRESENCE, DEFAULT_MQTT_ROOM_PRESENCE, bool),
]
for item in ALL_LIGHT_ENTITIES:
    OPTIONS_AREA.extend(item.config_flow_options())

OPTIONS_AREA_ADVANCED = [
    (CONF_INCLUDE_ENTITIES, [], cv.entity_ids),
    (CONF_EXCLUDE_ENTITIES, [], cv.entity_ids),
    (CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL, int),
    (
        CONF_PRESENCE_DEVICE_PLATFORMS,
        DEFAULT_PRESENCE_DEVICE_PLATFORMS,
        cv.ensure_list,
    ),
    (
        CONF_PRESENCE_SENSOR_DEVICE_CLASS,
        DEFAULT_PRESENCE_DEVICE_SENSOR_CLASS,
        cv.ensure_list,
    ),
    (
        CONF_ON_STATES,
        DEFAULT_ON_STATES,
        cv.ensure_list,
    ),
]

OPTIONS_AREA_META = [
    (CONF_CLEAR_TIMEOUT, DEFAULT_CLEAR_TIMEOUT, int),
    (CONF_ICON, DEFAULT_ICON, str),
]


OPTIONS_GROUP_CREATION = [
    (CONF_AGGREGATES_MIN_ENTITIES, DEFAULT_AGGREGATES_MIN_ENTITIES, int),
    (CONF_COVER_GROUPS, False, bool),
    (CONF_MEDIA_PLAYER_GROUPS, False, bool),
]

OPTIONS_CLIMATE_GROUP = [
    (CONF_CLIMATE_GROUPS_TURN_ON_STATE, DEFAULT_CLIMATE_GROUPS_TURN_ON_STATE, str),
]

OPTIONS_CLIMATE_GROUP_META = [
    (CONF_CLIMATE_GROUPS_TURN_ON_STATE, None, str),
]

OPTIONS_AREA_AWARE_MEDIA_PLAYER = [
    (CONF_NOTIFICATION_DEVICES, [], cv.entity_ids),
    (CONF_NOTIFY_STATES, DEFAULT_NOTIFY_STATES, cv.ensure_list),
]

OPTIONS_HUMIDITY = [
    (
        CONF_HUMIDITY_TREND_DOWN_CUT_OFF,
        DEFAULT_HUMIDITY_TREND_DOWN_CUT_OFF,
        float,
    ),
    (CONF_HUMIDITY_TREND_UP_CUT_OFF, DEFAULT_HUMIDITY_TREND_UP_CUT_OFF, float),
    (CONF_HUMIDITY_ZERO_WAIT_TIME, DEFAULT_HUMIDITY_ZERO_WAIT_TIME, int),
]


# Config Flow filters
CONFIG_FLOW_ENTITY_FILTER = [
    BINARY_SENSOR_DOMAIN,
    SENSOR_DOMAIN,
    SWITCH_DOMAIN,
    INPUT_BOOLEAN_DOMAIN,
]
CONFIG_FLOW_ENTITY_FILTER_EXT = [
    *CONFIG_FLOW_ENTITY_FILTER,
    LIGHT_DOMAIN,
    MEDIA_PLAYER_DOMAIN,
    CLIMATE_DOMAIN,
    SUN_DOMAIN,
]
