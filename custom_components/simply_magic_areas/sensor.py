"""Sensor controls for magic areas."""

from datetime import datetime, timedelta
import logging

from homeassistant.components.group.sensor import (
    ATTR_MEAN,
    ATTR_SUM,
    SensorGroup,
    SensorStateClass,
)
from homeassistant.components.sensor import (
    DEVICE_CLASS_UNITS,
    DOMAIN as SENSOR_DOMAIN,
    UNIT_CONVERTERS,
    SensorDeviceClass,
)
from homeassistant.components.statistics.sensor import StatisticsSensor
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_ENTITY_ID,
    ATTR_UNIT_OF_MEASUREMENT,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_utc_time_change

from .base.area_state_sensor import AreaStateSensor
from .base.entities import MagicEntity
from .base.magic import MagicArea
from .config.entity_names import EntityNames
from .const import (
    AGGREGATE_MODE_SUM,
    CONF_AGGREGATES_MIN_ENTITIES,
    CONF_FEATURE_GROUP_CREATION,
    DATA_AREA_OBJECT,
    DOMAIN,
    MODULE_DATA,
)
from .util import cleanup_magic_entities

_LOGGER = logging.getLogger(__name__)

ALWAYS_DEVICE_CLASS = {SensorDeviceClass.HUMIDITY, SensorDeviceClass.ILLUMINANCE}


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the magic area sensor config entry."""

    area: MagicArea = hass.data[MODULE_DATA][config_entry.entry_id][DATA_AREA_OBJECT]
    existing_sensor_entities: list[str] = []
    if DOMAIN + SENSOR_DOMAIN in area.entities:
        existing_sensor_entities = [
            e[ATTR_ENTITY_ID] for e in area.entities[DOMAIN + SENSOR_DOMAIN]
        ]

    aggregates: list[Entity] = []

    # Check SENSOR_DOMAIN entities, count by device_class
    if area.has_entities(SENSOR_DOMAIN):
        entities_by_device_class: dict[str, list[str]] = {}

        for entity in area.entities[SENSOR_DOMAIN]:
            if ATTR_DEVICE_CLASS not in entity:
                _LOGGER.debug(
                    "%s: Entity %s does not have device_class defined",
                    area.name,
                    entity[ATTR_ENTITY_ID],
                )
                continue

            if ATTR_UNIT_OF_MEASUREMENT not in entity:
                _LOGGER.debug(
                    "%s: Entity %s does not have unit_of_measurement defined",
                    area.name,
                    entity[ATTR_ENTITY_ID],
                )
                continue

            # Dictionary of sensors by device class.
            device_class = entity[ATTR_DEVICE_CLASS]
            if device_class not in entities_by_device_class:
                entities_by_device_class[device_class] = []
            entities_by_device_class[device_class].append(entity[ATTR_ENTITY_ID])

        # Create aggregates/illuminance sensor or illuminance ones.
        for item in entities_by_device_class.items():
            device_class = item[0]
            entities = item[1]

            if device_class not in ALWAYS_DEVICE_CLASS:
                if not area.has_feature(CONF_FEATURE_GROUP_CREATION):
                    continue
                if len(entities) < area.feature_config(CONF_FEATURE_GROUP_CREATION).get(
                    CONF_AGGREGATES_MIN_ENTITIES, 2
                ):
                    continue

            _LOGGER.debug(
                "%s: reating aggregate sensor for device_class '%s' with %d entities ",
                area.slug,
                device_class,
                len(entities),
            )
            aggregates.append(
                AreaSensorGroupSensor(
                    area=area,
                    device_class=SensorDeviceClass(device_class),
                    entity_ids=entities,
                )
            )

    if (
        len(
            [
                entity[ATTR_ENTITY_ID]
                for entity in area.entities.get(SENSOR_DOMAIN, [])
                if entity.get(ATTR_DEVICE_CLASS, "") == SensorDeviceClass.HUMIDITY
                and ATTR_UNIT_OF_MEASUREMENT in entity
            ]
        )
        > 0
    ):
        # Create the humidity stats sensor.
        _LOGGER.debug(
            "%s: Creating humidity stats sensor",
            area.slug,
        )
        aggregates.append(MagicStatisticsSensor(area))

    # Make the basic area state sensor.
    _LOGGER.debug(
        "%s: Creating basic area sensor",
        area.slug,
    )
    aggregates.append(AreaStateSensor(area))

    cleanup_magic_entities(area.hass, aggregates, existing_sensor_entities)

    async_add_entities(aggregates)


class AreaSensorGroupSensor(MagicEntity, SensorGroup):
    """Sensor for the magic area, group sensor with all the stuff in it."""

    def __init__(
        self,
        area: MagicArea,
        device_class: SensorDeviceClass,
        entity_ids: list[str],
    ) -> None:
        """Initialize an area sensor group sensor."""

        MagicEntity.__init__(
            self, area=area, domain=SENSOR_DOMAIN, translation_key=device_class
        )
        SensorGroup.__init__(
            self,
            hass=area.hass,
            device_class=device_class,
            entity_ids=entity_ids,
            ignore_non_numeric=True,
            name=None,
            unique_id=self._attr_unique_id,
            sensor_type=ATTR_SUM if device_class in AGGREGATE_MODE_SUM else ATTR_MEAN,
            state_class=SensorStateClass.TOTAL
            if device_class in AGGREGATE_MODE_SUM
            else SensorStateClass.MEASUREMENT,
            unit_of_measurement=str(
                UNIT_CONVERTERS[device_class].NORMALIZED_UNIT
                if device_class in UNIT_CONVERTERS
                else list(DEVICE_CLASS_UNITS[device_class])[0]
            ),
        )
        delattr(self, "_attr_name")


class MagicStatisticsSensor(MagicEntity, StatisticsSensor):
    """Statistics sensor to track the change in the humidity."""

    def __init__(self, area: MagicArea) -> None:
        """Create the sensor to track the change in humidity."""
        StatisticsSensor.__init__(
            self,
            source_entity_id=area.simply_magic_entity_id(
                SENSOR_DOMAIN, SensorDeviceClass.HUMIDITY
            ),
            name="",
            unique_id=None,
            state_characteristic="change_second",
            samples_max_buffer_size=5,
            samples_max_age=timedelta(minutes=5),
            samples_keep_last=True,
            precision=2,
            percentile=50,
        )
        MagicEntity.__init__(
            self,
            area=area,
            domain=SENSOR_DOMAIN,
            translation_key=EntityNames.HUMIDITY_STATISTICS,
        )
        delattr(self, "_attr_name")
        self.async_on_remove(self._cleanup_timers)
        self._update_periodically = async_track_utc_time_change(
            area.hass, self._update_state, hour="*", minute="*", second="0"
        )

    @callback
    async def _update_state(self, d: datetime):
        entity = self.hass.states.get(self._source_entity_id)
        if entity and entity.state:
            self._add_state_to_queue(entity)
        await self.async_update()

    @callback
    def _cleanup_timers(self) -> None:
        self._async_cancel_update_listener()
        self._update_periodically()
