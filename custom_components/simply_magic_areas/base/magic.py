"""The device setup for the simply magic areas."""

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
import logging
from typing import Any

from homeassistant.components.light import DOMAIN as LIGHT_DOMAIN
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers.area_registry import AreaEntry
from homeassistant.helpers.device_registry import async_get as async_get_dr
from homeassistant.helpers.entity_registry import (
    RegistryEntry,
    async_get as async_get_er,
)
from homeassistant.util import slugify

from ..config.area_state import AreaState
from ..config.entity_names import EntityNames
from ..const import (
    ALL_LIGHT_ENTITIES,
    AREA_TYPE_EXTERIOR,
    AREA_TYPE_INTERIOR,
    AREA_TYPE_META,
    CONF_ENABLED_FEATURES,
    CONF_EXCLUDE_ENTITIES,
    CONF_FAN_CONTROL,
    CONF_FEATURE_ADVANCED_LIGHT_GROUPS,
    CONF_FEATURE_GROUP_CREATION,
    CONF_INCLUDE_ENTITIES,
    CONF_LIGHT_CONTROL,
    CONF_TYPE,
    DATA_AREA_OBJECT,
    DEFAULT_FAN_CONTROL,
    DEFAULT_LIGHT_CONTROL,
    DOMAIN,
    EVENT_MAGICAREAS_AREA_READY,
    EVENT_MAGICAREAS_READY,
    MAGIC_AREAS_COMPONENTS,
    MAGIC_AREAS_COMPONENTS_META,
    MAGIC_DEVICE_ID_PREFIX,
    META_AREA_GLOBAL,
    MODULE_DATA,
)
from ..util import is_entity_list

_LOGGER = logging.getLogger(__name__)


class ControlType(StrEnum):
    """The control type to check."""

    Light = "light"
    Fan = "fan"
    System = "system"


@dataclass
class MagicEvent:
    """The data for magic area events."""

    id: str


@dataclass
class StateConfigData:
    """The read config data about the light for each state."""

    name: str
    entity: str | None
    entity_state_on: str
    dim_level: int
    for_state: AreaState
    icon: str
    control_entity: str
    lights: list[str]


class MagicArea(object):  # noqa: UP004
    """The base class for the magic area integration."""

    def __init__(
        self,
        hass: HomeAssistant,
        area: AreaEntry,
        config: ConfigEntry,
    ) -> None:
        """Initialize the magic area with all the stuff."""
        self.hass: HomeAssistant = hass
        self.name: str = area.name
        # Default to the icon for the area.
        self.icon: str = area.icon or "mdi:room"
        self.id: str = area.id
        self.slug: str = slugify(self.name)
        self.hass_config: ConfigEntry = config
        self.initialized: bool = False

        # Merged options
        area_config = dict(config.data)
        if config.options:
            area_config.update(config.options)
        self.config = area_config

        self.entities: dict[str, list[dict[str, str]]] = {}

        self.last_changed: int = datetime.now(UTC)  # type: ignore  # noqa: PGH003
        self.state: AreaState = AreaState.AREA_STATE_CLEAR
        self._state_config: dict[AreaState, StateConfigData] = {}

        self.loaded_platforms: list[str] = []

    async def initialize(self) -> None:
        """Initialise the simply magic area."""
        _LOGGER.debug("%s: Initializing area", self.slug)  # type: ignore  # noqa: PGH003

        await self._load_entities()

        await self._load_state_config()

        self._finalize_init()

    def areas_loaded(self) -> bool:
        """Return the state of the area being loaded."""
        if MODULE_DATA not in self.hass.data:
            return False

        data = self.hass.data[MODULE_DATA]
        for area_info in data.values():
            area = area_info[DATA_AREA_OBJECT]
            if not area.is_meta():
                if not area.initialized:
                    return False
        return True

    def _finalize_init(self) -> None:
        self.initialized = True

        self.hass.bus.async_fire(EVENT_MAGICAREAS_AREA_READY, {"id": self.id})  # type: ignore  # noqa: PGH003

        if not self.is_meta():
            # Check if we finished loading all areas
            if self.areas_loaded():
                self.hass.bus.async_fire(EVENT_MAGICAREAS_READY)  # type: ignore  # noqa: PGH003

        area_type = "Meta-Area" if self.is_meta() else "Area"
        _LOGGER.debug("%s: %s initialized", self.slug, area_type)  # type: ignore # noqa: PGH003

    def state_config(self, state: AreaState) -> StateConfigData | None:
        """Return the light entity config for the current state."""
        return self._state_config[state]

    def all_state_configs(self) -> dict[AreaState, StateConfigData]:
        """Return the dictionary with all the currently configured state configs."""
        return self._state_config

    def has_configured_state(self, state: AreaState) -> bool:
        """If the area has the specified configured state."""
        return state in self._state_config

    def has_feature(self, feature: str) -> bool:
        """If the area has the specified feature."""
        enabled_features = self.config.get(CONF_ENABLED_FEATURES, {})

        # Handle everything else
        if not isinstance(enabled_features, dict):
            _LOGGER.warning(  # type: ignore  # noqa: PGH003
                "%s: Invalid configuration for %s",
                self.name,
                CONF_ENABLED_FEATURES,
            )

        if feature not in enabled_features:
            enabled_aggregations = self.config.get(CONF_ENABLED_FEATURES, {}).get(
                CONF_FEATURE_GROUP_CREATION, {}
            )
            return feature in enabled_aggregations
        return True

    def feature_config(self, feature: str) -> dict[str, Any]:
        """Get the feature config for the specified feature."""
        if not self.has_feature(feature):
            if feature != CONF_FEATURE_ADVANCED_LIGHT_GROUPS:
                _LOGGER.debug("%s: Feature %s not enabled", self.name, feature)  # type: ignore  # noqa: PGH003
            return {}

        options = self.config.get(CONF_ENABLED_FEATURES, {})

        if not options:
            _LOGGER.debug("%s: No feature config found for %s", self.name, feature)  # type: ignore  # noqa: PGH003

        return options.get(feature, {})

    def available_platforms(self) -> list[str]:
        """Return the available platforms for this area."""
        available_platforms = []

        if self.is_meta():
            available_platforms = MAGIC_AREAS_COMPONENTS_META
        else:
            available_platforms = MAGIC_AREAS_COMPONENTS

        return available_platforms

    @property
    def area_type(self) -> str:
        """Type type of the area."""
        return self.config.get(CONF_TYPE) or AREA_TYPE_INTERIOR

    def is_meta(self) -> bool:
        """If this is a meta area."""
        return self.area_type == AREA_TYPE_META

    def is_interior(self) -> bool:
        """If this is an interior area."""
        return self.area_type == AREA_TYPE_INTERIOR

    def is_exterior(self) -> bool:
        """If this is an exterior area."""
        return self.area_type == AREA_TYPE_EXTERIOR

    def _is_magic_area_entity(self, entity: RegistryEntry) -> bool:
        """Return if entity belongs to this integration instance."""
        return entity.config_entry_id == self.hass_config.entry_id

    def _should_exclude_entity(self, entity: RegistryEntry) -> bool:
        """Exclude entity."""
        return (
            entity.config_entry_id == self.hass_config.entry_id  # Is magic_area entity
            or entity.disabled  # Is disabled
            or entity.entity_id  # In excluded list
            in self.feature_config(CONF_FEATURE_ADVANCED_LIGHT_GROUPS).get(
                CONF_EXCLUDE_ENTITIES, []
            )
        )

    async def _load_entities(self) -> None:
        """Load entities that belong to this area."""
        entity_list: list[str] = []
        magic_area_entities: list[str] = []
        include_entities: list[str] = self.feature_config(
            CONF_FEATURE_ADVANCED_LIGHT_GROUPS
        ).get(CONF_INCLUDE_ENTITIES, [])

        entity_registry = async_get_er(self.hass)
        device_registry = async_get_dr(self.hass)

        # Add entities from devices in this area
        devices_in_area = device_registry.devices.get_devices_for_area_id(self.id)
        for device in devices_in_area:
            entity_list.extend(
                [
                    entity.entity_id
                    for entity in entity_registry.entities.get_entries_for_device_id(
                        device.id
                    )
                    if not self._should_exclude_entity(entity)
                ]
            )

        # Add entities that are specifically set as this area but device is not or has no device.
        entities_in_area = entity_registry.entities.get_entries_for_area_id(self.id)
        entity_list.extend(
            [
                entity.entity_id
                for entity in entities_in_area
                if entity.entity_id not in entity_list
                and not self._should_exclude_entity(entity)
            ]
        )

        # Add magic are entities
        entities_for_config_id = (
            entity_registry.entities.get_entries_for_config_entry_id(
                self.hass_config.entry_id
            )
        )
        magic_area_entities.extend(  # type: ignore  # noqa: PGH003
            [entity.entity_id for entity in entities_for_config_id]
        )

        _LOGGER.debug(  # type: ignore  # noqa: PGH003
            "Area ID - %s, Entities - %s",
            self.id,
            entity_list,
        )

        if include_entities and isinstance(include_entities, list):  # type: ignore  # noqa: PGH003
            entity_list.extend(include_entities)

        self._load_entity_list("", entity_list)
        self._load_entity_list(DOMAIN, magic_area_entities)

        _LOGGER.debug("%s: Loaded entities for area  %s", self.slug, self.entities)  # type: ignore  # noqa: PGH003

    def _load_entity_list(self, prefix: str, entity_list: list[str]) -> None:
        for entity_id in entity_list:
            try:
                entity_component, entity_name = entity_id.split(".")

                # Get latest state and create object
                latest_state = self.hass.states.get(entity_id)
                updated_entity = {ATTR_ENTITY_ID: entity_id}

                if latest_state:
                    # Need to exclude entity_id if present but latest_state.attributes
                    # is a ReadOnlyDict so we can't remove it, need to iterate and select
                    # all keys that are NOT entity_id
                    for attr_key, attr_value in latest_state.attributes.items():
                        if attr_key != ATTR_ENTITY_ID:
                            updated_entity[attr_key] = attr_value

                # Ignore groups
                if is_entity_list(updated_entity[ATTR_ENTITY_ID]):
                    _LOGGER.debug(  # type: ignore  # noqa: PGH003
                        "%s: %s is probably a group, skipping",
                        self.slug,
                        entity_id,
                    )
                    continue

                if prefix + entity_component not in self.entities:
                    self.entities[prefix + entity_component] = []

                self.entities[prefix + entity_component].append(updated_entity)

            except Exception as err:  # noqa: BLE001
                _LOGGER.error(  # type: ignore  # noqa: PGH003
                    "%s: Unable to load entity '%s': {%s}",
                    self.slug,
                    entity_id,
                    str(err),
                )

    async def _initialize(self, _=None) -> None:
        _LOGGER.debug("%s: Initializing area", self.slug)  # type: ignore  # noqa: PGH003

        await self._load_entities()

        await self._load_state_config()

        self._finalize_init()

    def has_entities(self, domain: str) -> bool:
        """Check and see if this areas has entites with the specified domain."""
        return domain in self.entities

    async def _load_state_config(self) -> None:
        light_entities = []
        if LIGHT_DOMAIN in self.entities:
            light_entities = [e[ATTR_ENTITY_ID] for e in self.entities[LIGHT_DOMAIN]]

        for lg in ALL_LIGHT_ENTITIES:
            entity_ob: str | None = None
            base = self.config
            if lg.is_advanced:
                base = self.feature_config(CONF_FEATURE_ADVANCED_LIGHT_GROUPS)
            if lg.has_entity:
                entity_ob = base.get(lg.entity_name())
                if entity_ob is None:
                    continue
            lights = self.feature_config(CONF_FEATURE_ADVANCED_LIGHT_GROUPS).get(
                lg.advanced_lights_to_control(), light_entities
            )
            if not lights:
                lights = light_entities
            self._state_config[lg.enable_state] = StateConfigData(
                name=lg.name,
                entity=entity_ob,
                entity_state_on=self.feature_config(
                    CONF_FEATURE_ADVANCED_LIGHT_GROUPS
                ).get(lg.advanced_state_check(), "on"),
                dim_level=int(
                    self.feature_config(CONF_FEATURE_ADVANCED_LIGHT_GROUPS).get(
                        lg.state_dim_level(), lg.default_dim_level
                    )
                ),
                for_state=lg.enable_state,
                icon=lg.icon,
                control_entity=self.simply_magic_entity_id(
                    SWITCH_DOMAIN, EntityNames.LIGHT_CONTROL
                ),
                lights=lights,
            )

    def is_control_enabled(self, control_type: ControlType) -> bool:
        """If the area has controled turned on for simply magic areas."""
        entity_id = ""
        if control_type == ControlType.Fan:
            return self.config.get(CONF_FAN_CONTROL, DEFAULT_FAN_CONTROL)
        if control_type == ControlType.Light:
            return self.config.get(CONF_LIGHT_CONTROL, DEFAULT_LIGHT_CONTROL)

        entity_id = self.simply_magic_entity_id(
            SWITCH_DOMAIN, EntityNames.SYSTEM_CONTROL
        )
        switch_entity = self.hass.states.get(entity_id)
        if switch_entity:
            return switch_entity.state.lower() == STATE_ON  # type: ignore  # noqa: PGH003
        return True

    def simply_magic_entity_id(
        self, domain: str, name: str, area_name: str | None = None
    ):
        """Return the name for the entity."""
        if area_name is None:
            area_name = self.name
        return f"{domain}.{MAGIC_DEVICE_ID_PREFIX}{slugify(name)}_{slugify(area_name)}"

    def get_active_areas(self) -> list[str]:
        """Return the active areas for the magic area, always empty for non-meta area."""
        return []

    def get_child_areas(self) -> list[str]:
        """Return the child areas for the magic area, always empty for non-meta area."""
        return []


class MagicMetaArea(MagicArea):
    """Class for the meta simply magic areas that contain other areas."""

    def _areas_loaded(self, hass: HomeAssistant | None = None) -> bool:
        hass_object = hass if hass else self.hass

        if MODULE_DATA not in hass_object.data:
            return False

        data = hass_object.data[MODULE_DATA]
        for area_info in data.values():
            area = area_info[DATA_AREA_OBJECT]
            if area.config.get(CONF_TYPE) != AREA_TYPE_META:
                if not area.initialized:
                    return False

        return True

    def get_active_areas(self) -> list[str]:
        """Get the currently active areas."""
        areas = self.get_child_areas()
        active_areas: list[str] = []

        for area in areas:
            try:
                entity_id = f"binary_sensor.area_{area}"
                entity = self.hass.states.get(entity_id)

                if entity and entity.state == STATE_ON:
                    active_areas.append(area)
            except Exception as e:  # noqa: BLE001
                _LOGGER.error(  # type: ignore  # noqa: PGH003
                    "%s: Unable to get active area state for %s: %s",
                    self.slug,
                    area,
                    str(e),
                )

        return active_areas

    def get_child_areas(self) -> list[str]:
        """Get the child areas."""
        data = self.hass.data[MODULE_DATA]
        areas: list[str] = []

        for area_info in data.values():
            area = area_info[DATA_AREA_OBJECT]
            if (
                self.id == META_AREA_GLOBAL.lower()  # type: ignore  # noqa: PGH003
                or area.config.get(CONF_TYPE) == self.id
            ) and not area.is_meta():
                areas.append(area.slug)

        return areas

    async def _initialize(self, _=None) -> None:
        if self.initialized:
            _LOGGER.warning("%s: Meta-Area Already initialized, ignoring", self.name)  # type: ignore  # noqa: PGH003
            return

        # Meta-areas need to wait until other simply magic areas are loaded.
        if not self._areas_loaded():
            _LOGGER.warning(  # type: ignore  # noqa: PGH003
                "%s: Meta-Area Non-meta areas not loaded. This shouldn't happen",
                self.name,
            )
            return

        _LOGGER.debug("%s: Initializing meta area", self.name)  # type: ignore  # noqa: PGH003

        await self._load_entities()

        self._finalize_init()

    async def _load_entities(self) -> None:
        entity_list: list[str] = []

        data = self.hass.data[MODULE_DATA]
        for area_info in data.values():
            area = area_info[DATA_AREA_OBJECT]
            if (
                self.id == META_AREA_GLOBAL.lower()  # type: ignore  # noqa: PGH003
                or area.config.get(CONF_TYPE) == self.id
            ):
                for entities in area.entities.values():
                    for entity in entities:
                        if not isinstance(entity["entity_id"], str):
                            _LOGGER.debug(  # type: ignore  # noqa: PGH003
                                "%s: Entity ID is not a string: %s (probably a group, skipping)",
                                self.slug,
                                entity["entity_id"],
                            )
                            continue

                        # Skip excluded entities
                        if entity["entity_id"] in self.feature_config(
                            CONF_FEATURE_ADVANCED_LIGHT_GROUPS
                        ).get(CONF_EXCLUDE_ENTITIES, []):
                            continue

                        entity_list.append(entity["entity_id"])

        self._load_entity_list("", entity_list)

        _LOGGER.debug("%s: Loaded entities for meta area %s", self.slug, self.entities)  # type: ignore  # noqa: PGH003
