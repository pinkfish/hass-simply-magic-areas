"""Media player pieces for the system."""

import logging

from homeassistant.components.group.media_player import MediaPlayerGroup
from homeassistant.components.media_player import (
    ATTR_MEDIA_CONTENT_ID,
    ATTR_MEDIA_CONTENT_TYPE,
    DOMAIN as MEDIA_PLAYER_DOMAIN,
    SERVICE_PLAY_MEDIA,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
)
from homeassistant.components.select import DOMAIN as SELECT_DOMAIN
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID, SERVICE_TURN_OFF, STATE_IDLE, STATE_ON
from homeassistant.core import Event, EventStateChangedData, HomeAssistant
from homeassistant.helpers.area_registry import AreaEntry, async_get as async_get_ar
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event

from .base.entities import MagicEntity
from .base.magic import MagicArea
from .config.area_state import AreaState
from .config.entity_names import EntityNames
from .const import (
    CONF_FEATURE_AREA_AWARE_MEDIA_PLAYER,
    CONF_MEDIA_PLAYER_GROUPS,
    CONF_NOTIFICATION_DEVICES,
    CONF_NOTIFY_STATES,
    DATA_AREA_OBJECT,
    DEFAULT_NOTIFICATION_DEVICES,
    DEFAULT_NOTIFY_STATES,
    META_AREA_GLOBAL,
    MODULE_DATA,
)

DEPENDENCIES = ["media_player"]

_LOGGER = logging.getLogger(__name__)
ATTR_TRACKED_ENTITY_IDS = "tracked_entity_ids"
ATTR_TRACKED_AREAS = "tracked_areas"


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the media player entities."""
    area: MagicArea = hass.data[MODULE_DATA][config_entry.entry_id][DATA_AREA_OBJECT]

    # Check if we are the Global Meta Area
    if not area.is_meta() or area.id != META_AREA_GLOBAL.lower():
        _LOGGER.debug("%s: Not Global Meta-Area, skipping", area.name)
        return

    # Media Player Groups
    if not area.has_feature(CONF_MEDIA_PLAYER_GROUPS):
        _LOGGER.debug("%s: No media player features", area.name)
        return

    _LOGGER.debug("%s: Setting up media player groups", area.name)

    # Try to setup AAMP
    _LOGGER.debug("%s: Trying to setup AAMP", area.name)

    # Check if there are any media player devices
    if not area.has_entities(MEDIA_PLAYER_DOMAIN):
        _LOGGER.debug("%s: No %s entities for area", area.name, MEDIA_PLAYER_DOMAIN)
        return

    media_player_entities = [
        e[ATTR_ENTITY_ID] for e in area.entities[MEDIA_PLAYER_DOMAIN]
    ]

    async_add_entities([AreaMediaPlayerGroup(area, media_player_entities)])


async def setup_area_aware_media_player(
    hass: HomeAssistant, area: MagicArea, async_add_entities: AddEntitiesCallback
) -> None:
    """Create the area aware media player."""
    registry = async_get_ar(hass)

    # Check if we have areas with MEDIA_PLAYER_DOMAIN entities
    areas_with_media_players = []

    for entry in registry.areas:
        current_area = registry.async_get_area(entry)

        # Skip meta areas
        if current_area.is_meta():
            _LOGGER.debug("%s: Is meta-area, skipping", current_area.name)
            continue

        # Skip areas with feature not enabled
        if not current_area.has_feature(CONF_FEATURE_AREA_AWARE_MEDIA_PLAYER):
            _LOGGER.debug(
                "%s: Does not have AAMP feature enabled, skipping", current_area.name
            )
            continue

        # Skip areas without media player entities
        if not current_area.has_entities(MEDIA_PLAYER_DOMAIN):
            _LOGGER.debug(
                "%s: Has no media player entities, skipping", current_area.name
            )
            continue

        # Skip areas without notification devices set
        notification_devices = current_area.feature_config(
            CONF_FEATURE_AREA_AWARE_MEDIA_PLAYER
        ).get(CONF_NOTIFICATION_DEVICES)

        if not notification_devices:
            _LOGGER.debug(
                "%s: Has no notification devices, skipping", current_area.name
            )
            continue

        # If all passes, we add this valid area to the list
        areas_with_media_players.append(current_area)

    if not areas_with_media_players:
        _LOGGER.debug(
            "%s: No areas with %s entities. Skipping creation of area-aware-media-player",
            "Everywhere",
            MEDIA_PLAYER_DOMAIN,
        )
        return

    area_names = [i.name for i in areas_with_media_players]

    _LOGGER.debug(
        "%s: Setting up area-aware media player with areas: %s", area.name, area_names
    )
    async_add_entities([AreaAwareMediaPlayer(area, areas_with_media_players)])


class AreaAwareMediaPlayer(MagicEntity, MediaPlayerEntity):
    """Media player that is area aware."""

    def __init__(self, area: MagicArea, areas: list[AreaEntry]) -> None:
        """Initialize the area aware media player."""
        MagicEntity.__init__(self, area=area, translation_key=EntityNames.MEDIA_PLAYER)
        MediaPlayerEntity.__init__()

        delattr(self, "_attr_name")

        self._attr_state = STATE_IDLE

        self._areas = areas
        self._tracked_entities: list[str] = []

        for area in self._areas:
            entity_list = self._get_media_players_for_area(area)
            if entity_list:
                self._tracked_entities.extend(entity_list)

        _LOGGER.info("AreaAwareMediaPlayer loaded")

    def _update_attributes(self) -> None:
        self._attr_extra_state_attributes[ATTR_TRACKED_AREAS] = [
            self.area.simply_magic_entity_id(SELECT_DOMAIN, EntityNames.STATE, area)
            for area in self.areas
        ]
        self._attr_extra_state_attributes[ATTR_TRACKED_ENTITY_IDS] = (
            self._tracked_entities
        )

    def _get_media_players_for_area(self, area: MagicArea) -> list[str]:
        entity_ids = []

        notification_devices = area.feature_config(
            CONF_FEATURE_AREA_AWARE_MEDIA_PLAYER
        ).get(CONF_NOTIFICATION_DEVICES, DEFAULT_NOTIFICATION_DEVICES)

        _LOGGER.debug("%s: Notification devices: %s", area.name, notification_devices)

        area_media_players = [
            entity[ATTR_ENTITY_ID] for entity in area.entities[MEDIA_PLAYER_DOMAIN]
        ]

        # Check if media_player entities are notification devices
        entity_ids = [mp for mp in area_media_players if mp in notification_devices]

        return set(entity_ids)

    async def async_added_to_hass(self):
        """Call when entity about to be added to hass."""

        last_state = await self.async_get_last_state()

        if last_state:
            _LOGGER.debug(
                "%s: Media Player restored [state=%s]", self.area.name, last_state.state
            )
            self._attr_state = last_state.state
        else:
            self._attr_state = STATE_IDLE

        self.schedule_update_state()

    @property
    def supported_features(self):
        """Flag media player features that are supported."""
        return MediaPlayerEntityFeature.PLAY_MEDIA

    def _get_active_areas(self):
        active_areas = []

        for area in self.areas:
            area_binary_sensor_name = self.area.simly_magic_entity_id(
                SELECT_DOMAIN, EntityNames.STATE, area.name
            )
            area_binary_sensor_state = self.hass.states.get(area_binary_sensor_name)

            if not area_binary_sensor_state:
                _LOGGER.debug(
                    "%s: No state found for entity %s",
                    area.name,
                    area_binary_sensor_name,
                )
                continue

            # Ignore not occupied areas
            if area_binary_sensor_state.state != STATE_ON:
                continue

            # Check notification states
            notification_states = area.feature_config(
                CONF_FEATURE_AREA_AWARE_MEDIA_PLAYER
            ).get(CONF_NOTIFY_STATES, DEFAULT_NOTIFY_STATES)

            # Check sleep
            if area.has_state(AreaState.AREA_STATE_SLEEP) and (
                AreaState.AREA_STATE_SLEEP not in notification_states
            ):
                continue

            # Check other states
            has_valid_state = False
            for notification_state in notification_states:
                if area.has_state(notification_state):
                    has_valid_state = True

            # Append area
            if has_valid_state:
                active_areas.append(area)

        return active_areas

    def play_media(self, media_type, media_id, **kwargs):
        """Forward a piece of media to media players in active areas."""

        # Read active areas
        active_areas = self.get_active_areas()

        # Fail early
        if not active_areas:
            _LOGGER.info("%s: No areas active. Ignoring", self.area.name)
            return False

        # Gather media_player entities
        media_players = []
        for area in active_areas:
            media_players.extend(self._get_media_players_for_area(area))

        if not media_players:
            _LOGGER.info(
                "%s: No media_player entities to forward. Ignoring", self.area.name
            )
            return False

        data = {
            ATTR_MEDIA_CONTENT_ID: media_id,
            ATTR_MEDIA_CONTENT_TYPE: media_type,
            ATTR_ENTITY_ID: media_players,
        }

        self.hass.services.call(MEDIA_PLAYER_DOMAIN, SERVICE_PLAY_MEDIA, data)

        return True


class AreaMediaPlayerGroup(MagicEntity, MediaPlayerGroup):
    """Media player for the area."""

    def __init__(self, area, entities) -> None:
        """Initialize the media player for the area."""
        MagicEntity.__init__(self, area=area, translation_key=EntityNames.MEDIA_PLAYER)
        MediaPlayerGroup.__init__(self, self.unique_id, "", entities)
        delattr(self, "_attr_name")

        _LOGGER.debug(
            "%s: Media Player group created with entities: %s", area.name, entities
        )

    def _area_state_changed(self, event: Event[EventStateChangedData]) -> None:
        _LOGGER.debug(
            "%s: Media Player group detected area state change", self.area.name
        )

        new_state = event.data["new_state"]
        if new_state is None:
            return

        if new_state.state == AreaState.AREA_STATE_CLEAR:
            _LOGGER.debug("%s:  Area clear, turning off media players", self.area.name)
            self._turn_off()

    def _turn_off(self) -> None:
        service_data = {ATTR_ENTITY_ID: self.entity_id}
        self.hass.services.call(MEDIA_PLAYER_DOMAIN, SERVICE_TURN_OFF, service_data)

    async def async_added_to_hass(self) -> None:
        """Register callbacks."""

        self.async_on_remove(
            async_track_state_change_event(
                self.hass,
                [self.area.simply_magic_entity_id(SELECT_DOMAIN, EntityNames.STATE)],
                self._area_state_change,
            )
        )
        await super().async_added_to_hass()
