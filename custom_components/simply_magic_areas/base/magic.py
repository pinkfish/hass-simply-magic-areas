"""The device setup for the simply magic areas."""

import logging
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from homeassistant.components.light import DOMAIN as LIGHT_DOMAIN
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_ENTITY_ID,
    ATTR_UNIT_OF_MEASUREMENT,
    LIGHT_LUX,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.area_registry import AreaEntry
from homeassistant.helpers.device_registry import async_get as async_get_dr
from homeassistant.helpers.entity_registry import (
    RegistryEntry,
)
from homeassistant.helpers.entity_registry import (
    async_get as async_get_er,
)
from homeassistant.util import slugify

from ..config.area_state import AreaState, LightingProfile
from ..config.entity_names import EntityNames
from ..const import (
    ALL_LIGHT_ENTITIES,
    AREA_TYPE_BEDROOM,
    AREA_TYPE_EXTERIOR,
    AREA_TYPE_INTERIOR,
    AREA_TYPE_META,
    ATTR_PRESENCE_SENSORS,
    BEDROOM_AREA_KEYWORDS,
    BINARY_SENSOR_DOMAIN,
    CONF_BRIGHT_LUX_THRESHOLD,
    CONF_CHILD_AREAS,
    CONF_ENABLED_FEATURES,
    CONF_EXCLUDE_ENTITIES,
    CONF_FAN_CONTROL,
    CONF_FEATURE_ADVANCED_LIGHT_GROUPS,
    CONF_FEATURE_GROUP_CREATION,
    CONF_FEATURE_HUMIDITY,
    CONF_ID,
    CONF_INCLUDE_ENTITIES,
    CONF_LIGHT_CONTROL,
    CONF_MQTT_ROOM_PRESENCE,
    CONF_NAME,
    CONF_PRESENCE_DEVICE_PLATFORMS,
    CONF_PRESENCE_SENSOR_DEVICE_CLASS,
    CONF_REFERENCE_BRIGHT_AREA,
    CONF_REFERENCE_ILLUMINANCE_SENSOR,
    CONF_SLEEP_MODE,
    CONF_TYPE,
    CONF_UNOCCUPIED_WHEN_LIGHTS_OFF,
    CONTACT_SENSOR_DEVICE_CLASSES,
    DATA_AREA_OBJECT,
    DEFAULT_BRIGHT_LUX_THRESHOLD,
    DEFAULT_FAN_CONTROL,
    DEFAULT_LIGHT_CONTROL,
    DEFAULT_MQTT_ROOM_PRESENCE,
    DEFAULT_PRESENCE_DEVICE_PLATFORMS,
    DEFAULT_PRESENCE_DEVICE_SENSOR_CLASS,
    DEFAULT_SLEEP_MODE,
    DEFAULT_UNOCCUPIED_WHEN_LIGHTS_OFF,
    DOMAIN,
    EVENT_MAGICAREAS_AREA_READY,
    EVENT_MAGICAREAS_READY,
    INHERITABLE_CONF_KEYS,
    MAGIC_AREAS_COMPONENTS,
    MAGIC_AREAS_COMPONENTS_META,
    MAGIC_DEVICE_ID_PREFIX,
    META_AREA_BEDROOMS,
    META_AREA_EXTERIOR,
    META_AREA_GLOBAL,
    META_AREA_INTERIOR,
    META_AREAS,
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


class InheritedConfig(dict):
    """Dictionary proxy that falls back to parent meta-area configuration for inheritable keys."""

    def __init__(self, area: "MagicArea", initial_data: dict) -> None:
        """Initialize inherited config wrapper."""
        super().__init__(initial_data)
        self._area = area

    def get(self, key: Any, default: Any = None) -> Any:
        """Get value with fallback to parent meta-area if key is inheritable."""
        val = super().get(key)
        if val not in (None, ""):
            if isinstance(val, str) and val.lower() in ("none", "disabled"):
                return None
            return val

        if key in INHERITABLE_CONF_KEYS:
            inherited = self._area.get_inherited_value(key)
            if inherited not in (None, ""):
                return inherited

        return default

    def __getitem__(self, key: Any) -> Any:
        """Get item with fallback to parent meta-area if key is inheritable."""
        val = super().get(key)
        if val not in (None, ""):
            if isinstance(val, str) and val.lower() in ("none", "disabled"):
                return None
            return val

        if key in INHERITABLE_CONF_KEYS:
            inherited = self._area.get_inherited_value(key)
            if inherited not in (None, ""):
                return inherited

        if key in self:
            return super().__getitem__(key)

        raise KeyError(key)

    def __contains__(self, key: Any) -> bool:
        """Check if key exists either locally or via inheritance."""
        val = super().get(key)
        if val not in (None, ""):
            return not (isinstance(val, str) and val.lower() in ("none", "disabled"))
        if key in INHERITABLE_CONF_KEYS:
            return self._area.get_inherited_value(key) not in (None, "")
        return super().__contains__(key)


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
        self.config = InheritedConfig(self, area_config)

        self.entities: dict[str, list[dict[str, str]]] = {}

        self.last_changed: int = datetime.now(UTC)  # type: ignore
        self.state: AreaState = AreaState.AREA_STATE_CLEAR
        self.lighting_profile: LightingProfile = LightingProfile.STANDARD
        self.sleep_mode_enabled: bool = bool(
            self.config.get(CONF_SLEEP_MODE, DEFAULT_SLEEP_MODE)
        )
        self.system_control_enabled: bool = True
        self._system_control_switch: Any = None
        self._state_sensor: Any = None
        self._light_group: Any = None
        self._fan_group: Any = None
        self._state_config: dict[AreaState | LightingProfile | str, StateConfigData] = {}

        self.loaded_platforms: list[str] = []

    async def initialize(self) -> None:
        """Initialise the simply magic area."""
        _LOGGER.debug("%s: Initializing area", self.slug)  # type: ignore

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
            if not area.is_meta() and not area.initialized:
                return False
        return True

    def _finalize_init(self) -> None:
        self.initialized = True

        self.hass.bus.async_fire(EVENT_MAGICAREAS_AREA_READY, {"id": self.id})  # type: ignore

        if not self.is_meta() and self.areas_loaded():
            self.hass.bus.async_fire(EVENT_MAGICAREAS_READY)  # type: ignore

        area_type = "Meta-Area" if self.is_meta() else "Area"
        _LOGGER.debug("%s: %s initialized", self.slug, area_type)  # type: ignore

    def all_state_configs(self) -> dict[AreaState | LightingProfile | str, StateConfigData]:
        """Return the dictionary with all the currently configured state configs."""
        return self._state_config

    def has_configured_state(self, state: AreaState | LightingProfile | str) -> bool:
        """If the area has the specified configured state."""
        return state in self._state_config

    def state_config(self, state: AreaState | LightingProfile | str) -> StateConfigData | None:
        """Return the state config for the given state if configured."""
        return self._state_config.get(state)

    def has_feature(self, feature: str) -> bool:
        """If the area has the specified feature."""
        enabled_features = self.config.get(CONF_ENABLED_FEATURES, {})

        # Handle everything else
        if not isinstance(enabled_features, dict):
            _LOGGER.warning(  # type: ignore
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
            if feature not in (
                CONF_FEATURE_ADVANCED_LIGHT_GROUPS,
                CONF_FEATURE_HUMIDITY,
            ):
                _LOGGER.debug("%s: Feature %s not enabled", self.name, feature)  # type: ignore
            return {}

        options = self.config.get(CONF_ENABLED_FEATURES, {})

        if not options:
            _LOGGER.debug("%s: No feature config found for %s", self.name, feature)  # type: ignore

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
        return self.area_type in (AREA_TYPE_INTERIOR, AREA_TYPE_BEDROOM)

    def is_exterior(self) -> bool:
        """If this is an exterior area."""
        return self.area_type == AREA_TYPE_EXTERIOR

    def get_other_magic_area_devices(self) -> list[dict[str, Any]]:
        """Return metadata for all other configured non-meta Simply Magic Area devices."""
        if not self.hass:
            return []

        reserved_meta_ids = {m.lower() for m in META_AREAS}
        my_id = str(getattr(self, "id", "") or "").lower()
        my_slug = str(getattr(self, "slug", "") or "").lower()

        devices_by_slug: dict[str, dict[str, Any]] = {}

        def _register_candidate(
            area_id: str,
            area_name: str,
            area_slug: str | None = None,
            entry_id: str | None = None,
            device_id: str | None = None,
        ) -> None:
            norm_id = str(area_id or "").strip()
            norm_name = str(area_name or norm_id).strip()
            slug = str(area_slug or slugify(norm_name or norm_id)).lower()
            if not slug or slug == my_slug or norm_id.lower() == my_id:
                return
            if norm_id.lower() in reserved_meta_ids or slug in reserved_meta_ids:
                return

            rec = devices_by_slug.get(slug)
            if rec is None:
                rec = {
                    "slug": slug,
                    "id": norm_id or slug,
                    "name": norm_name or slug,
                    "device_id": device_id,
                    "entry_id": entry_id,
                    "keys": set(),
                }
                devices_by_slug[slug] = rec
            else:
                if device_id and not rec.get("device_id"):
                    rec["device_id"] = device_id
                if entry_id and not rec.get("entry_id"):
                    rec["entry_id"] = entry_id

            keys: set[str] = rec["keys"]
            for candidate in (
                slug,
                norm_id.lower(),
                norm_name.lower(),
                norm_name.lower().replace(" ", "_"),
                f"{MAGIC_DEVICE_ID_PREFIX}{slug}",
                f"{MAGIC_DEVICE_ID_PREFIX}{norm_id.lower()}",
                f"sensor.{MAGIC_DEVICE_ID_PREFIX}state_{slug}",
                f"light.{MAGIC_DEVICE_ID_PREFIX}light_{slug}",
                f"switch.{MAGIC_DEVICE_ID_PREFIX}sleep_mode_{slug}",
                f"switch.{MAGIC_DEVICE_ID_PREFIX}system_control_{slug}",
                f"binary_sensor.{MAGIC_DEVICE_ID_PREFIX}occupancy_{slug}",
            ):
                if candidate:
                    keys.add(candidate)
            if device_id:
                keys.add(str(device_id).lower())
            if entry_id:
                keys.add(str(entry_id).lower())

        # 1. Loaded MagicArea objects in MODULE_DATA
        ma_data = self.hass.data.get(MODULE_DATA, {})
        if isinstance(ma_data, dict):
            for entry_key, entry_info in ma_data.items():
                if not isinstance(entry_info, dict):
                    continue
                other = entry_info.get(DATA_AREA_OBJECT)
                if other is None or other is self or other.is_meta():
                    continue
                _register_candidate(
                    area_id=getattr(other, "id", ""),
                    area_name=getattr(other, "name", ""),
                    area_slug=getattr(other, "slug", None),
                    entry_id=str(
                        getattr(getattr(other, "hass_config", None), "entry_id", "")
                        or entry_key
                    ),
                )

        # 2. Configured Simply Magic Area ConfigEntries
        if hasattr(self.hass, "config_entries") and hasattr(
            self.hass.config_entries, "async_entries"
        ):
            for entry in self.hass.config_entries.async_entries(DOMAIN):
                if entry.data.get(CONF_TYPE) == AREA_TYPE_META:
                    continue
                c_id = str(entry.data.get(CONF_ID) or "")
                c_name = str(entry.data.get(CONF_NAME) or entry.title or c_id)
                if not c_id and not c_name:
                    continue
                _register_candidate(
                    area_id=c_id or slugify(c_name),
                    area_name=c_name,
                    entry_id=entry.entry_id,
                )

        # 3. Simply Magic Area devices in Home Assistant's Device Registry
        try:
            dev_reg = async_get_dr(self.hass)
            for dev in dev_reg.devices.values():
                for domain, ident in dev.identifiers:
                    if domain == DOMAIN and ident.startswith(MAGIC_DEVICE_ID_PREFIX):
                        raw_area_id = ident[len(MAGIC_DEVICE_ID_PREFIX) :]
                        dev_name = dev.name_by_user or dev.name or raw_area_id
                        _register_candidate(
                            area_id=raw_area_id,
                            area_name=dev_name,
                            device_id=dev.id,
                        )
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Device registry lookup skipped: %s", err)

        return sorted(
            devices_by_slug.values(),
            key=lambda item: (str(item["name"]).lower(), str(item["slug"])),
        )

    def resolve_child_magic_area_slugs(self, raw_children: Any = None) -> list[str]:
        """Resolve a list of child area identifiers strictly against other Simply Magic Area devices."""
        if raw_children is None:
            raw_children = self.config.get(CONF_CHILD_AREAS)
        if not raw_children or not isinstance(raw_children, (list, tuple, set)):
            return []

        other_devices = self.get_other_magic_area_devices()
        if not other_devices:
            return []

        resolved: list[str] = []
        for raw in raw_children:
            if not raw:
                continue
            token = str(raw).strip().lower()
            token_slug = slugify(token)
            for dev in other_devices:
                if token in dev["keys"] or (token_slug and token_slug in dev["keys"]):
                    if dev["slug"] not in resolved:
                        resolved.append(dev["slug"])
                    break
        return resolved

    def is_bedroom_meta(self) -> bool:
        """Return True if this is a bedroom meta-area."""
        if not self.is_meta():
            return False
        slug = (self.slug or "").lower()
        area_id = (self.id or "").lower()
        return (
            area_id in (META_AREA_BEDROOMS.lower(), "bedroom")
            or "bedroom" in slug
            or bool(self.config.get(CONF_CHILD_AREAS))
        )

    def is_bedroom_or_ensuite(self) -> bool:
        """Return True if this room is a bedroom or ensuite (tracked independently of main house occupancy)."""
        if self.is_meta():
            return self.is_bedroom_meta()
        if self.is_exterior():
            return False
        if self.area_type == AREA_TYPE_BEDROOM:
            return True
        slug = (self.slug or "").lower()
        name = (self.name or "").lower().replace(" ", "_")
        area_id = (self.id or "").lower()
        my_tokens = {slug, name, area_id}
        if self.hass:
            if MODULE_DATA in self.hass.data:
                for entry_info in self.hass.data[MODULE_DATA].values():
                    if not isinstance(entry_info, dict):
                        continue
                    other = entry_info.get(DATA_AREA_OBJECT)
                    if other and other.is_meta() and other.is_bedroom_meta():
                        configured_children = other.config.get(CONF_CHILD_AREAS) or []
                        if configured_children:
                            resolved_slugs = set(other.resolve_child_magic_area_slugs(configured_children))
                            raw_tokens = {str(c).lower() for c in configured_children if c} | {
                                slugify(str(c)) for c in configured_children if c
                            }
                            if my_tokens & (resolved_slugs | raw_tokens):
                                return True
            if hasattr(self.hass, "config_entries") and hasattr(
                self.hass.config_entries, "async_entries"
            ):
                for entry in self.hass.config_entries.async_entries(DOMAIN):
                    entry_id_val = str(entry.data.get(CONF_ID, "")).lower()
                    if entry_id_val in (META_AREA_BEDROOMS.lower(), "bedroom"):
                        merged = dict(entry.data)
                        if entry.options:
                            merged.update(entry.options)
                        configured_children = merged.get(CONF_CHILD_AREAS) or []
                        if configured_children:
                            raw_tokens = {str(c).lower() for c in configured_children if c} | {
                                slugify(str(c)) for c in configured_children if c
                            }
                            if my_tokens & raw_tokens:
                                return True
        combined = f"{slug} {name} {area_id}"
        return any(kw in combined for kw in BEDROOM_AREA_KEYWORDS)

    def is_main_house_area(self) -> bool:
        """Return True if this room is a main interior house area (excluding bedrooms and ensuites)."""
        return (
            not self.is_meta()
            and self.is_interior()
            and not self.is_bedroom_or_ensuite()
        )

    def set_sleep_mode(self, enabled: bool, propagate: bool = True) -> None:
        """Manually enable or disable sleep mode for this area (and child areas if applicable)."""
        enabled_bool = bool(enabled)
        self.sleep_mode_enabled = enabled_bool
        if isinstance(self.config, dict):
            self.config[CONF_SLEEP_MODE] = enabled_bool

        sleep_switch = getattr(self, "_sleep_mode_switch", None)
        if sleep_switch is not None and hasattr(sleep_switch, "set_synced_state"):
            sleep_switch.set_synced_state(enabled_bool)

        profile_sensor = getattr(self, "_lighting_profile_sensor", None)
        if profile_sensor is not None and hasattr(
            profile_sensor, "set_manual_sleep_mode"
        ):
            profile_sensor.set_manual_sleep_mode(enabled_bool)

        if not propagate or not self.hass or MODULE_DATA not in self.hass.data:
            return

        configured_children = {
            str(c).lower() for c in (self.config.get(CONF_CHILD_AREAS) or []) if c
        }
        if self.is_meta():
            configured_children.update(str(c).lower() for c in self.get_child_areas())

        my_keys = {
            str(self.slug or "").lower(),
            str(self.id or "").lower(),
            str(self.name or "").lower(),
        }

        for entry_info in self.hass.data[MODULE_DATA].values():
            if not isinstance(entry_info, dict):
                continue
            other = entry_info.get(DATA_AREA_OBJECT)
            if other is None or other is self:
                continue

            other_keys = {
                str(getattr(other, "slug", "") or "").lower(),
                str(getattr(other, "id", "") or "").lower(),
                str(getattr(other, "name", "") or "").lower(),
            }
            other_ref = str(
                other.config.get(CONF_REFERENCE_BRIGHT_AREA)
                or other.feature_config(CONF_FEATURE_ADVANCED_LIGHT_GROUPS).get(
                    CONF_REFERENCE_BRIGHT_AREA
                )
                or ""
            ).lower()

            should_propagate = False
            if self.is_meta():
                if self.is_bedroom_meta():
                    should_propagate = (
                        bool(other_keys & configured_children)
                        if self.config.get(CONF_CHILD_AREAS)
                        else other.is_bedroom_or_ensuite()
                    )
                elif self.id.lower() in (
                    META_AREA_INTERIOR.lower(),
                    META_AREA_GLOBAL.lower(),
                ):
                    should_propagate = other.is_main_house_area()
            elif (configured_children and (other_keys & configured_children)) or (
                other_ref and other_ref in my_keys
            ):
                should_propagate = True

            if should_propagate and hasattr(other, "set_sleep_mode"):
                other.set_sleep_mode(enabled_bool, propagate=False)

    def set_house_sleep_mode(self, enabled: bool) -> None:
        """Manually enable or disable sleep mode for the main house (excluding bedrooms)."""
        enabled_bool = bool(enabled)
        if not self.hass or MODULE_DATA not in self.hass.data:
            if self.is_main_house_area() or (
                self.is_meta()
                and self.id.lower()
                in (META_AREA_INTERIOR.lower(), META_AREA_GLOBAL.lower())
            ):
                self.set_sleep_mode(enabled_bool, propagate=False)
            return

        for entry_info in self.hass.data[MODULE_DATA].values():
            if not isinstance(entry_info, dict):
                continue
            other = entry_info.get(DATA_AREA_OBJECT)
            if other is None:
                continue
            if other.is_main_house_area() or (
                other.is_meta()
                and other.id.lower()
                in (META_AREA_INTERIOR.lower(), META_AREA_GLOBAL.lower())
            ):
                other.set_sleep_mode(enabled_bool, propagate=False)

    def set_bedrooms_sleep_mode(self, enabled: bool) -> None:
        """Manually enable or disable sleep mode for all bedrooms and bedroom meta-areas."""
        enabled_bool = bool(enabled)
        if not self.hass or MODULE_DATA not in self.hass.data:
            if self.is_bedroom_or_ensuite() or self.is_bedroom_meta():
                self.set_sleep_mode(enabled_bool, propagate=False)
            return

        for entry_info in self.hass.data[MODULE_DATA].values():
            if not isinstance(entry_info, dict):
                continue
            other = entry_info.get(DATA_AREA_OBJECT)
            if other is None:
                continue
            if other.is_bedroom_or_ensuite() or other.is_bedroom_meta():
                other.set_sleep_mode(enabled_bool, propagate=False)

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

        # Add magic area entities
        magic_area_entities: list[str] = [
            entity.entity_id
            for entity in entity_registry.entities.get_entries_for_config_entry_id(
                self.hass_config.entry_id
            )
        ]

        # Add mqtt room items
        mqtt_room_entities: list[str] = [
            entity.entity_id
            for entity in entity_registry.entities.get_entries_for_config_entry_id(
                "mqtt_room"
            )
        ]

        _LOGGER.debug(  # type: ignore
            "Area ID - %s, Entities - %s",
            self.id,
            entity_list,
        )

        if include_entities and isinstance(include_entities, list):  # type: ignore
            entity_list.extend(include_entities)

        self._load_entity_list("", entity_list)
        self._load_entity_list(DOMAIN, magic_area_entities)
        self._load_entity_list("mqtt_room", mqtt_room_entities)

        _LOGGER.debug("%s: Loaded entities for area  %s", self.slug, self.entities)  # type: ignore

    def _load_entity_list(self, prefix: str, entity_list: list[str]) -> None:
        entity_registry = async_get_er(self.hass)
        for entity_id in entity_list:
            try:
                entity_component, _ = entity_id.split(".")

                # Get latest state and create object
                latest_state = self.hass.states.get(entity_id)
                updated_entity: dict[str, Any] = {ATTR_ENTITY_ID: entity_id}

                if latest_state:
                    # Need to exclude entity_id if present but latest_state.attributes
                    # is a ReadOnlyDict so we can't remove it, need to iterate and select
                    # all keys that are NOT entity_id
                    for attr_key, attr_value in latest_state.attributes.items():
                        if attr_key != ATTR_ENTITY_ID:
                            updated_entity[attr_key] = attr_value

                # Fallback to EntityRegistry metadata if device_class or unit_of_measurement
                # is missing (e.g. sensor is unavailable or uninitialized at startup)
                if (
                    ATTR_DEVICE_CLASS not in updated_entity
                    or ATTR_UNIT_OF_MEASUREMENT not in updated_entity
                ):
                    reg_entry = entity_registry.async_get(entity_id)
                    if reg_entry is not None:
                        if ATTR_DEVICE_CLASS not in updated_entity:
                            dev_class = (
                                reg_entry.device_class
                                or reg_entry.original_device_class
                            )
                            if dev_class:
                                updated_entity[ATTR_DEVICE_CLASS] = dev_class
                        if (
                            ATTR_UNIT_OF_MEASUREMENT not in updated_entity
                            and reg_entry.unit_of_measurement
                        ):
                            updated_entity[ATTR_UNIT_OF_MEASUREMENT] = (
                                reg_entry.unit_of_measurement
                            )

                # Ignore groups
                if is_entity_list(updated_entity[ATTR_ENTITY_ID]):
                    _LOGGER.debug(  # type: ignore
                        "%s: %s is probably a group, skipping",
                        self.slug,
                        entity_id,
                    )
                    continue

                if prefix + entity_component not in self.entities:
                    self.entities[prefix + entity_component] = []

                self.entities[prefix + entity_component].append(updated_entity)

            except Exception as err:  # noqa: BLE001
                _LOGGER.error(  # type: ignore
                    "%s: Unable to load entity '%s': {%s}",
                    self.slug,
                    entity_id,
                    str(err),
                )

    async def _initialize(self, _=None) -> None:
        _LOGGER.debug("%s: Initializing area", self.slug)  # type: ignore

        await self._load_entities()

        await self._load_state_config()

        self._finalize_init()

    def has_entities(self, domain: str) -> bool:
        """Check and see if this areas has entites with the specified domain."""
        return domain in self.entities

    def has_configured_presence_sensors(self) -> bool:
        """Return True if this area has any configured presence or contact sensors."""
        if self.is_meta():
            return len(self.get_child_areas()) > 0

        state_sensor_id = self.simply_magic_entity_id(SENSOR_DOMAIN, EntityNames.STATE)
        state_obj = self.hass.states.get(state_sensor_id)
        if state_obj is not None and ATTR_PRESENCE_SENSORS in state_obj.attributes:
            sensors_attr = state_obj.attributes.get(ATTR_PRESENCE_SENSORS)
            if isinstance(sensors_attr, (list, tuple, set)):
                return len(sensors_attr) > 0

        adv_conf = self.feature_config(CONF_FEATURE_ADVANCED_LIGHT_GROUPS)
        valid_presence_platforms = adv_conf.get(
            CONF_PRESENCE_DEVICE_PLATFORMS, DEFAULT_PRESENCE_DEVICE_PLATFORMS
        )
        configured_classes = (
            set(
                adv_conf.get(
                    CONF_PRESENCE_SENSOR_DEVICE_CLASS,
                    DEFAULT_PRESENCE_DEVICE_SENSOR_CLASS,
                )
            )
            | CONTACT_SENSOR_DEVICE_CLASSES
        )
        include_entities = set(adv_conf.get(CONF_INCLUDE_ENTITIES, []))

        for component, entities in self.entities.items():
            if component == "mqtt_room" + SENSOR_DOMAIN:
                mqtt_room = (
                    adv_conf.get(CONF_MQTT_ROOM_PRESENCE)
                    if self.has_feature(CONF_FEATURE_ADVANCED_LIGHT_GROUPS)
                    else None
                )
                if mqtt_room is None:
                    mqtt_room = self.config.get(
                        CONF_MQTT_ROOM_PRESENCE, DEFAULT_MQTT_ROOM_PRESENCE
                    )
                if mqtt_room and entities:
                    return True
                continue

            if component not in valid_presence_platforms:
                continue

            for entity in entities:
                if not entity:
                    continue
                ent_id = entity.get(ATTR_ENTITY_ID)
                if not ent_id:
                    continue
                if component == BINARY_SENSOR_DOMAIN and ent_id not in include_entities:
                    if ATTR_DEVICE_CLASS not in entity:
                        continue
                    if entity[ATTR_DEVICE_CLASS] not in configured_classes:
                        continue
                return True

        return False

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
                if entity_ob is None and lg.is_advanced:
                    entity_ob = self.config.get(lg.entity_name())
                if entity_ob is None:
                    if lg.enable_state == AreaState.AREA_STATE_BRIGHT and (
                        int(
                            base.get(
                                CONF_BRIGHT_LUX_THRESHOLD,
                                DEFAULT_BRIGHT_LUX_THRESHOLD,
                            )
                        )
                        > 0
                        or base.get(CONF_REFERENCE_BRIGHT_AREA)
                        or base.get(CONF_REFERENCE_ILLUMINANCE_SENSOR)
                    ):
                        entity_ob = None
                    else:
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
                        lg.state_dim_level(),
                        base.get(lg.state_dim_level(), lg.default_dim_level),
                    )
                ),
                for_state=lg.enable_state,
                icon=lg.icon,
                control_entity=self.simply_magic_entity_id(
                    SWITCH_DOMAIN, EntityNames.LIGHT_CONTROL
                ),
                lights=lights,
            )
            if lg.enable_state == AreaState.AREA_STATE_OCCUPIED:
                self._state_config[LightingProfile.STANDARD] = self._state_config[
                    lg.enable_state
                ]
            elif lg.enable_state == AreaState.AREA_STATE_BRIGHT:
                self._state_config[LightingProfile.BRIGHT] = self._state_config[
                    lg.enable_state
                ]
            elif lg.enable_state == AreaState.AREA_STATE_SLEEP:
                self._state_config[LightingProfile.SLEEP] = self._state_config[
                    lg.enable_state
                ]
            elif lg.enable_state == AreaState.AREA_STATE_ACCENTED:
                self._state_config[LightingProfile.ACCENTED] = self._state_config[
                    lg.enable_state
                ]

    def set_system_control(self, enabled: bool, propagate: bool = True) -> None:
        """Enable or disable automated system control for this area (and child areas if meta)."""
        enabled_bool = bool(enabled)
        self.system_control_enabled = enabled_bool

        sys_switch = getattr(self, "_system_control_switch", None)
        if sys_switch is not None and hasattr(sys_switch, "set_synced_state"):
            sys_switch.set_synced_state(enabled_bool)

        light_group = getattr(self, "_light_group", None)
        if (
            not enabled_bool
            and light_group is not None
            and hasattr(light_group, "stop_all_active_control")
        ):
            light_group.stop_all_active_control()

        fan_group = getattr(self, "_fan_group", None)
        if not enabled_bool and fan_group is not None:
            if hasattr(fan_group, "stop_all_active_control"):
                fan_group.stop_all_active_control()
            elif hasattr(fan_group, "_cancel_retry_timer"):
                fan_group._cancel_retry_timer()

        state_sensor = getattr(self, "_state_sensor", None)
        if state_sensor is not None and hasattr(state_sensor, "_update_state"):
            state_sensor._update_state(datetime.now(UTC))

        profile_sensor = getattr(self, "_lighting_profile_sensor", None)
        if (
            enabled_bool
            and profile_sensor is not None
            and hasattr(profile_sensor, "refresh_profile")
        ):
            profile_sensor.refresh_profile()

        if propagate and self.is_meta() and self.hass and MODULE_DATA in self.hass.data:
            area_id_lower = (self.id or "").lower()
            for entry_info in self.hass.data[MODULE_DATA].values():
                if not isinstance(entry_info, dict):
                    continue
                other = entry_info.get(DATA_AREA_OBJECT)
                if other is None or other is self or other.is_meta():
                    continue
                applies = False
                if area_id_lower == META_AREA_GLOBAL.lower():
                    applies = True
                elif area_id_lower == META_AREA_EXTERIOR.lower():
                    applies = other.is_exterior()
                elif area_id_lower == META_AREA_INTERIOR.lower():
                    applies = other.is_interior()
                elif self.is_bedroom_meta():
                    applies = other.is_bedroom_or_ensuite()
                if applies and hasattr(other, "set_system_control"):
                    other.set_system_control(enabled_bool, propagate=False)

    def is_control_enabled(self, control_type: ControlType) -> bool:
        """If the area has controled turned on for simply magic areas."""
        if control_type == ControlType.Fan:
            return self.config.get(CONF_FAN_CONTROL, DEFAULT_FAN_CONTROL)
        if control_type == ControlType.Light:
            return self.config.get(CONF_LIGHT_CONTROL, DEFAULT_LIGHT_CONTROL)

        if not self.is_meta():
            parent_meta_names = [META_AREA_GLOBAL]
            if self.is_exterior():
                parent_meta_names.append(META_AREA_EXTERIOR)
            else:
                parent_meta_names.append(META_AREA_INTERIOR)
                if self.is_bedroom_or_ensuite():
                    parent_meta_names.append(META_AREA_BEDROOMS)
            for meta_name in parent_meta_names:
                meta_sw_id = self.simply_magic_entity_id(
                    SWITCH_DOMAIN, EntityNames.SYSTEM_CONTROL, area_name=meta_name
                )
                meta_sw = self.hass.states.get(meta_sw_id)
                if meta_sw is not None and meta_sw.state.lower() == STATE_OFF:
                    return False

        candidate_ids: list[str] = []
        sw = getattr(self, "_system_control_switch", None)
        if sw is not None and getattr(sw, "entity_id", None):
            candidate_ids.append(sw.entity_id)
        default_id = self.simply_magic_entity_id(
            SWITCH_DOMAIN, EntityNames.SYSTEM_CONTROL
        )
        if default_id not in candidate_ids:
            candidate_ids.append(default_id)
        if getattr(self, "slug", None):
            slug_id = self.simply_magic_entity_id(
                SWITCH_DOMAIN, EntityNames.SYSTEM_CONTROL, area_name=self.slug
            )
            if slug_id not in candidate_ids:
                candidate_ids.append(slug_id)

        for entity_id in candidate_ids:
            switch_entity = self.hass.states.get(entity_id)
            if switch_entity is not None and switch_entity.state not in (
                STATE_UNKNOWN,
                STATE_UNAVAILABLE,
            ):
                return switch_entity.state.lower() == STATE_ON  # type: ignore

        if sw is not None and getattr(sw, "_attr_is_on", None) is not None:
            return bool(sw._attr_is_on)
        if getattr(self, "system_control_enabled", None) is not None:
            return bool(self.system_control_enabled)
        return True

    def simply_magic_entity_id(
        self, domain: str, name: str, area_name: str | None = None
    ):
        """Return the name for the entity."""
        if area_name is None:
            area_name = self.name
        return f"{domain}.{MAGIC_DEVICE_ID_PREFIX}{slugify(name)}_{slugify(area_name)}"

    @staticmethod
    def _parse_valid_lux(state_obj: Any) -> float | None:
        """Parse a finite non-negative illuminance value from a state object."""
        if state_obj and state_obj.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            try:
                val = float(state_obj.state)
                if math.isfinite(val) and val >= 0:
                    return val
            except (ValueError, TypeError):
                pass
        return None

    def get_effective_illuminance(self) -> float | None:
        """Get the current illuminance value for this area from local or reference sensors."""
        # 1. Local illuminance sensor
        local_lux_sensor = self.simply_magic_entity_id(
            SENSOR_DOMAIN, EntityNames.ILLUMINANCE
        )
        val = self._parse_valid_lux(self.hass.states.get(local_lux_sensor))
        if val is not None:
            return val

        if SENSOR_DOMAIN in self.entities:
            for entity in self.entities[SENSOR_DOMAIN]:
                if (
                    entity.get(ATTR_DEVICE_CLASS) == SensorDeviceClass.ILLUMINANCE
                    or entity.get("unit_of_measurement") == LIGHT_LUX
                ):
                    val = self._parse_valid_lux(
                        self.hass.states.get(entity[ATTR_ENTITY_ID])
                    )
                    if val is not None:
                        return val

        # 2. Reference illuminance sensor
        ref_sensor = self.config.get(
            CONF_REFERENCE_ILLUMINANCE_SENSOR,
            self.feature_config(CONF_FEATURE_ADVANCED_LIGHT_GROUPS).get(
                CONF_REFERENCE_ILLUMINANCE_SENSOR
            ),
        )
        if ref_sensor:
            val = self._parse_valid_lux(self.hass.states.get(ref_sensor))
            if val is not None:
                return val

        # 3. Reference area illuminance sensor
        ref_area = self.config.get(
            CONF_REFERENCE_BRIGHT_AREA,
            self.feature_config(CONF_FEATURE_ADVANCED_LIGHT_GROUPS).get(
                CONF_REFERENCE_BRIGHT_AREA
            ),
        )
        if ref_area:
            ref_lux_sensor = self.simply_magic_entity_id(
                SENSOR_DOMAIN, EntityNames.ILLUMINANCE, area_name=ref_area
            )
            val = self._parse_valid_lux(self.hass.states.get(ref_lux_sensor))
            if val is not None:
                return val

            if MODULE_DATA in self.hass.data:
                for entry in self.hass.data[MODULE_DATA].values():
                    area = entry.get(DATA_AREA_OBJECT)
                    if (
                        area
                        and (
                            area.id == ref_area
                            or area.slug == ref_area
                            or area.name == ref_area
                        )
                        and SENSOR_DOMAIN in area.entities
                    ):
                        for entity in area.entities[SENSOR_DOMAIN]:
                            if (
                                entity.get(ATTR_DEVICE_CLASS)
                                == SensorDeviceClass.ILLUMINANCE
                                or entity.get("unit_of_measurement") == LIGHT_LUX
                            ):
                                val = self._parse_valid_lux(
                                    self.hass.states.get(entity[ATTR_ENTITY_ID])
                                )
                                if val is not None:
                                    return val

        return None

    def get_parent_meta_areas(self) -> list["MagicArea"]:
        """Return parent meta areas in priority order: zone meta area first, then global."""
        if self.is_meta():
            if self.id.lower() != META_AREA_GLOBAL.lower():
                global_meta = self._get_meta_area_by_id(META_AREA_GLOBAL.lower())
                return [global_meta] if global_meta else []
            return []

        parents = []
        if self.is_bedroom_or_ensuite():
            bedroom_meta = self._get_meta_area_by_id(META_AREA_BEDROOMS.lower())
            if bedroom_meta:
                parents.append(bedroom_meta)
        # Direct parent: Interior or Exterior
        parent_id = (
            META_AREA_EXTERIOR.lower()
            if self.is_exterior()
            else META_AREA_INTERIOR.lower()
        )
        parent_meta = self._get_meta_area_by_id(parent_id)
        if parent_meta and parent_meta not in parents:
            parents.append(parent_meta)

        # Root parent: Global
        global_meta = self._get_meta_area_by_id(META_AREA_GLOBAL.lower())
        if global_meta and global_meta not in parents:
            parents.append(global_meta)

        return parents

    def _get_meta_area_by_id(self, meta_id: str) -> "MagicArea | None":
        """Get loaded meta area object by id."""
        if MODULE_DATA in self.hass.data:
            for entry in self.hass.data[MODULE_DATA].values():
                area = entry.get(DATA_AREA_OBJECT)
                if area and area.is_meta() and area.id.lower() == meta_id.lower():
                    return area
        return None

    def get_inherited_value(self, key: str) -> Any:
        """Look up a configuration key from parent meta areas."""
        if not hasattr(self, "_resolving_inheritance"):
            self._resolving_inheritance = set()

        if key in self._resolving_inheritance:
            return None

        self._resolving_inheritance.add(key)
        try:
            # 1. Search loaded meta-areas in MODULE_DATA
            for parent in self.get_parent_meta_areas():
                val = parent.config.get(key)
                if val not in (None, ""):
                    return val

            # 2. Fallback to config_entries in case parent meta-area instance is not in MODULE_DATA yet
            parent_ids = []
            if not self.is_meta():
                if self.is_bedroom_or_ensuite():
                    parent_ids.append(META_AREA_BEDROOMS.lower())
                parent_ids.append(
                    META_AREA_EXTERIOR.lower()
                    if self.is_exterior()
                    else META_AREA_INTERIOR.lower()
                )
            if self.id.lower() != META_AREA_GLOBAL.lower():
                parent_ids.append(META_AREA_GLOBAL.lower())

            if hasattr(self.hass, "config_entries") and hasattr(
                self.hass.config_entries, "async_entries"
            ):
                for entry in self.hass.config_entries.async_entries(DOMAIN):
                    entry_id = str(entry.data.get(CONF_ID, "")).lower()
                    if entry_id in parent_ids:
                        merged = dict(entry.data)
                        if entry.options:
                            merged.update(entry.options)
                        val = merged.get(key)
                        if val not in (None, ""):
                            return val

            return None
        finally:
            self._resolving_inheritance.discard(key)

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
            if area.config.get(CONF_TYPE) != AREA_TYPE_META and not area.initialized:
                return False

        return True

    def _are_all_meta_lights_off(self, child_slugs: list[str]) -> bool:
        """Return True if at least one light exists across the child areas and all are currently OFF."""
        if not self.hass:
            return False
        light_ids: list[str] = []
        for ent in self.entities.get(LIGHT_DOMAIN, []):
            ent_id = ent.get(ATTR_ENTITY_ID)
            if isinstance(ent_id, str) and ent_id not in light_ids:
                light_ids.append(ent_id)
        if MODULE_DATA in self.hass.data:
            for area_info in self.hass.data[MODULE_DATA].values():
                child_area = area_info.get(DATA_AREA_OBJECT)
                if child_area and child_area.slug in child_slugs:
                    lg_id = child_area.simply_magic_entity_id(
                        LIGHT_DOMAIN, EntityNames.LIGHT
                    )
                    if lg_id not in light_ids:
                        light_ids.append(lg_id)
                    for ent in child_area.entities.get(LIGHT_DOMAIN, []):
                        ent_id = ent.get(ATTR_ENTITY_ID)
                        if isinstance(ent_id, str) and ent_id not in light_ids:
                            light_ids.append(ent_id)
        for slug in child_slugs:
            lg_id = self.simply_magic_entity_id(
                LIGHT_DOMAIN, EntityNames.LIGHT, area_name=slug
            )
            if lg_id not in light_ids:
                light_ids.append(lg_id)

        valid_states = []
        for lid in light_ids:
            st = self.hass.states.get(lid)
            if st is not None and st.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE):
                valid_states.append(st.state)
        return bool(valid_states) and all(s == STATE_OFF for s in valid_states)

    def get_active_areas(self) -> list[str]:
        """Get the currently active areas."""
        areas = self.get_child_areas()
        if (
            self.is_bedroom_meta()
            and self.config.get(
                CONF_UNOCCUPIED_WHEN_LIGHTS_OFF, DEFAULT_UNOCCUPIED_WHEN_LIGHTS_OFF
            )
            and self._are_all_meta_lights_off(areas)
        ):
            return []

        active_areas: list[str] = []

        if MODULE_DATA in self.hass.data:
            for area_info in self.hass.data[MODULE_DATA].values():
                child_area = area_info.get(DATA_AREA_OBJECT)
                if (
                    child_area
                    and child_area.slug in areas
                    and child_area.state != AreaState.AREA_STATE_CLEAR
                ):
                    active_areas.append(child_area.slug)

        return active_areas

    def get_child_areas(self) -> list[str]:
        """Get the child areas (strictly from other Simply Magic Area devices)."""
        data = self.hass.data.get(MODULE_DATA, {})
        configured_children = self.config.get(CONF_CHILD_AREAS)
        if configured_children and isinstance(configured_children, (list, tuple, set)):
            return self.resolve_child_magic_area_slugs(configured_children)

        areas: list[str] = []

        for area_info in data.values():
            area = area_info[DATA_AREA_OBJECT]
            if area.is_meta():
                continue
            if self.is_bedroom_meta():
                if area.is_bedroom_or_ensuite():
                    areas.append(area.slug)
            elif self.id == META_AREA_INTERIOR.lower():  # type: ignore
                # House interior occupancy tracks main areas independently of bedrooms and ensuites
                if area.is_main_house_area():
                    areas.append(area.slug)
            elif self.id == META_AREA_GLOBAL.lower():  # type: ignore
                # House global occupancy tracks main/exterior areas independently of bedrooms and ensuites
                if not area.is_bedroom_or_ensuite():
                    areas.append(area.slug)
            elif area.config.get(CONF_TYPE) == self.id:
                areas.append(area.slug)

        return areas

    async def _initialize(self, _=None) -> None:
        if self.initialized:
            _LOGGER.warning("%s: Meta-Area Already initialized, ignoring", self.name)  # type: ignore
            return

        # Meta-areas need to wait until other simply magic areas are loaded.
        if not self._areas_loaded():
            _LOGGER.warning(  # type: ignore
                "%s: Meta-Area Non-meta areas not loaded. This shouldn't happen",
                self.name,
            )
            return

        _LOGGER.debug("%s: Initializing meta area", self.name)  # type: ignore

        await self._load_entities()

        self._finalize_init()

    async def _load_entities(self) -> None:
        entity_list: list[str] = []
        child_slugs = set(self.get_child_areas())

        data = self.hass.data[MODULE_DATA]
        for area_info in data.values():
            area = area_info[DATA_AREA_OBJECT]
            if not area.is_meta() and (
                area.slug in child_slugs
                or self.id == META_AREA_GLOBAL.lower()  # type: ignore
                or area.config.get(CONF_TYPE) == self.id
            ):
                if (
                    self.id in (META_AREA_INTERIOR.lower(), META_AREA_GLOBAL.lower())
                    and area.is_bedroom_or_ensuite()
                ):
                    continue
                for entities in area.entities.values():
                    for entity in entities:
                        if not isinstance(entity["entity_id"], str):
                            _LOGGER.debug(  # type: ignore
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

        _LOGGER.debug("%s: Loaded entities for meta area %s", self.slug, self.entities)  # type: ignore
