"""Tests for media_player platform."""

from homeassistant.components.media_player import (
    ATTR_MEDIA_CONTENT_ID,
    ATTR_MEDIA_CONTENT_TYPE,
    SERVICE_PLAY_MEDIA,
    SERVICE_TURN_ON,
)
from homeassistant.components.media_player import (
    DOMAIN as MEDIA_PLAYER_DOMAIN,
)
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers.area_registry import async_get as async_get_ar
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from ..config.area_state import AreaState
from ..const import (
    AREA_TYPE_META,
    CONF_ENABLED_FEATURES,
    CONF_FEATURE_AREA_AWARE_MEDIA_PLAYER,
    CONF_ID,
    CONF_MEDIA_PLAYER_GROUPS,
    CONF_NAME,
    CONF_NOTIFICATION_DEVICES,
    CONF_NOTIFY_STATES,
    CONF_TYPE,
    DOMAIN,
    META_AREA_GLOBAL,
)
from .conftest import AREA_NAME, CONFIG_ENTRY_DATA
from .mocks import MockMediaPlayer


async def test_media_player_no_feature(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    one_media_player: list[MockMediaPlayer],
    _setup_integration: None,
) -> None:
    """Test media player group not created when feature disabled."""
    area_media_player = hass.states.get(
        f"{MEDIA_PLAYER_DOMAIN}.simply_magic_areas_media_player_kitchen"
    )
    assert area_media_player is None


async def test_media_player_group_creation_and_state(
    hass: HomeAssistant,
    one_media_player: list[MockMediaPlayer],
) -> None:
    """Test area media player group creation, turn off on clear."""
    data = dict(CONFIG_ENTRY_DATA)
    data[CONF_ENABLED_FEATURES] = {
        **data.get(CONF_ENABLED_FEATURES, {}),
        CONF_MEDIA_PLAYER_GROUPS: {},
    }
    entry = MockConfigEntry(domain=DOMAIN, data=data)
    entry.add_to_hass(hass)
    registry = async_get_ar(hass)
    registry.async_get_or_create(AREA_NAME)

    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    group_entity_id = f"{MEDIA_PLAYER_DOMAIN}.simply_magic_areas_media_kitchen"
    state = hass.states.get(group_entity_id)
    assert state is not None
    assert state.state == STATE_OFF

    # Turn media player on
    await hass.services.async_call(
        MEDIA_PLAYER_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: group_entity_id},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert one_media_player[0].state == STATE_ON

    # Set area state to clear -> should turn off media player
    state_select_entity_id = f"{SENSOR_DOMAIN}.simply_magic_areas_state_kitchen"
    hass.states.async_set(
        state_select_entity_id,
        AreaState.AREA_STATE_CLEAR,
    )
    await hass.async_block_till_done()
    assert one_media_player[0].state == STATE_OFF

    # Unload
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_area_aware_media_player(
    hass: HomeAssistant,
    one_media_player: list[MockMediaPlayer],
) -> None:
    """Test area-aware media player (AAMP) forwarding."""
    # 1. Setup kitchen area with AAMP enabled and notification device configured
    kitchen_data = dict(CONFIG_ENTRY_DATA)
    kitchen_data[CONF_ENABLED_FEATURES] = {
        **kitchen_data.get(CONF_ENABLED_FEATURES, {}),
        CONF_MEDIA_PLAYER_GROUPS: {},
        CONF_FEATURE_AREA_AWARE_MEDIA_PLAYER: {
            CONF_NOTIFICATION_DEVICES: ["media_player.media_player"],
            CONF_NOTIFY_STATES: [AreaState.AREA_STATE_OCCUPIED],
        },
    }
    kitchen_entry = MockConfigEntry(domain=DOMAIN, data=kitchen_data)
    kitchen_entry.add_to_hass(hass)

    # 2. Setup Global Meta Area with CONF_MEDIA_PLAYER_GROUPS
    global_data = {
        CONF_NAME: META_AREA_GLOBAL,
        CONF_ID: META_AREA_GLOBAL.lower(),
        CONF_TYPE: AREA_TYPE_META,
        CONF_ENABLED_FEATURES: {
            CONF_MEDIA_PLAYER_GROUPS: {},
        },
    }
    global_entry = MockConfigEntry(domain=DOMAIN, data=global_data)
    global_entry.add_to_hass(hass)

    registry = async_get_ar(hass)
    registry.async_get_or_create(AREA_NAME)

    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    aamp_entity_id = (
        f"{MEDIA_PLAYER_DOMAIN}.simply_magic_areas_area_aware_media_player_global"
    )
    aamp_state = hass.states.get(aamp_entity_id)
    assert aamp_state is not None

    # Set kitchen state select to clear -> play_media should not forward
    state_select_entity_id = f"{SENSOR_DOMAIN}.simply_magic_areas_state_kitchen"
    hass.states.async_set(state_select_entity_id, AreaState.AREA_STATE_CLEAR)
    await hass.async_block_till_done()

    await hass.services.async_call(
        MEDIA_PLAYER_DOMAIN,
        SERVICE_PLAY_MEDIA,
        {
            ATTR_ENTITY_ID: aamp_entity_id,
            ATTR_MEDIA_CONTENT_ID: "https://example.com/sound.mp3",
            ATTR_MEDIA_CONTENT_TYPE: "music",
        },
        blocking=True,
    )
    await hass.async_block_till_done()

    # Now set kitchen state to occupied
    hass.states.async_set(state_select_entity_id, AreaState.AREA_STATE_OCCUPIED)
    # Also ensure kitchen area object recognizes occupied state
    from ..const import DATA_AREA_OBJECT, MODULE_DATA
    kitchen_area = hass.data[MODULE_DATA][kitchen_entry.entry_id][DATA_AREA_OBJECT]
    kitchen_area.states = [AreaState.AREA_STATE_OCCUPIED]

    await hass.services.async_call(
        MEDIA_PLAYER_DOMAIN,
        SERVICE_PLAY_MEDIA,
        {
            ATTR_ENTITY_ID: aamp_entity_id,
            ATTR_MEDIA_CONTENT_ID: "https://example.com/sound.mp3",
            ATTR_MEDIA_CONTENT_TYPE: "music",
        },
        blocking=True,
    )
    assert len(one_media_player[0].played_media) == 1
    assert one_media_player[0].played_media[0][0] == "music"
    assert one_media_player[0].played_media[0][1] == "https://example.com/sound.mp3"

    # Clean up entries
    await hass.config_entries.async_unload(global_entry.entry_id)
    await hass.config_entries.async_unload(kitchen_entry.entry_id)
    await hass.async_block_till_done()
