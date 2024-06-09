"""Select control for magic areas, tracks the state as an enum."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
import logging

from homeassistant.components.binary_sensor import DOMAIN as BINARY_SENSOR_DOMAIN
from homeassistant.components.select import (
    DOMAIN as SELECT_DOMAIN,
    SelectEntity,
    SelectEntityDescription,
)
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_DEVICE_CLASS, ATTR_ENTITY_ID, STATE_ON
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
    call_later,
)

from .base.entities import MagicEntity
from .base.magic import MagicArea
from .config.area_state import AreaState
from .config.entity_names import EntityNames
from .const import (
    ATTR_ACTIVE_AREAS,
    ATTR_ACTIVE_SENSORS,
    ATTR_AREAS,
    ATTR_CLEAR_TIMEOUT,
    ATTR_EXTENDED_TIMEOUT,
    ATTR_LAST_ACTIVE_SENSORS,
    ATTR_PRESENCE_SENSORS,
    ATTR_STATE,
    ATTR_TYPE,
    CONF_CLEAR_TIMEOUT,
    CONF_EXTENDED_TIMEOUT,
    CONF_FEATURE_ADVANCED_LIGHT_GROUPS,
    CONF_ON_STATES,
    CONF_PRESENCE_DEVICE_PLATFORMS,
    CONF_PRESENCE_SENSOR_DEVICE_CLASS,
    CONF_TYPE,
    CONF_UPDATE_INTERVAL,
    DATA_AREA_OBJECT,
    DEFAULT_ON_STATES,
    DEFAULT_PRESENCE_DEVICE_PLATFORMS,
    DEFAULT_PRESENCE_DEVICE_SENSOR_CLASS,
    DEFAULT_UPDATE_INTERVAL,
    INVALID_STATES,
    MODULE_DATA,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Area config entry."""
    _LOGGER.debug("Doing area select")  # type: ignore  # noqa: PGH003
    area: MagicArea = hass.data[MODULE_DATA][config_entry.entry_id][DATA_AREA_OBJECT]

    # Create basic presence sensor
    async_add_entities([])
