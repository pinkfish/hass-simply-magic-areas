"""Binary sensor control for magic areas."""

import logging

from homeassistant.components.binary_sensor import (
    DOMAIN as BINARY_SENSOR_DOMAIN,
    BinarySensorDeviceClass,
)
from homeassistant.components.group.binary_sensor import BinarySensorGroup
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_ENTITY_ID,
    ATTR_UNIT_OF_MEASUREMENT,
    STATE_ON,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity_registry import async_get as async_get_er

from .base.entities import MagicEntity
from .base.magic import MagicArea
from .const import (
    AGGREGATE_MODE_ALL,
    CONF_AGGREGATES_MIN_ENTITIES,
    CONF_AGGREGATION,
    CONF_FEATURE_GROUP_CREATION,
    CONF_FEATURE_HEALTH,
    DATA_AREA_OBJECT,
    DISTRESS_SENSOR_CLASSES,
    DOMAIN,
    MODULE_DATA,
)

_LOGGER = logging.getLogger(__name__)
ATTR_GRADIENT: str = "gradient"
ATTR_MIN_GRADIENT: str = "min_gradient"
ATTR_ENTITY_TO_MONITOR: str = "entity_to_monitor"


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Area config entry."""

    area: MagicArea = hass.data[MODULE_DATA][config_entry.entry_id][DATA_AREA_OBJECT]

    entities: list[str] = []
    existing_trend_entities: list[str] = []
    if DOMAIN + BINARY_SENSOR_DOMAIN in area.entities:
        existing_trend_entities = [
            e[ATTR_ENTITY_ID] for e in area.entities[DOMAIN + BINARY_SENSOR_DOMAIN]
        ]

    # Check SENSOR_DOMAIN entities, count by device_class
    entities_by_device_class: dict[str, list[str]] = {}

    for entity in area.entities.get(BINARY_SENSOR_DOMAIN, []):
        if ATTR_DEVICE_CLASS not in entity:
            _LOGGER.debug(
                "Entity %s does not have device_class defined",
                entity[ATTR_ENTITY_ID],
            )
            continue

        if ATTR_UNIT_OF_MEASUREMENT not in entity:
            _LOGGER.debug(
                "Entity %s does not have unit_of_measurement defined",
                entity[ATTR_ENTITY_ID],
            )
            continue

        # Dictionary of sensors by device class.
        device_class = entity[ATTR_DEVICE_CLASS]
        entities = entities_by_device_class.get(device_class, [])
        entities.append(entity[ATTR_ENTITY_ID])
        entities_by_device_class[device_class] = entities

    # Create extra sensors
    sensors: list[Entity] = []
    if area.has_feature(CONF_AGGREGATION):
        sensors.extend(create_aggregate_sensors(area, entities_by_device_class))

    if area.has_feature(CONF_FEATURE_HEALTH):
        sensors.extend(create_health_sensors(area, entities_by_device_class))

    # Do the actual add.
    async_add_entities(sensors)

    _cleanup_binary_sensor_entities(area.hass, sensors, existing_trend_entities)


def create_health_sensors(
    area: MagicArea, entities_by_device_class: dict[str, list[str]]
) -> list[Entity]:
    """Add the health sensors for the area."""
    if not area.has_feature(CONF_FEATURE_HEALTH):
        return []

    if BINARY_SENSOR_DOMAIN not in area.entities:
        return []

    distress_entities = [
        e
        for dc in DISTRESS_SENSOR_CLASSES
        for e in entities_by_device_class.get(dc, [])
    ]

    if len(distress_entities) < area.feature_config(CONF_FEATURE_GROUP_CREATION).get(
        CONF_AGGREGATES_MIN_ENTITIES, 0
    ):
        return []

    _LOGGER.debug("Creating health sensor for area (%s)", area.slug)
    ret: list[Entity] = []
    ret.append(
        AreaSensorGroupBinarySensor(
            area,
            entity_ids=distress_entities,
            device_class=BinarySensorDeviceClass.PROBLEM,
        )
    )
    return ret


def create_aggregate_sensors(
    area: MagicArea, entities_by_device_class: dict[str, list[str]]
) -> list[Entity]:
    """Create the aggregate sensors for the area."""
    # Create aggregates
    if not area.has_feature(CONF_AGGREGATION):
        return []

    # Check BINARY_SENSOR_DOMAIN entities, count by device_class
    if BINARY_SENSOR_DOMAIN not in area.entities:
        return []

    aggregates: list[Entity] = []
    for device_class, entities in entities_by_device_class.items():
        if len(entities) < area.feature_config(CONF_FEATURE_GROUP_CREATION).get(
            CONF_AGGREGATES_MIN_ENTITIES, 0
        ):
            continue

        _LOGGER.debug(
            "Creating aggregate sensor for device_class '%s' with %s entities (%s)",
            device_class,
            len(entities),
            area.slug,
        )
        aggregates.append(
            AreaSensorGroupBinarySensor(
                area, BinarySensorDeviceClass(device_class), entities
            )
        )

    return aggregates


def _cleanup_binary_sensor_entities(
    hass: HomeAssistant, new_ids: list[Entity], old_ids: list[str]
) -> None:
    entity_registry = async_get_er(hass)
    new_entity_ids = [e.entity_id for e in new_ids]
    for ent_id in old_ids:
        if ent_id in new_entity_ids:
            continue
        _LOGGER.warning("Deleting old entity %s", ent_id)
        entity_registry.async_remove(ent_id)


class AreaSensorGroupBinarySensor(MagicEntity, BinarySensorGroup):
    """Group binary sensor for the area."""

    def __init__(
        self,
        area: MagicArea,
        device_class: BinarySensorDeviceClass,
        entity_ids: list[str],
    ) -> None:
        """Initialize an area sensor group binary sensor."""

        MagicEntity.__init__(
            self, area=area, translation_key=device_class, domain=BINARY_SENSOR_DOMAIN
        )
        BinarySensorGroup.__init__(
            self,
            device_class=device_class,
            entity_ids=entity_ids,
            mode=device_class in AGGREGATE_MODE_ALL,
            unique_id=self._attr_unique_id,
        )

        self.area = area

    async def async_added_to_hass(self) -> None:
        """Call when entity about to be added to hass."""
        last_state = await self.async_get_last_state()
        if last_state:
            _LOGGER.debug(
                "%s restored [state=%s]",
                self.name,
                last_state.state,
            )
            self._attr_is_on = last_state.state == STATE_ON
            self._attr_extra_state_attributes = dict(last_state.attributes)
        await super().async_added_to_hass()
        self.async_write_ha_state()
