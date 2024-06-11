"""Sensor controls for magic areas."""

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
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_ENTITY_ID,
    ATTR_UNIT_OF_MEASUREMENT,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity_registry import async_get as async_get_er

from .base.area_state_sensor import AreaStateSensor
from .base.entities import MagicEntity
from .base.magic import MagicArea
from .const import (
    AGGREGATE_MODE_SUM,
    CONF_AGGREGATES_MIN_ENTITIES,
    CONF_FEATURE_GROUP_CREATION,
    DATA_AREA_OBJECT,
    DOMAIN,
    MODULE_DATA,
)

_LOGGER = logging.getLogger(__name__)

ALWAYS_DEVICE_CLASS = {SensorDeviceClass.HUMIDITY, SensorDeviceClass.ILLUMINANCE}


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
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

    # Create the basic state sensor.
    _LOGGER.debug(
        "%s: Creating state sensor",
        area.slug,
    )

    aggregates.append(AreaStateSensor(area))

    _cleanup_sensor_entities(
        area.hass, [a.entity_id for a in aggregates], existing_sensor_entities
    )

    async_add_entities(aggregates)


def _cleanup_sensor_entities(
    hass: HomeAssistant, new_ids: list[str], old_ids: list[str]
) -> None:
    entity_registry = async_get_er(hass)
    for ent_id in old_ids:
        if ent_id in new_ids:
            continue
        _LOGGER.warning("Deleting old entity %s", ent_id)
        entity_registry.async_remove(ent_id)


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
