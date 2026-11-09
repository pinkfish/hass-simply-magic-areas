"""Tests for door contact edge triggers, actuator sliding backoff retry, bathroom mmWave reliability, and broken device resilience."""

from datetime import UTC, datetime, timedelta

from homeassistant.components.binary_sensor import (
    DOMAIN as BINARY_SENSOR_DOMAIN,
)
from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
)
from homeassistant.components.fan import DOMAIN as FAN_DOMAIN
from homeassistant.components.light import ATTR_BRIGHTNESS
from homeassistant.components.light import DOMAIN as LIGHT_DOMAIN
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_ENTITY_ID,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.simply_magic_areas.base.area_state_sensor import AreaStateSensor
from custom_components.simply_magic_areas.base.magic import StateConfigData
from custom_components.simply_magic_areas.climate import AreaClimateGroup
from custom_components.simply_magic_areas.config.area_state import (
    AreaState,
    LightingProfile,
)
from custom_components.simply_magic_areas.const import (
    ATTR_ARRIVAL_PREWARM_ACTIVE,
    ATTR_ARRIVAL_PREWARM_REASON,
    ATTR_MANUAL_CONTROL,
    ATTR_SLEEP_WAKEUP_BOOST,
    ATTR_STATE,
    CONF_CLEAR_TIMEOUT,
    CONF_EXTENDED_TIMEOUT,
    CONF_ON_STATES,
    DATA_AREA_OBJECT,
    MODULE_DATA,
)
from custom_components.simply_magic_areas.fan import AreaFanGroup
from custom_components.simply_magic_areas.light import AreaLightGroup
from custom_components.simply_magic_areas.media_player import AreaMediaPlayerGroup
from custom_components.simply_magic_areas.ml.mmwave_filter import MMWaveFanFilter


def _make_area_state_sensor(
    hass: HomeAssistant,
    area_slug: str = "bathroom",
    clear_timeout: int = 60,
    extended_timeout: int = 60,
) -> AreaStateSensor:
    """Create a lightweight AreaStateSensor instance for unit testing."""
    from unittest.mock import MagicMock

    area = MagicMock()
    area.hass = hass
    area.name = area_slug.capitalize()
    area.slug = area_slug
    area.id = area_slug
    area.state = AreaState.AREA_STATE_CLEAR
    area.is_meta.return_value = False
    area.is_control_enabled.return_value = True
    area.config = {
        CONF_CLEAR_TIMEOUT: clear_timeout,
        CONF_EXTENDED_TIMEOUT: extended_timeout,
    }
    area.feature_config.return_value = {
        CONF_ON_STATES: [STATE_ON],
    }
    area.entities = {}
    area.simply_magic_entity_id.side_effect = (
        lambda domain, name, area_name=None: f"{domain}.simply_magic_areas_{name}_{area_name or area_slug}"
    )

    sensor = AreaStateSensor(area)
    sensor.hass = hass
    sensor._attr_name = f"{area.name} State"
    sensor.entity_id = f"sensor.simply_magic_areas_state_{area_slug}"
    sensor.schedule_update_ha_state = MagicMock()
    return sensor


async def test_door_contact_sensor_edge_triggers_only(hass: HomeAssistant) -> None:
    """Test that door contact sensors only trigger occupancy on state transitions and never hold static occupancy."""
    sensor = _make_area_state_sensor(
        hass, area_slug="bathroom", clear_timeout=30, extended_timeout=30
    )
    door_id = "binary_sensor.bathroom_door"
    hass.states.async_set(
        door_id,
        STATE_ON,
        {ATTR_DEVICE_CLASS: BinarySensorDeviceClass.DOOR},
    )
    sensor.area.entities = {
        BINARY_SENSOR_DOMAIN: [
            {
                ATTR_ENTITY_ID: door_id,
                ATTR_DEVICE_CLASS: BinarySensorDeviceClass.DOOR,
            }
        ]
    }
    sensor._load_presence_sensors()
    assert door_id in sensor._sensors

    # 1. Static ON (door left wide open) must NOT hold the area occupied
    assert sensor._get_sensors_state() is False
    assert sensor.get_current_area_state() == AreaState.AREA_STATE_CLEAR

    await sensor._setup_listeners()

    # 2. Door closes (ON -> OFF): edge trigger transitions area to OCCUPIED and starts clear_timeout
    hass.states.async_set(
        door_id,
        STATE_OFF,
        {ATTR_DEVICE_CLASS: BinarySensorDeviceClass.DOOR},
    )
    await hass.async_block_till_done()
    assert sensor.area.state == AreaState.AREA_STATE_OCCUPIED
    assert sensor._is_on_clear_timeout() is True

    # 3. Door opens (OFF -> ON) while area is extended: edge trigger returns area to OCCUPIED
    sensor._last_off_time = datetime.now(UTC) - timedelta(seconds=35)
    sensor._update_state()
    assert sensor.area.state == AreaState.AREA_STATE_EXTENDED

    hass.states.async_set(
        door_id,
        STATE_ON,
        {ATTR_DEVICE_CLASS: BinarySensorDeviceClass.DOOR},
    )
    await hass.async_block_till_done()
    assert sensor.area.state == AreaState.AREA_STATE_OCCUPIED
    assert sensor._is_on_clear_timeout() is True

    # 4. Even while the door stays open (STATE_ON), after clear + extended timeout elapses,
    # the area transitions back to CLEAR because static door state never holds occupancy.
    sensor._last_off_time = datetime.now(UTC) - timedelta(seconds=70)
    sensor._update_state()
    assert sensor.area.state == AreaState.AREA_STATE_CLEAR

    # 5. Door sensor going unavailable or recovering from unavailable does NOT trigger occupancy
    hass.states.async_set(
        door_id,
        STATE_UNAVAILABLE,
        {ATTR_DEVICE_CLASS: BinarySensorDeviceClass.DOOR},
    )
    await hass.async_block_till_done()
    assert sensor.area.state == AreaState.AREA_STATE_CLEAR

    hass.states.async_set(
        door_id,
        STATE_ON,
        {ATTR_DEVICE_CLASS: BinarySensorDeviceClass.DOOR},
    )
    await hass.async_block_till_done()
    assert sensor.area.state == AreaState.AREA_STATE_CLEAR

    sensor._cleanup_timers()


async def test_broken_presence_sensor_does_not_stick_area_occupied(
    hass: HomeAssistant,
) -> None:
    """Test that an active presence sensor going unavailable updates _last_off_time and schedules clear timeout."""
    sensor = _make_area_state_sensor(
        hass, area_slug="bathroom", clear_timeout=30, extended_timeout=30
    )
    mmwave_id = "binary_sensor.bathroom_mmwave"
    hass.states.async_set(
        mmwave_id,
        STATE_ON,
        {ATTR_DEVICE_CLASS: BinarySensorDeviceClass.OCCUPANCY},
    )
    sensor.area.entities = {
        BINARY_SENSOR_DOMAIN: [
            {
                ATTR_ENTITY_ID: mmwave_id,
                ATTR_DEVICE_CLASS: BinarySensorDeviceClass.OCCUPANCY,
            }
        ]
    }
    sensor._load_presence_sensors()
    await sensor._setup_listeners()

    sensor._update_state()
    assert sensor.area.state == AreaState.AREA_STATE_OCCUPIED

    # Now the sensor crashes/disconnects (STATE_ON -> STATE_UNAVAILABLE)
    hass.states.async_set(
        mmwave_id,
        STATE_UNAVAILABLE,
        {ATTR_DEVICE_CLASS: BinarySensorDeviceClass.OCCUPANCY},
    )
    await hass.async_block_till_done()

    # Area should have scheduled a clear timeout rather than remaining stuck occupied forever
    assert sensor._is_on_clear_timeout() is True

    # Once clear + extended timeout elapses, area clears
    sensor._last_off_time = datetime.now(UTC) - timedelta(seconds=65)
    sensor._update_state()
    assert sensor.area.state == AreaState.AREA_STATE_CLEAR
    sensor._cleanup_timers()


async def test_mmwave_filter_single_sensor_and_fresh_entry_not_suppressed(
    hass: HomeAssistant,
) -> None:
    """Test that a bathroom with a single mmWave sensor (or broken companion) is not silenced when fan is running."""
    from unittest.mock import MagicMock

    area = MagicMock()
    area.hass = hass
    area.slug = "bathroom"
    area.simply_magic_entity_id.side_effect = (
        lambda domain, name, area_name=None: f"{domain}.simply_magic_areas_{name}_{area_name or 'bathroom'}"
    )

    fan_group_id = "fan.simply_magic_areas_fan_bathroom"
    hass.states.async_set(fan_group_id, STATE_ON)

    mmwave_id = "binary_sensor.bathroom_mmwave_presence"
    door_id = "binary_sensor.bathroom_door"
    broken_pir_id = "binary_sensor.bathroom_pir"

    hass.states.async_set(
        mmwave_id,
        STATE_ON,
        {ATTR_DEVICE_CLASS: BinarySensorDeviceClass.PRESENCE},
    )
    hass.states.async_set(
        door_id,
        STATE_OFF,
        {ATTR_DEVICE_CLASS: BinarySensorDeviceClass.DOOR},
    )
    hass.states.async_set(
        broken_pir_id,
        STATE_UNAVAILABLE,
        {ATTR_DEVICE_CLASS: BinarySensorDeviceClass.MOTION},
    )

    filt = MMWaveFanFilter(hass, area)
    # Simulate fan running for 60s
    filt._last_fan_on_ts = datetime.now(UTC).timestamp() - 60.0

    mmwave_state = hass.states.get(mmwave_id)
    assert mmwave_state is not None

    # 1. With only a door contact sensor and a broken PIR sensor alongside the mmWave sensor,
    # has_available_companion is False, so the mmWave sensor must NOT be suppressed!
    is_fan, _, _ = filt.evaluate_radar_signals(
        mmwave_id,
        mmwave_state,
        all_area_sensors=[mmwave_id, door_id, broken_pir_id],
    )
    assert is_fan is False

    # 2. Even if an available companion PIR sensor is OFF, if the mmWave sensor JUST turned ON
    # (< 30s ago, after the fan started), it is a fresh human entry and must NOT be suppressed!
    hass.states.async_set(
        broken_pir_id,
        STATE_OFF,
        {ATTR_DEVICE_CLASS: BinarySensorDeviceClass.MOTION},
    )
    fresh_mmwave_state = hass.states.get(mmwave_id)
    assert fresh_mmwave_state is not None
    is_fan_fresh, _, _ = filt.evaluate_radar_signals(
        mmwave_id,
        fresh_mmwave_state,
        all_area_sensors=[mmwave_id, broken_pir_id],
    )
    assert is_fan_fresh is False


async def test_light_zwave_delayed_report_and_extended_reentry_and_sliding_backoff(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    one_light,
    one_motion,
    _setup_integration,
) -> None:
    """Test Z-Wave delayed report immunity, extended->occupied re-entry, and sliding backoff retries."""
    _ = (one_light, one_motion, _setup_integration)
    area = hass.data[MODULE_DATA][config_entry.entry_id][DATA_AREA_OBJECT]
    light_group_id = "light.simply_magic_areas_light_kitchen"
    member_light_id = "light.test_5678"

    # Find AreaLightGroup entity instance
    entity_comp = hass.data.get("entity_components", {}).get(LIGHT_DOMAIN)
    light_group: AreaLightGroup | None = None
    if entity_comp:
        for ent in entity_comp.entities:
            if ent.entity_id == light_group_id:
                light_group = ent
                break
    assert light_group is not None

    # 1. Test that delayed Z-Wave turn-on + brightness report within grace window does NOT set manual_control
    hass.states.async_set(member_light_id, STATE_OFF)
    conf = StateConfigData(
        name="occupied",
        entity=None,
        entity_state_on=STATE_ON,
        dim_level=80,
        for_state=AreaState.AREA_STATE_OCCUPIED,
        icon="mdi:lightbulb",
        control_entity="switch.simply_magic_areas_light_control_kitchen",
        lights=[member_light_id],
    )
    light_group._turn_on_light(conf, force=True)
    await hass.async_block_till_done()

    # Simulate physical Z-Wave dimmer reporting ON with slightly rounded brightness 200ms later
    expected_br = int(80 * 255 / 100)
    hass.states.async_set(
        light_group_id,
        STATE_ON,
        {ATTR_BRIGHTNESS: expected_br - 1},
    )
    await hass.async_block_till_done()
    assert light_group._is_controlled_by_this_entity() is True
    assert light_group.extra_state_attributes.get(ATTR_MANUAL_CONTROL) is not True

    light_group._cancel_retry_timer()

    # 2. Test manual-off override clears immediately when entering EXTENDED (unoccupied) and turns back ON when OCCUPIED
    light_group._set_controlled_by_this_entity(False)
    light_group._attr_is_on = False
    state_sensor_id = "sensor.simply_magic_areas_state_kitchen"
    hass.states.async_set(state_sensor_id, AreaState.AREA_STATE_EXTENDED)
    await hass.async_block_till_done()
    assert light_group._is_controlled_by_this_entity() is True

    turn_on_calls = async_mock_service(hass, LIGHT_DOMAIN, SERVICE_TURN_ON)
    hass.states.async_set(state_sensor_id, AreaState.AREA_STATE_OCCUPIED)
    await hass.async_block_till_done()
    assert light_group._is_controlled_by_this_entity() is True
    assert len(turn_on_calls) >= 1
    light_group._cancel_retry_timer()

    # Also verify manual-off -> CLEAR -> OCCUPIED resets and turns light ON
    turn_on_calls.clear()
    light_group._set_controlled_by_this_entity(False)
    light_group._attr_is_on = False
    hass.states.async_set(state_sensor_id, AreaState.AREA_STATE_CLEAR)
    await hass.async_block_till_done()
    assert light_group._is_controlled_by_this_entity() is True
    hass.states.async_set(state_sensor_id, AreaState.AREA_STATE_OCCUPIED)
    await hass.async_block_till_done()

    assert light_group._is_controlled_by_this_entity() is True
    assert len(turn_on_calls) >= 1
    light_group._cancel_retry_timer()

    # 3. Test sliding backoff retry when Z-Wave drops initial turn_on packet
    turn_on_calls.clear()
    hass.states.async_set(member_light_id, STATE_OFF)
    light_group._attr_is_on = False
    light_group._turn_on_light(conf, force=True)
    await hass.async_block_till_done()
    assert len(turn_on_calls) == 1

    # Underlying member_light_id is still STATE_OFF (dropped Z-Wave packet).
    # Fire retry #1 at +2s
    light_group._execute_actuator_retry()
    await hass.async_block_till_done()
    assert len(turn_on_calls) == 2
    assert light_group._retry_attempt == 1

    # Still STATE_OFF -> Fire retry #2 at +5s
    light_group._execute_actuator_retry()
    await hass.async_block_till_done()
    assert len(turn_on_calls) == 3
    assert light_group._retry_attempt == 2

    # Now Z-Wave light finally turns ON -> next retry check sees no unresponsive lights and stops
    hass.states.async_set(member_light_id, STATE_ON, {ATTR_BRIGHTNESS: expected_br})
    light_group._execute_actuator_retry()
    await hass.async_block_till_done()
    assert len(turn_on_calls) == 3
    assert light_group._retry_attempt == 0
    light_group._cancel_retry_timer()
    _ = area


async def test_broken_light_and_broken_fan_resilience(hass: HomeAssistant) -> None:
    """Test that one broken (unavailable) light or fan does not block working devices or cause endless retries."""
    from unittest.mock import MagicMock

    area = MagicMock()
    area.hass = hass
    area.name = "Bathroom"
    area.slug = "bathroom"
    area.id = "bathroom"
    area.state = AreaState.AREA_STATE_OCCUPIED
    area.is_meta.return_value = False
    area.is_exterior.return_value = False
    area.is_control_enabled.return_value = True
    area.config = {}
    area.feature_config.return_value = {}
    area.get_effective_illuminance.return_value = 0.0
    area.entities = {
        LIGHT_DOMAIN: [
            {ATTR_ENTITY_ID: "light.bath_working"},
            {ATTR_ENTITY_ID: "light.bath_broken"},
        ],
        FAN_DOMAIN: [
            {ATTR_ENTITY_ID: "fan.bath_working"},
            {ATTR_ENTITY_ID: "fan.bath_broken"},
        ],
        SENSOR_DOMAIN: [
            {
                ATTR_ENTITY_ID: "sensor.bath_temp_working",
                ATTR_DEVICE_CLASS: SensorDeviceClass.TEMPERATURE,
            },
            {
                ATTR_ENTITY_ID: "sensor.bath_temp_broken",
                ATTR_DEVICE_CLASS: SensorDeviceClass.TEMPERATURE,
            },
            {
                ATTR_ENTITY_ID: "sensor.bath_hum_working",
                ATTR_DEVICE_CLASS: SensorDeviceClass.HUMIDITY,
            },
            {
                ATTR_ENTITY_ID: "sensor.bath_hum_broken",
                ATTR_DEVICE_CLASS: SensorDeviceClass.HUMIDITY,
            },
        ],
    }
    area.simply_magic_entity_id.side_effect = (
        lambda domain, name, area_name=None: f"{domain}.simply_magic_areas_{name}_{area_name or 'bathroom'}"
    )

    # Set one light & one fan working, one light & one fan unavailable
    hass.states.async_set("light.bath_working", STATE_OFF)
    hass.states.async_set("light.bath_broken", STATE_UNAVAILABLE)
    hass.states.async_set("fan.bath_working", STATE_OFF)
    hass.states.async_set("fan.bath_broken", STATE_UNAVAILABLE)
    hass.states.async_set("sensor.bath_temp_working", "22.5")
    hass.states.async_set("sensor.bath_temp_broken", STATE_UNAVAILABLE)
    hass.states.async_set("sensor.bath_hum_working", "65.0")
    hass.states.async_set("sensor.bath_hum_broken", STATE_UNAVAILABLE)

    light_group = AreaLightGroup(
        area, ["light.bath_working", "light.bath_broken"]
    )
    light_group.hass = hass
    light_group._attr_name = "Bathroom Lights"
    light_group.entity_id = "light.simply_magic_areas_light_bathroom"
    light_group.schedule_update_ha_state = MagicMock()
    light_group.async_update_group_state = MagicMock()

    # Working light is OFF, broken light is UNAVAILABLE -> _get_unresponsive_lights(STATE_ON) only lists working light
    assert light_group._get_unresponsive_lights(STATE_ON) == ["light.bath_working"]

    # Once working light turns ON, _get_unresponsive_lights(STATE_ON) is empty (broken light is ignored)
    hass.states.async_set("light.bath_working", STATE_ON)
    assert light_group._get_unresponsive_lights(STATE_ON) == []
    assert light_group._has_any_working_light_on() is True
    assert light_group._has_any_working_light_off() is False

    # Test FanGroup with one broken fan and fallback sensors
    fan_group = AreaFanGroup(area, ["fan.bath_working", "fan.bath_broken"])
    fan_group.hass = hass
    fan_group._attr_name = "Bathroom Fans"
    fan_group.entity_id = "fan.simply_magic_areas_fan_bathroom"
    fan_group.schedule_update_ha_state = MagicMock()

    fan_turn_on_calls = async_mock_service(hass, FAN_DOMAIN, SERVICE_TURN_ON)
    fan_group._turn_on_fan()
    await hass.async_block_till_done()
    assert len(fan_turn_on_calls) == 1

    # Working fan hasn't turned on yet -> retry #1 fires
    fan_group._execute_actuator_retry()
    await hass.async_block_till_done()
    assert len(fan_turn_on_calls) == 2

    # Working fan turns ON while broken fan stays UNAVAILABLE -> no more retries
    hass.states.async_set("fan.bath_working", STATE_ON)
    fan_group._execute_actuator_retry()
    await hass.async_block_till_done()
    assert len(fan_turn_on_calls) == 2
    assert fan_group._retry_attempt == 0

    # Verify fallback to working individual temperature & humidity sensors when aggregate sensor is missing
    assert fan_group._get_area_temperature() == 22.5
    assert fan_group._get_area_humidity() == 65.0

    light_group._cancel_retry_timer()
    fan_group._cancel_retry_timer()
    fan_group._stop_watchdog_timer()


async def test_sensorless_room_lighting_profiles_and_night_dimming(
    hass: HomeAssistant,
) -> None:
    """Test that a room with no presence sensors follows sleep/bright/standard profiles and dims when switched on at night."""
    from unittest.mock import MagicMock

    area = MagicMock()
    area.hass = hass
    area.name = "Guest Bedroom"
    area.slug = "guest_bedroom"
    area.id = "guest_bedroom"
    area.state = AreaState.AREA_STATE_CLEAR
    area.lighting_profile = LightingProfile.STANDARD
    area.is_meta.return_value = False
    area.is_exterior.return_value = False
    area.is_control_enabled.return_value = True
    area.has_configured_presence_sensors.return_value = False
    area.has_configured_state.side_effect = (
        lambda st: st == AreaState.AREA_STATE_OCCUPIED
    )
    area.state_config.side_effect = lambda st: (
        StateConfigData(
            name="occupied",
            entity=None,
            entity_state_on=STATE_ON,
            dim_level=100,
            for_state=AreaState.AREA_STATE_OCCUPIED,
            icon="mdi:lightbulb",
            control_entity="switch.simply_magic_areas_light_control_guest_bedroom",
            lights=["light.guest_bedroom_light"],
        )
        if st == AreaState.AREA_STATE_OCCUPIED
        else None
    )
    area.config = {}
    area.feature_config.return_value = {
        "sleep_state_dim": 25,
        "bright_state_dim": 0,
    }
    area.get_effective_illuminance.return_value = 0.0
    area.entities = {
        LIGHT_DOMAIN: [{ATTR_ENTITY_ID: "light.guest_bedroom_light"}],
    }
    area.simply_magic_entity_id.side_effect = (
        lambda domain, name, area_name=None: (
            f"{domain}.simply_magic_areas_{name}_{area_name or 'guest_bedroom'}"
        )
    )

    state_sensor_id = "sensor.simply_magic_areas_state_guest_bedroom"
    profile_sensor_id = "sensor.simply_magic_areas_lighting_profile_guest_bedroom"
    light_group_id = "light.simply_magic_areas_light_guest_bedroom"

    hass.states.async_set(
        state_sensor_id,
        AreaState.AREA_STATE_CLEAR,
        {"presence_sensors": []},
    )
    hass.states.async_set(profile_sensor_id, LightingProfile.STANDARD)
    hass.states.async_set("light.guest_bedroom_light", STATE_OFF)

    light_group = AreaLightGroup(area, ["light.guest_bedroom_light"])
    light_group.hass = hass
    light_group._attr_name = "Guest Bedroom Lights"
    light_group.entity_id = light_group_id
    light_group.schedule_update_ha_state = MagicMock()
    light_group.async_update_group_state = MagicMock()

    assert light_group._has_presence_sensors() is False

    turn_on_calls = async_mock_service(hass, LIGHT_DOMAIN, SERVICE_TURN_ON)
    turn_off_calls = async_mock_service(hass, LIGHT_DOMAIN, "turn_off")

    # 1. When lights are OFF in a sensorless room, profile change to SLEEP keeps lights OFF
    area.lighting_profile = LightingProfile.SLEEP
    light_group._profile_change(
        MagicMock(
            data={
                "old_state": MagicMock(state=LightingProfile.STANDARD),
                "new_state": MagicMock(state=LightingProfile.SLEEP),
            }
        )
    )
    await hass.async_block_till_done()
    assert len(turn_on_calls) == 0

    # 2. When light is switched ON at the wall during SLEEP mode, it immediately dims to sleep_state_dim (25% -> 63)
    hass.states.async_set(
        "light.guest_bedroom_light", STATE_ON, {ATTR_BRIGHTNESS: 255}
    )
    light_group._update_group_state(
        MagicMock(
            data={
                "old_state": MagicMock(state=STATE_OFF, attributes={}),
                "new_state": MagicMock(
                    state=STATE_ON, attributes={ATTR_BRIGHTNESS: 255}
                ),
            }
        )
    )
    await hass.async_block_till_done()
    assert len(turn_on_calls) == 1
    assert turn_on_calls[-1].data[ATTR_BRIGHTNESS] == int(25 * 255 / 100)
    light_group._cancel_retry_timer()

    # 3. When light is already ON in a sensorless room and profile changes STANDARD -> SLEEP, it dims to 25%
    turn_on_calls.clear()
    area.lighting_profile = LightingProfile.STANDARD
    light_group._attr_is_on = True
    hass.states.async_set(
        "light.guest_bedroom_light", STATE_ON, {ATTR_BRIGHTNESS: 255}
    )
    area.lighting_profile = LightingProfile.SLEEP
    light_group._profile_change(
        MagicMock(
            data={
                "old_state": MagicMock(state=LightingProfile.STANDARD),
                "new_state": MagicMock(state=LightingProfile.SLEEP),
            }
        )
    )
    await hass.async_block_till_done()
    assert len(turn_on_calls) == 1
    assert turn_on_calls[-1].data[ATTR_BRIGHTNESS] == int(25 * 255 / 100)
    light_group._cancel_retry_timer()

    # 4. When profile changes SLEEP -> BRIGHT while light is ON, on/off is manual so it NEVER turns off;
    #    instead it restores the dim level to 100% (255)
    turn_on_calls.clear()
    area.lighting_profile = LightingProfile.BRIGHT
    light_group._profile_change(
        MagicMock(
            data={
                "old_state": MagicMock(state=LightingProfile.SLEEP),
                "new_state": MagicMock(state=LightingProfile.BRIGHT),
            }
        )
    )
    await hass.async_block_till_done()
    assert len(turn_off_calls) == 0
    assert len(turn_on_calls) == 1
    assert turn_on_calls[-1].data[ATTR_BRIGHTNESS] == 255
    light_group._cancel_retry_timer()

    # 5. Manual brightness override while ON is respected for non-sleep profile changes (BRIGHT -> STANDARD),
    #    but entering SLEEP mode always auto-dims currently-ON lights to sleep_state_dim (25%)
    turn_on_calls.clear()
    light_group.last_update_from_entity = False
    light_group._command_in_flight_until = 0.0
    light_group._update_group_state(
        MagicMock(
            data={
                "old_state": MagicMock(
                    state=STATE_ON, attributes={ATTR_BRIGHTNESS: 255}
                ),
                "new_state": MagicMock(
                    state=STATE_ON, attributes={ATTR_BRIGHTNESS: 180}
                ),
            }
        )
    )
    assert light_group._is_controlled_by_this_entity() is False

    # Profile change BRIGHT -> STANDARD while in manual brightness override is ignored
    area.lighting_profile = LightingProfile.STANDARD
    light_group._profile_change(
        MagicMock(
            data={
                "old_state": MagicMock(state=LightingProfile.BRIGHT),
                "new_state": MagicMock(state=LightingProfile.STANDARD),
            }
        )
    )
    await hass.async_block_till_done()
    assert len(turn_on_calls) == 0
    assert len(turn_off_calls) == 0

    # Entering SLEEP mode (STANDARD -> SLEEP) while light is currently ON always auto-dims to 25%
    area.lighting_profile = LightingProfile.SLEEP
    light_group._profile_change(
        MagicMock(
            data={
                "old_state": MagicMock(state=LightingProfile.STANDARD),
                "new_state": MagicMock(state=LightingProfile.SLEEP),
            }
        )
    )
    await hass.async_block_till_done()
    assert len(turn_on_calls) == 1
    assert turn_on_calls[-1].data[ATTR_BRIGHTNESS] == int(25 * 255 / 100)
    assert len(turn_off_calls) == 0
    light_group._cancel_retry_timer()

    # Even direct _turn_off_light(force=True) never turns off a sensorless room's light
    light_group._turn_off_light(force=True)
    await hass.async_block_till_done()
    assert len(turn_off_calls) == 0

    # 6. Turning light OFF externally in a sensorless room resets manual_control so future profile changes work
    light_group.last_update_from_entity = False
    light_group._command_in_flight_until = 0.0
    light_group._update_group_state(
        MagicMock(
            data={
                "old_state": MagicMock(state=STATE_ON, attributes={}),
                "new_state": MagicMock(state=STATE_OFF, attributes={}),
            }
        )
    )
    assert light_group._is_controlled_by_this_entity() is True
    light_group._cancel_retry_timer()


async def test_switch_default_dim_level_prestaging_all_rooms(
    hass: HomeAssistant,
) -> None:
    """Test pre-staging default turn-on dim levels on smart switches across rooms when entering/leaving sleep mode."""
    from unittest.mock import MagicMock

    area = MagicMock()
    area.hass = hass
    area.name = "Master Bathroom"
    area.slug = "master_bathroom"
    area.id = "master_bathroom"
    area.state = AreaState.AREA_STATE_CLEAR
    area.lighting_profile = LightingProfile.STANDARD
    area.is_meta.return_value = False
    area.is_exterior.return_value = False
    area.is_control_enabled.return_value = True
    area.has_configured_presence_sensors.return_value = True
    area.has_configured_state.side_effect = (
        lambda st: st == AreaState.AREA_STATE_OCCUPIED
    )
    area.state_config.side_effect = lambda st: (
        StateConfigData(
            name="occupied",
            entity=None,
            entity_state_on=STATE_ON,
            dim_level=100,
            for_state=AreaState.AREA_STATE_OCCUPIED,
            icon="mdi:lightbulb",
            control_entity="switch.simply_magic_areas_light_control_master_bathroom",
            lights=["light.master_bathroom_dimmer"],
        )
        if st == AreaState.AREA_STATE_OCCUPIED
        else None
    )
    area.config = {}
    area.feature_config.return_value = {"sleep_state_dim": 30}
    area.get_effective_illuminance.return_value = 0.0
    area.entities = {
        LIGHT_DOMAIN: [{ATTR_ENTITY_ID: "light.master_bathroom_dimmer"}],
    }
    area.simply_magic_entity_id.side_effect = (
        lambda domain, name, area_name=None: (
            f"{domain}.simply_magic_areas_{name}_{area_name or 'master_bathroom'}"
        )
    )

    # Register Z-Wave (0..99), standard (0..100), unavailable, and excluded (ramp rate / minimum) number entities
    zwave_default_id = "number.master_bathroom_dimmer_default_level_local"
    zigbee_default_id = "number.master_bathroom_dimmer_on_level"
    broken_default_id = "number.master_bathroom_dimmer_default_brightness"
    ramp_rate_id = "number.master_bathroom_dimmer_ramp_rate"
    min_level_id = "number.master_bathroom_dimmer_minimum_level"

    hass.states.async_set(
        zwave_default_id,
        "99",
        {"min": 0, "max": 99, "friendly_name": "Default Level (Local)"},
    )
    hass.states.async_set(
        zigbee_default_id,
        "100",
        {"min": 1, "max": 100, "friendly_name": "On Level"},
    )
    hass.states.async_set(
        broken_default_id,
        STATE_UNAVAILABLE,
        {"min": 0, "max": 100, "friendly_name": "Default Brightness"},
    )
    hass.states.async_set(
        ramp_rate_id,
        "3",
        {"min": 0, "max": 99, "friendly_name": "Dimming Ramp Rate"},
    )
    hass.states.async_set(
        min_level_id,
        "1",
        {"min": 1, "max": 45, "friendly_name": "Minimum Brightness Level"},
    )

    set_value_calls = async_mock_service(hass, "number", "set_value")

    light_group = AreaLightGroup(area, ["light.master_bathroom_dimmer"])
    light_group.hass = hass
    light_group._attr_name = "Master Bathroom Lights"
    light_group.entity_id = "light.simply_magic_areas_light_master_bathroom"
    light_group.schedule_update_ha_state = MagicMock()
    light_group.async_update_group_state = MagicMock()

    discovered = light_group._get_switch_default_dim_entities()
    assert zwave_default_id in discovered
    assert zigbee_default_id in discovered
    assert broken_default_id in discovered
    assert ramp_rate_id not in discovered
    assert min_level_id not in discovered

    # Transition to SLEEP profile -> sets working switches to 30% (30 on 0..99 and 30 on 1..100), skips broken
    area.lighting_profile = LightingProfile.SLEEP
    light_group._profile_change(
        MagicMock(
            data={
                "old_state": MagicMock(state=LightingProfile.STANDARD),
                "new_state": MagicMock(state=LightingProfile.SLEEP),
            }
        )
    )
    await hass.async_block_till_done()

    called_Map = {c.data[ATTR_ENTITY_ID]: c.data["value"] for c in set_value_calls}
    assert called_Map == {
        zwave_default_id: 30,
        zigbee_default_id: 30,
    }

    # Update states to reflect new values, then transition back to STANDARD -> restores 99 (Z-Wave) and 100
    hass.states.async_set(zwave_default_id, "30", {"min": 0, "max": 99})
    hass.states.async_set(zigbee_default_id, "30", {"min": 1, "max": 100})
    set_value_calls.clear()

    area.lighting_profile = LightingProfile.STANDARD
    light_group._profile_change(
        MagicMock(
            data={
                "old_state": MagicMock(state=LightingProfile.SLEEP),
                "new_state": MagicMock(state=LightingProfile.STANDARD),
            }
        )
    )
    await hass.async_block_till_done()

    called_restore_map = {
        c.data[ATTR_ENTITY_ID]: c.data["value"] for c in set_value_calls
    }
    assert called_restore_map == {
        zwave_default_id: 99,
        zigbee_default_id: 100,
    }
    light_group._cancel_retry_timer()


async def test_sensor_room_manual_switch_on_off_and_dim_level_control(
    hass: HomeAssistant,
) -> None:
    """Test that in a sensor-equipped room, manual switch ON/OFF sets manual override (held until room clears) while still adjusting dim level for sleep/bright modes without changing ON/OFF state."""
    from unittest.mock import MagicMock

    area = MagicMock()
    area.hass = hass
    area.name = "Bedroom"
    area.slug = "bedroom"
    area.id = "bedroom"
    area.state = AreaState.AREA_STATE_OCCUPIED
    area.lighting_profile = LightingProfile.STANDARD
    area.is_meta.return_value = False
    area.is_exterior.return_value = False
    area.is_control_enabled.return_value = True
    area.has_configured_presence_sensors.return_value = True
    area.has_configured_state.side_effect = (
        lambda st: st == AreaState.AREA_STATE_OCCUPIED
    )
    area.state_config.side_effect = lambda st: (
        StateConfigData(
            name="occupied",
            entity=None,
            entity_state_on=STATE_ON,
            dim_level=100,
            for_state=AreaState.AREA_STATE_OCCUPIED,
            icon="mdi:lightbulb",
            control_entity="switch.simply_magic_areas_light_control_bedroom",
            lights=["light.bedroom_light"],
        )
        if st == AreaState.AREA_STATE_OCCUPIED
        else None
    )
    area.config = {}
    area.feature_config.return_value = {
        "sleep_state_dim": 20,
        "bright_state_dim": 0,
    }
    area.get_effective_illuminance.return_value = 0.0
    area.entities = {
        LIGHT_DOMAIN: [{ATTR_ENTITY_ID: "light.bedroom_light"}],
    }
    area.simply_magic_entity_id.side_effect = (
        lambda domain, name, area_name=None: (
            f"{domain}.simply_magic_areas_{name}_{area_name or 'bedroom'}"
        )
    )

    state_sensor_id = "sensor.simply_magic_areas_state_bedroom"
    hass.states.async_set(
        state_sensor_id,
        AreaState.AREA_STATE_OCCUPIED,
        {"presence_sensors": ["binary_sensor.bedroom_motion"]},
    )
    hass.states.async_set("light.bedroom_light", STATE_OFF)

    light_group = AreaLightGroup(area, ["light.bedroom_light"])
    light_group.hass = hass
    light_group._attr_name = "Bedroom Lights"
    light_group.entity_id = "light.simply_magic_areas_light_bedroom"
    light_group.schedule_update_ha_state = MagicMock()
    light_group.async_update_group_state = MagicMock()

    assert light_group._has_presence_sensors() is True

    turn_on_calls = async_mock_service(hass, LIGHT_DOMAIN, SERVICE_TURN_ON)
    turn_off_calls = async_mock_service(hass, LIGHT_DOMAIN, "turn_off")

    # 1. User manually flips switch ON while room is in SLEEP mode -> sets manual_control=True AND adjusts dim level to 20%
    area.lighting_profile = LightingProfile.SLEEP
    hass.states.async_set(
        "light.bedroom_light", STATE_ON, {ATTR_BRIGHTNESS: 255}
    )
    light_group._update_group_state(
        MagicMock(
            data={
                "old_state": MagicMock(state=STATE_OFF, attributes={}),
                "new_state": MagicMock(
                    state=STATE_ON, attributes={ATTR_BRIGHTNESS: 255}
                ),
            }
        )
    )
    await hass.async_block_till_done()
    assert light_group._is_controlled_by_this_entity() is False
    assert light_group.extra_state_attributes.get(ATTR_MANUAL_CONTROL) is True
    assert len(turn_on_calls) == 1
    assert turn_on_calls[-1].data[ATTR_BRIGHTNESS] == int(20 * 255 / 100)
    light_group._cancel_retry_timer()

    # 2. While switch is manually ON, profile changes SLEEP -> BRIGHT (bright_state_dim=0):
    #    ON/OFF state is manual so it NEVER turns off; instead it adjusts dim level to 100% (255)
    turn_on_calls.clear()
    area.lighting_profile = LightingProfile.BRIGHT
    light_group._profile_change(
        MagicMock(
            data={
                "old_state": MagicMock(state=LightingProfile.SLEEP),
                "new_state": MagicMock(state=LightingProfile.BRIGHT),
            }
        )
    )
    await hass.async_block_till_done()
    assert len(turn_off_calls) == 0
    assert len(turn_on_calls) == 1
    assert turn_on_calls[-1].data[ATTR_BRIGHTNESS] == 255
    assert light_group.extra_state_attributes.get(ATTR_MANUAL_CONTROL) is True
    light_group._cancel_retry_timer()

    # 3. While switch is manually ON, profile changes BRIGHT -> SLEEP:
    #    Adjusts dim level back to 20% while keeping manual_control=True
    turn_on_calls.clear()
    area.lighting_profile = LightingProfile.SLEEP
    light_group._profile_change(
        MagicMock(
            data={
                "old_state": MagicMock(state=LightingProfile.BRIGHT),
                "new_state": MagicMock(state=LightingProfile.SLEEP),
            }
        )
    )
    await hass.async_block_till_done()
    assert len(turn_on_calls) == 1
    assert turn_on_calls[-1].data[ATTR_BRIGHTNESS] == int(20 * 255 / 100)
    assert light_group.extra_state_attributes.get(ATTR_MANUAL_CONTROL) is True
    light_group._cancel_retry_timer()

    # 4. User manually turns switch OFF while room is occupied -> manual_control=True holds it OFF across profile changes
    turn_on_calls.clear()
    light_group.last_update_from_entity = False
    light_group._command_in_flight_until = 0.0
    hass.states.async_set("light.bedroom_light", STATE_OFF)
    light_group._update_group_state(
        MagicMock(
            data={
                "old_state": MagicMock(state=STATE_ON, attributes={}),
                "new_state": MagicMock(state=STATE_OFF, attributes={}),
            }
        )
    )
    assert light_group.extra_state_attributes.get(ATTR_MANUAL_CONTROL) is True

    area.lighting_profile = LightingProfile.STANDARD
    light_group._profile_change(
        MagicMock(
            data={
                "old_state": MagicMock(state=LightingProfile.SLEEP),
                "new_state": MagicMock(state=LightingProfile.STANDARD),
            }
        )
    )
    await hass.async_block_till_done()
    assert len(turn_on_calls) == 0
    light_group._cancel_retry_timer()


async def test_front_room_arrival_prewarm_and_handoff(hass: HomeAssistant) -> None:
    """Test that front room / entry lights pre-warm when arriving home at night and hand off cleanly on occupancy."""
    from unittest.mock import MagicMock

    hass.states.async_set("sun.sun", "below_horizon", {"elevation": -10.0})
    hass.states.async_set("sensor.simply_magic_areas_state_interior", AreaState.AREA_STATE_CLEAR)

    area = MagicMock()
    area.hass = hass
    area.name = "Front Room"
    area.slug = "front_room"
    area.id = "front_room"
    area.state = AreaState.AREA_STATE_CLEAR
    area.lighting_profile = LightingProfile.STANDARD
    area.is_meta.return_value = False
    area.is_exterior.return_value = False
    area.is_control_enabled.return_value = True
    area.has_configured_presence_sensors.return_value = True
    area.has_configured_state.side_effect = (
        lambda st: st == AreaState.AREA_STATE_OCCUPIED
    )
    area.state_config.side_effect = lambda st: (
        StateConfigData(
            name="occupied",
            entity=None,
            entity_state_on=STATE_ON,
            dim_level=100,
            for_state=AreaState.AREA_STATE_OCCUPIED,
            icon="mdi:lightbulb",
            control_entity="switch.simply_magic_areas_light_control_front_room",
            lights=["light.front_room_light"],
        )
        if st == AreaState.AREA_STATE_OCCUPIED
        else None
    )
    area.config = {}
    area.feature_config.return_value = {}
    area.get_effective_illuminance.return_value = 0.0
    area.entities = {
        LIGHT_DOMAIN: [{ATTR_ENTITY_ID: "light.front_room_light"}],
    }
    area.simply_magic_entity_id.side_effect = (
        lambda domain, name, area_name=None: (
            f"{domain}.simply_magic_areas_{name}_{area_name or 'front_room'}"
        )
    )

    hass.states.async_set("light.front_room_light", STATE_OFF)
    light_group = AreaLightGroup(area, ["light.front_room_light"])
    light_group.hass = hass
    light_group._attr_name = "Front Room Lights"
    light_group.entity_id = "light.simply_magic_areas_light_front_room"
    light_group.schedule_update_ha_state = MagicMock()
    light_group.async_update_group_state = MagicMock()

    assert light_group._is_entry_area() is True

    turn_on_calls = async_mock_service(hass, LIGHT_DOMAIN, SERVICE_TURN_ON)
    turn_off_calls = async_mock_service(hass, LIGHT_DOMAIN, "turn_off")

    # 1. Proximity sensor reports resident approaching home (800m away, towards) -> pre-warms front room light!
    light_group._proximity_state_change(
        MagicMock(
            data={
                "entity_id": "proximity.home",
                "old_state": MagicMock(
                    state="2000", attributes={"dir_of_travel": "towards"}
                ),
                "new_state": MagicMock(
                    state="800", attributes={"dir_of_travel": "towards"}
                ),
            }
        )
    )
    await hass.async_block_till_done()
    assert len(turn_on_calls) == 1
    assert turn_on_calls[-1].data[ATTR_BRIGHTNESS] == 255
    assert (
        light_group.extra_state_attributes.get(ATTR_ARRIVAL_PREWARM_ACTIVE)
        is True
    )
    assert (
        light_group.extra_state_attributes.get(ATTR_ARRIVAL_PREWARM_REASON)
        == "proximity_approaching"
    )
    light_group._cancel_retry_timer()

    # 2. While arrival pre-warm is active, clear state does NOT turn the front room light off
    hass.states.async_set(
        "light.front_room_light", STATE_ON, {ATTR_BRIGHTNESS: 255}
    )
    light_group._attr_is_on = True
    light_group._turn_off_light()
    await hass.async_block_till_done()
    assert len(turn_off_calls) == 0

    # 3. Resident walks into the front room (CLEAR -> OCCUPIED) -> pre-warm hands off to normal occupancy
    area.state = AreaState.AREA_STATE_OCCUPIED
    light_group._area_state_change(
        MagicMock(
            event_type="state_changed",
            data={
                "old_state": MagicMock(state=AreaState.AREA_STATE_CLEAR),
                "new_state": MagicMock(state=AreaState.AREA_STATE_OCCUPIED),
            },
        )
    )
    await hass.async_block_till_done()
    assert (
        light_group.extra_state_attributes.get(ATTR_ARRIVAL_PREWARM_ACTIVE)
        is None
    )
    light_group._cancel_retry_timer()


async def test_extended_mode_warning_dim_before_clear(
    hass: HomeAssistant,
) -> None:
    """Test that entering extended mode gently dims lights to 50% as a warning instead of plunging into darkness."""
    from unittest.mock import MagicMock

    area = MagicMock()
    area.hass = hass
    area.name = "Office"
    area.slug = "office"
    area.id = "office"
    area.state = AreaState.AREA_STATE_OCCUPIED
    area.lighting_profile = LightingProfile.STANDARD
    area.is_meta.return_value = False
    area.is_exterior.return_value = False
    area.is_control_enabled.return_value = True
    area.has_configured_presence_sensors.return_value = True
    area.has_configured_state.side_effect = lambda st: st in (
        AreaState.AREA_STATE_OCCUPIED,
        AreaState.AREA_STATE_EXTENDED,
    )
    area.state_config.side_effect = lambda st: (
        StateConfigData(
            name="occupied",
            entity=None,
            entity_state_on=STATE_ON,
            dim_level=100,
            for_state=AreaState.AREA_STATE_OCCUPIED,
            icon="mdi:lightbulb",
            control_entity="switch.simply_magic_areas_light_control_office",
            lights=["light.office_light"],
        )
        if st == AreaState.AREA_STATE_OCCUPIED
        else StateConfigData(
            name="extended",
            entity=None,
            entity_state_on=STATE_ON,
            dim_level=0,
            for_state=AreaState.AREA_STATE_EXTENDED,
            icon="mdi:lightbulb",
            control_entity="switch.simply_magic_areas_light_control_office",
            lights=["light.office_light"],
        )
    )
    area.config = {}
    area.feature_config.return_value = {}
    area.get_effective_illuminance.return_value = 0.0
    area.entities = {
        LIGHT_DOMAIN: [{ATTR_ENTITY_ID: "light.office_light"}],
    }
    area.simply_magic_entity_id.side_effect = (
        lambda domain, name, area_name=None: (
            f"{domain}.simply_magic_areas_{name}_{area_name or 'office'}"
        )
    )

    hass.states.async_set(
        "sensor.simply_magic_areas_state_office",
        AreaState.AREA_STATE_OCCUPIED,
        {"presence_sensors": ["binary_sensor.office_motion"]},
    )
    hass.states.async_set("light.office_light", STATE_ON, {ATTR_BRIGHTNESS: 255})

    light_group = AreaLightGroup(area, ["light.office_light"])
    light_group.hass = hass
    light_group._attr_name = "Office Lights"
    light_group.entity_id = "light.simply_magic_areas_light_office"
    light_group._attr_is_on = True
    light_group.schedule_update_ha_state = MagicMock()
    light_group.async_update_group_state = MagicMock()

    turn_on_calls = async_mock_service(hass, LIGHT_DOMAIN, SERVICE_TURN_ON)
    turn_off_calls = async_mock_service(hass, LIGHT_DOMAIN, "turn_off")

    # 1. Transition OCCUPIED -> EXTENDED: light dims to 50% (127) instead of turning off!
    area.state = AreaState.AREA_STATE_EXTENDED
    light_group._area_state_change(
        MagicMock(
            event_type="state_changed",
            data={
                "old_state": MagicMock(state=AreaState.AREA_STATE_OCCUPIED),
                "new_state": MagicMock(state=AreaState.AREA_STATE_EXTENDED),
            },
        )
    )
    await hass.async_block_till_done()
    assert len(turn_off_calls) == 0
    assert len(turn_on_calls) == 1
    assert turn_on_calls[-1].data[ATTR_BRIGHTNESS] == int(50 * 255 / 100)
    light_group._cancel_retry_timer()

    # 2. Transition EXTENDED -> CLEAR: light now turns completely OFF
    area.state = AreaState.AREA_STATE_CLEAR
    light_group._area_state_change(
        MagicMock(
            event_type="state_changed",
            data={
                "old_state": MagicMock(state=AreaState.AREA_STATE_EXTENDED),
                "new_state": MagicMock(state=AreaState.AREA_STATE_CLEAR),
            },
        )
    )
    await hass.async_block_till_done()
    assert len(turn_off_calls) == 1
    light_group._cancel_retry_timer()


async def test_all_away_departure_sweep_and_living_area_sleep_wakeup(
    hass: HomeAssistant,
) -> None:
    """Test all-away departure sweep across sensorless/manual rooms and sustained sleep wake-up in living areas."""
    from unittest.mock import MagicMock

    hass.states.async_set("person.alice", "not_home")
    hass.states.async_set("person.bob", "not_home")

    area = MagicMock()
    area.hass = hass
    area.name = "Kitchen"
    area.slug = "kitchen"
    area.id = "kitchen"
    area.state = AreaState.AREA_STATE_OCCUPIED
    area.lighting_profile = LightingProfile.SLEEP
    area.is_meta.return_value = False
    area.is_exterior.return_value = False
    area.is_control_enabled.return_value = True
    area.has_configured_presence_sensors.return_value = False
    area.has_configured_state.side_effect = (
        lambda st: st == AreaState.AREA_STATE_OCCUPIED
    )
    area.state_config.side_effect = lambda st: (
        StateConfigData(
            name="occupied",
            entity=None,
            entity_state_on=STATE_ON,
            dim_level=100,
            for_state=AreaState.AREA_STATE_OCCUPIED,
            icon="mdi:lightbulb",
            control_entity="switch.simply_magic_areas_light_control_kitchen",
            lights=["light.kitchen_light"],
        )
        if st == AreaState.AREA_STATE_OCCUPIED
        else None
    )
    area.config = {}
    area.feature_config.return_value = {"sleep_state_dim": 30}
    area.get_effective_illuminance.return_value = 0.0
    area.entities = {
        LIGHT_DOMAIN: [{ATTR_ENTITY_ID: "light.kitchen_light"}],
    }
    area.simply_magic_entity_id.side_effect = (
        lambda domain, name, area_name=None: (
            f"{domain}.simply_magic_areas_{name}_{area_name or 'kitchen'}"
        )
    )

    hass.states.async_set("light.kitchen_light", STATE_ON, {ATTR_BRIGHTNESS: 76})

    light_group = AreaLightGroup(area, ["light.kitchen_light"])
    light_group.hass = hass
    light_group._attr_name = "Kitchen Lights"
    light_group.entity_id = "light.simply_magic_areas_light_kitchen"
    light_group._attr_is_on = True
    light_group.schedule_update_ha_state = MagicMock()
    light_group.async_update_group_state = MagicMock()

    turn_on_calls = async_mock_service(hass, LIGHT_DOMAIN, SERVICE_TURN_ON)
    turn_off_calls = async_mock_service(hass, LIGHT_DOMAIN, "turn_off")

    # 1. Sustained occupancy in Kitchen during SLEEP mode promotes brightness to 100% (255)
    assert light_group._is_living_area() is True
    light_group._sleep_wakeup_timer_expired()
    await hass.async_block_till_done()
    assert len(turn_on_calls) == 1
    assert turn_on_calls[-1].data[ATTR_BRIGHTNESS] == 255
    assert (
        light_group.extra_state_attributes.get(ATTR_SLEEP_WAKEUP_BOOST) is True
    )
    light_group._cancel_retry_timer()

    # 2. All-away departure sweep: when last person transitions home -> not_home,
    #    even a sensorless room with manual_control=True is swept OFF!
    area.state = AreaState.AREA_STATE_CLEAR
    light_group._set_controlled_by_this_entity(False)
    assert light_group.extra_state_attributes.get(ATTR_MANUAL_CONTROL) is True

    light_group._person_state_change(
        MagicMock(
            data={
                "entity_id": "person.bob",
                "old_state": MagicMock(state="home"),
                "new_state": MagicMock(state="not_home"),
            }
        )
    )
    await hass.async_block_till_done()
    assert light_group.extra_state_attributes.get(ATTR_MANUAL_CONTROL) is None
    assert len(turn_off_calls) == 1
    light_group._cancel_retry_timer()


async def test_climate_and_media_player_ignore_attribute_updates_and_restore_hvac_mode(
    hass: HomeAssistant,
) -> None:
    """Test that AreaClimateGroup restores last active HVACMode and both climate and media player ignore attribute-only updates."""
    from unittest.mock import MagicMock

    from homeassistant.components.climate import (
        ATTR_HVAC_MODE,
        SERVICE_SET_HVAC_MODE,
        HVACAction,
        HVACMode,
    )
    from homeassistant.components.climate import (
        DOMAIN as CLIMATE_DOMAIN,
    )
    from homeassistant.components.media_player import (
        DOMAIN as MEDIA_PLAYER_DOMAIN,
    )

    area = MagicMock()
    area.hass = hass
    area.name = "Living Room"
    area.slug = "living_room"
    area.id = "living_room"
    area.is_meta.return_value = False
    area.is_control_enabled.return_value = True
    area.feature_config.return_value = {
        "turn_on_state": AreaState.AREA_STATE_OCCUPIED
    }
    area.simply_magic_entity_id.side_effect = (
        lambda domain, name, area_name=None: (
            f"{domain}.simply_magic_areas_{name}_{area_name or 'living_room'}"
        )
    )

    climate_group = AreaClimateGroup(area, ["climate.living_room_thermostat"])
    climate_group.hass = hass
    climate_group._attr_name = "Living Room Climate"
    climate_group.entity_id = "climate.simply_magic_areas_climate_living_room"
    climate_group._attr_hvac_modes = [
        HVACMode.OFF,
        HVACMode.HEAT_COOL,
        HVACMode.HEAT,
        HVACMode.COOL,
    ]
    climate_group._attr_hvac_mode = HVACMode.COOL
    climate_group._attr_hvac_action = HVACAction.COOLING

    hvac_calls = async_mock_service(hass, CLIMATE_DOMAIN, SERVICE_SET_HVAC_MODE)

    # 1. Attribute-only update on AreaStateSensor (same state "clear" with ATTR_STATE in attributes) is ignored
    climate_group._area_state_changed(
        MagicMock(
            data={
                "old_state": MagicMock(
                    state=AreaState.AREA_STATE_CLEAR,
                    attributes={ATTR_STATE: AreaState.AREA_STATE_CLEAR},
                ),
                "new_state": MagicMock(
                    state=AreaState.AREA_STATE_CLEAR,
                    attributes={ATTR_STATE: AreaState.AREA_STATE_CLEAR, "lux": 50},
                ),
            }
        )
    )
    await hass.async_block_till_done()
    assert len(hvac_calls) == 0

    # 2. Transition OCCUPIED -> CLEAR saves HVACMode.COOL and turns off
    climate_group._area_state_changed(
        MagicMock(
            data={
                "old_state": MagicMock(
                    state=AreaState.AREA_STATE_OCCUPIED,
                    attributes={ATTR_STATE: AreaState.AREA_STATE_OCCUPIED},
                ),
                "new_state": MagicMock(
                    state=AreaState.AREA_STATE_CLEAR,
                    attributes={ATTR_STATE: AreaState.AREA_STATE_CLEAR},
                ),
            }
        )
    )
    await hass.async_block_till_done()
    assert len(hvac_calls) == 1
    assert hvac_calls[-1].data[ATTR_HVAC_MODE] == HVACMode.OFF
    assert climate_group._last_active_hvac_mode == HVACMode.COOL

    # 3. Transition CLEAR -> OCCUPIED restores HVACMode.COOL (not HEAT_COOL!)
    hvac_calls.clear()
    climate_group._attr_hvac_mode = HVACMode.OFF
    climate_group._attr_hvac_action = HVACAction.OFF
    climate_group._area_state_changed(
        MagicMock(
            data={
                "old_state": MagicMock(
                    state=AreaState.AREA_STATE_CLEAR,
                    attributes={ATTR_STATE: AreaState.AREA_STATE_CLEAR},
                ),
                "new_state": MagicMock(
                    state=AreaState.AREA_STATE_OCCUPIED,
                    attributes={ATTR_STATE: AreaState.AREA_STATE_OCCUPIED},
                ),
            }
        )
    )
    await hass.async_block_till_done()
    assert len(hvac_calls) == 1
    assert hvac_calls[-1].data[ATTR_HVAC_MODE] == HVACMode.COOL

    # 4. Media player group ignores attribute-only updates on AreaStateSensor
    mp_group = AreaMediaPlayerGroup(area, ["media_player.living_room_tv"])
    mp_group.hass = hass
    mp_group.entity_id = "media_player.simply_magic_areas_media_player_living_room"
    mp_off_calls = async_mock_service(hass, MEDIA_PLAYER_DOMAIN, "turn_off")

    mp_group._area_state_changed(
        MagicMock(
            data={
                "old_state": MagicMock(
                    state=AreaState.AREA_STATE_CLEAR,
                    attributes={ATTR_STATE: AreaState.AREA_STATE_CLEAR},
                ),
                "new_state": MagicMock(
                    state=AreaState.AREA_STATE_CLEAR,
                    attributes={ATTR_STATE: AreaState.AREA_STATE_CLEAR, "lux": 20},
                ),
            }
        )
    )
    await hass.async_block_till_done()
    assert len(mp_off_calls) == 0


async def test_hvac_overhead_fan_slow_circulation_and_exhaust_exclusion(
    hass: HomeAssistant,
) -> None:
    """Verify overhead fans run at slow speed (25% default) when HVAC runs, while exhaust fans do not."""
    from unittest.mock import MagicMock

    from homeassistant.components.fan import ATTR_PERCENTAGE
    from homeassistant.const import SERVICE_TURN_OFF

    from custom_components.simply_magic_areas.const import (
        ATTR_HVAC_CIRCULATION_ON,
        ATTR_HVAC_CIRCULATION_SPEED,
        ATTR_LINKED_CLIMATE_ENTITIES,
        CONF_HVAC_OVERHEAD_FAN_SPEED,
        CONF_HVAC_RUN_OVERHEAD_FANS,
        CONF_LINKED_CLIMATE_ENTITY,
    )

    fan_on_calls = async_mock_service(hass, FAN_DOMAIN, SERVICE_TURN_ON)
    fan_off_calls = async_mock_service(hass, FAN_DOMAIN, SERVICE_TURN_OFF)

    # 1. Living Room with ceiling fan linked to climate.hallway_thermostat (defaults to ON at 25%)
    hass.states.async_set(
        "fan.living_room_ceiling_fan",
        STATE_OFF,
        {"friendly_name": "Living Room Ceiling Fan"},
    )
    hass.states.async_set(
        "climate.hallway_thermostat",
        "cool",
        {"hvac_action": "idle", "current_temperature": 72, "temperature": 72},
    )

    living_area = MagicMock()
    living_area.hass = hass
    living_area.name = "Living Room"
    living_area.slug = "living_room"
    living_area.id = "living_room"
    living_area.state = AreaState.AREA_STATE_OCCUPIED
    living_area.is_meta.return_value = False
    living_area.is_exterior.return_value = False
    living_area.is_interior.return_value = True
    living_area.is_control_enabled.return_value = True
    living_area.has_feature.return_value = False
    living_area.config = {}
    living_area.feature_config.return_value = {
        CONF_LINKED_CLIMATE_ENTITY: "climate.hallway_thermostat",
    }
    living_area.entities = {
        FAN_DOMAIN: [{"entity_id": "fan.living_room_ceiling_fan"}],
    }
    living_area.simply_magic_entity_id.side_effect = (
        lambda domain, name, area_name=None: f"{domain}.simply_magic_areas_{name}_{area_name or 'living_room'}"
    )

    living_fan_group = AreaFanGroup(living_area, ["fan.living_room_ceiling_fan"])
    living_fan_group.hass = hass
    living_fan_group._attr_name = "Living Room Fans"
    living_fan_group.entity_id = "fan.simply_magic_areas_fan_living_room"
    living_fan_group.schedule_update_ha_state = MagicMock()

    # Initially HVAC is idle -> overhead fan stays off
    assert living_fan_group._evaluate_hvac_fan_circulation() is False
    assert len(fan_on_calls) == 0

    # HVAC starts cooling -> overhead fan turns on at slow 25% speed
    hass.states.async_set(
        "climate.hallway_thermostat",
        "cool",
        {"hvac_action": "cooling", "current_temperature": 75, "temperature": 72},
    )
    assert living_fan_group._evaluate_hvac_fan_circulation() is True
    await hass.async_block_till_done()
    assert len(fan_on_calls) == 1
    assert fan_on_calls[-1].data[ATTR_ENTITY_ID] == "fan.simply_magic_areas_fan_living_room"
    assert fan_on_calls[-1].data[ATTR_PERCENTAGE] == 25
    assert living_fan_group._attr_extra_state_attributes[ATTR_HVAC_CIRCULATION_ON] is True
    assert living_fan_group._attr_extra_state_attributes[ATTR_HVAC_CIRCULATION_SPEED] == 25
    assert living_fan_group._attr_extra_state_attributes[ATTR_LINKED_CLIMATE_ENTITIES] == [
        "climate.hallway_thermostat"
    ]

    # When HVAC returns to idle -> overhead fan turns off and cleans up attributes
    hass.states.async_set("fan.living_room_ceiling_fan", STATE_ON)
    hass.states.async_set(
        "climate.hallway_thermostat",
        "cool",
        {"hvac_action": "idle", "current_temperature": 72, "temperature": 72},
    )
    assert living_fan_group._evaluate_hvac_fan_circulation() is False
    await hass.async_block_till_done()
    assert len(fan_off_calls) == 1
    assert ATTR_HVAC_CIRCULATION_ON not in living_fan_group._attr_extra_state_attributes

    # 2. Bathroom exhaust fan is excluded from HVAC circulation
    fan_on_calls.clear()
    hass.states.async_set(
        "fan.bathroom_exhaust_fan",
        STATE_OFF,
        {"friendly_name": "Bathroom Exhaust Fan"},
    )
    hass.states.async_set(
        "climate.hallway_thermostat",
        "heat",
        {"hvac_action": "heating", "current_temperature": 66, "temperature": 70},
    )
    bath_area = MagicMock()
    bath_area.hass = hass
    bath_area.name = "Bathroom"
    bath_area.slug = "bathroom"
    bath_area.id = "bathroom"
    bath_area.state = AreaState.AREA_STATE_CLEAR
    bath_area.is_meta.return_value = False
    bath_area.is_exterior.return_value = False
    bath_area.is_interior.return_value = True
    bath_area.is_control_enabled.return_value = True
    bath_area.has_feature.return_value = True
    bath_area.config = {}
    bath_area.feature_config.return_value = {}
    bath_area.entities = {
        FAN_DOMAIN: [{"entity_id": "fan.bathroom_exhaust_fan"}],
    }
    bath_area.simply_magic_entity_id.side_effect = (
        lambda domain, name, area_name=None: f"{domain}.simply_magic_areas_{name}_{area_name or 'bathroom'}"
    )
    bath_fan_group = AreaFanGroup(bath_area, ["fan.bathroom_exhaust_fan"])
    bath_fan_group.hass = hass
    bath_fan_group._attr_name = "Bathroom Fans"
    bath_fan_group.entity_id = "fan.simply_magic_areas_fan_bathroom"
    bath_fan_group.schedule_update_ha_state = MagicMock()

    assert bath_fan_group._evaluate_hvac_fan_circulation() is False
    await hass.async_block_till_done()
    assert len(fan_on_calls) == 0

    # 3. Custom speed (35%) and disabling via CONF_HVAC_RUN_OVERHEAD_FANS = False
    hass.states.async_set("fan.living_room_ceiling_fan", STATE_OFF)
    living_area.config = {CONF_HVAC_OVERHEAD_FAN_SPEED: 35}
    living_fan_group._cancel_retry_timer()
    assert living_fan_group._evaluate_hvac_fan_circulation() is True
    await hass.async_block_till_done()
    assert fan_on_calls[-1].data[ATTR_PERCENTAGE] == 35

    hass.states.async_set("fan.living_room_ceiling_fan", STATE_ON)
    living_area.config = {CONF_HVAC_RUN_OVERHEAD_FANS: False}
    fan_off_calls.clear()
    assert living_fan_group._evaluate_hvac_fan_circulation() is False
    await hass.async_block_till_done()
    assert len(fan_off_calls) == 1
    living_fan_group._cancel_retry_timer()


async def test_manual_light_changes_synchronize_all_room_lights_and_dim_level(
    hass: HomeAssistant,
) -> None:
    """Verify manual ON, dim, and OFF changes on one light synchronize all lights in the room."""
    from unittest.mock import MagicMock


    on_calls = async_mock_service(hass, LIGHT_DOMAIN, SERVICE_TURN_ON)
    off_calls = async_mock_service(hass, LIGHT_DOMAIN, "turn_off")

    hass.states.async_set("light.kitchen_main", STATE_OFF)
    hass.states.async_set("light.kitchen_island", STATE_OFF)

    area = MagicMock()
    area.hass = hass
    area.name = "Kitchen"
    area.slug = "kitchen"
    area.id = "kitchen"
    area.state = AreaState.AREA_STATE_OCCUPIED
    area.lighting_profile = LightingProfile.STANDARD
    area.is_meta.return_value = False
    area.is_exterior.return_value = False
    area.is_interior.return_value = True
    area.is_control_enabled.return_value = True
    area.config = {}
    area.feature_config.return_value = {}
    area.entities = {
        LIGHT_DOMAIN: [
            {"entity_id": "light.kitchen_main"},
            {"entity_id": "light.kitchen_island"},
        ],
        BINARY_SENSOR_DOMAIN: [
            {
                "entity_id": "binary_sensor.kitchen_motion",
                ATTR_DEVICE_CLASS: BinarySensorDeviceClass.MOTION,
            }
        ],
    }
    area.all_state_configs.return_value = {
        AreaState.AREA_STATE_OCCUPIED: StateConfigData(
            name="occupied",
            entity=None,
            entity_state_on=STATE_ON,
            dim_level=100.0,
            for_state=AreaState.AREA_STATE_OCCUPIED,
            icon="mdi:lightbulb",
            control_entity="switch.simply_magic_areas_light_control_kitchen",
            lights=["light.kitchen_main", "light.kitchen_island"],
        ),
        LightingProfile.STANDARD: StateConfigData(
            name="standard",
            entity=None,
            entity_state_on=STATE_ON,
            dim_level=100.0,
            for_state=LightingProfile.STANDARD,
            icon="mdi:lightbulb",
            control_entity="switch.simply_magic_areas_light_control_kitchen",
            lights=["light.kitchen_main", "light.kitchen_island"],
        ),
    }
    area.get_effective_illuminance.return_value = 0.0
    area.simply_magic_entity_id.side_effect = (
        lambda domain, name, area_name=None: f"{domain}.simply_magic_areas_{name}_{area_name or 'kitchen'}"
    )

    lg = AreaLightGroup(area, ["light.kitchen_main", "light.kitchen_island"])
    lg.hass = hass
    lg._attr_name = "Kitchen Lights"
    lg.entity_id = "light.simply_magic_areas_light_kitchen"
    lg.schedule_update_ha_state = MagicMock()
    lg.async_update_group_state = MagicMock()

    # 1. User manually turns ON light.kitchen_main at brightness 180 while light.kitchen_island is OFF
    hass.states.async_set("light.kitchen_main", STATE_ON, {ATTR_BRIGHTNESS: 180})
    lg._member_light_state_change(
        MagicMock(
            data={
                "entity_id": "light.kitchen_main",
                "old_state": MagicMock(state=STATE_OFF, attributes={}),
                "new_state": MagicMock(
                    state=STATE_ON, attributes={ATTR_BRIGHTNESS: 180}
                ),
            }
        )
    )
    await hass.async_block_till_done()
    assert len(on_calls) == 1
    assert on_calls[-1].data[ATTR_ENTITY_ID] == "light.simply_magic_areas_light_kitchen"
    assert on_calls[-1].data[ATTR_BRIGHTNESS] == 180
    assert lg._attr_extra_state_attributes.get(ATTR_MANUAL_CONTROL) is True

    # Simulate both lights now ON at 180
    hass.states.async_set("light.kitchen_island", STATE_ON, {ATTR_BRIGHTNESS: 180})
    lg._command_in_flight_until = 0.0
    lg.last_update_from_entity = False
    on_calls.clear()

    # 2. User manually dims light.kitchen_main from 180 to 90 -> light.kitchen_island syncs to 90
    hass.states.async_set("light.kitchen_main", STATE_ON, {ATTR_BRIGHTNESS: 90})
    lg._member_light_state_change(
        MagicMock(
            data={
                "entity_id": "light.kitchen_main",
                "old_state": MagicMock(
                    state=STATE_ON, attributes={ATTR_BRIGHTNESS: 180}
                ),
                "new_state": MagicMock(
                    state=STATE_ON, attributes={ATTR_BRIGHTNESS: 90}
                ),
            }
        )
    )
    await hass.async_block_till_done()
    assert len(on_calls) == 1
    assert on_calls[-1].data[ATTR_ENTITY_ID] == "light.simply_magic_areas_light_kitchen"
    assert on_calls[-1].data[ATTR_BRIGHTNESS] == 90
    assert lg._manual_brightness_override is True

    # 3. If light.kitchen_island missed the dim command (still at 180), _get_unresponsive_lights catches it
    assert lg._get_unresponsive_lights(STATE_ON) == ["light.kitchen_island"]
    on_calls.clear()
    lg._execute_actuator_retry()
    await hass.async_block_till_done()
    assert len(on_calls) == 1
    assert on_calls[-1].data[ATTR_BRIGHTNESS] == 90

    # Simulate both lights now synced at 90
    hass.states.async_set("light.kitchen_island", STATE_ON, {ATTR_BRIGHTNESS: 90})
    assert lg._get_unresponsive_lights(STATE_ON) == []
    lg._command_in_flight_until = 0.0
    lg.last_update_from_entity = False

    # 4. User manually turns OFF light.kitchen_main -> light.kitchen_island also turns OFF
    hass.states.async_set("light.kitchen_main", STATE_OFF)
    lg._member_light_state_change(
        MagicMock(
            data={
                "entity_id": "light.kitchen_main",
                "old_state": MagicMock(
                    state=STATE_ON, attributes={ATTR_BRIGHTNESS: 90}
                ),
                "new_state": MagicMock(state=STATE_OFF, attributes={}),
            }
        )
    )
    await hass.async_block_till_done()
    assert len(off_calls) == 1
    assert (
        off_calls[-1].data[ATTR_ENTITY_ID] == "light.simply_magic_areas_light_kitchen"
    )
    lg._cancel_retry_timer()

    # 5. Verify sync is strictly scoped to a single room (never across rooms, house groups, or meta-areas)
    hass.states.async_set(
        "light.all_house_lights",
        STATE_OFF,
        {ATTR_ENTITY_ID: ["light.kitchen_main", "light.bedroom_main"]},
    )
    hass.states.async_set("light.bedroom_main", STATE_OFF)
    lg._all_lights = [
        "light.kitchen_main",
        "light.kitchen_island",
        "light.all_house_lights",
        "light.simply_magic_areas_light_bedroom",
    ]
    assert lg._get_local_room_lights() == [
        "light.kitchen_main",
        "light.kitchen_island",
    ]

    # Meta-area never syncs across rooms
    area.is_meta.return_value = True
    assert lg._get_local_room_lights() == []


async def test_manual_light_turn_off_clears_quickly_on_unoccupied_and_turns_back_on_when_occupied(
    hass: HomeAssistant,
) -> None:
    """Test manual light turn-off shortens unoccupied clear timeout, clears manual_control on unoccupied, and turns back on when occupied."""
    from unittest.mock import MagicMock

    from ..base.area_state_sensor import AreaStateSensor
    from ..const import CONF_CLEAR_TIMEOUT, DEFAULT_MANUAL_OFF_CLEAR_TIMEOUT

    area = MagicMock()
    area.hass = hass
    area.name = "Kitchen"
    area.slug = "kitchen"
    area.id = "kitchen"
    area.state = AreaState.AREA_STATE_OCCUPIED
    area.lighting_profile = LightingProfile.STANDARD
    area.is_meta.return_value = False
    area.is_exterior.return_value = False
    area.is_interior.return_value = True
    area.is_control_enabled.return_value = True
    area.config = {CONF_CLEAR_TIMEOUT: 360}
    area.feature_config.return_value = {}
    area.entities = {
        LIGHT_DOMAIN: [{"entity_id": "light.kitchen_main"}],
        BINARY_SENSOR_DOMAIN: [],
    }
    area.all_state_configs.return_value = {}
    area.simply_magic_entity_id.side_effect = (
        lambda domain, name, area_name=None: f"{domain}.simply_magic_areas_{name}_{area_name or 'kitchen'}"
    )

    sensor = AreaStateSensor(area)
    sensor.hass = hass
    sensor.entity_id = "sensor.simply_magic_areas_state_kitchen"
    sensor.schedule_update_ha_state = MagicMock()

    # Normally clear_timeout is 360s
    assert sensor._get_effective_clear_timeout() == 360

    # When light is manually turned OFF, effective clear_timeout drops to DEFAULT_MANUAL_OFF_CLEAR_TIMEOUT (10s)
    hass.states.async_set(
        "light.simply_magic_areas_light_kitchen",
        STATE_OFF,
        {ATTR_MANUAL_CONTROL: True},
    )
    assert sensor._get_effective_clear_timeout() == DEFAULT_MANUAL_OFF_CLEAR_TIMEOUT

    # Also test AreaLightGroup manual-off clearing quickly when space is already unoccupied (EXTENDED/CLEAR)
    area.state = AreaState.AREA_STATE_EXTENDED
    hass.states.async_set("light.kitchen_main", STATE_OFF)
    lg = AreaLightGroup(area, ["light.kitchen_main"])
    lg.hass = hass
    lg._attr_name = "Kitchen Lights"
    lg.entity_id = "light.simply_magic_areas_light_kitchen"
    lg.schedule_update_ha_state = MagicMock()
    lg._set_controlled_by_this_entity(False)
    lg._attr_is_on = False

    lg._schedule_manual_off_unoccupied_clear_if_needed()
    assert lg._manual_off_unoccupied_timer_cb is not None
    lg._manual_off_unoccupied_timer_expired()
    assert lg._is_controlled_by_this_entity() is True
    assert lg.extra_state_attributes.get(ATTR_MANUAL_CONTROL) is None


async def test_house_occupancy_independent_of_bedrooms_and_ensuites(
    hass: HomeAssistant,
) -> None:
    """Test that house occupancy (Interior/Global) tracks main areas independently of bedrooms and ensuites."""
    from unittest.mock import MagicMock

    from ..base.area_state_sensor import AreaStateSensor
    from ..base.magic import MagicArea, MagicMetaArea
    from ..const import (
        AREA_TYPE_BEDROOM,
        AREA_TYPE_INTERIOR,
        AREA_TYPE_META,
        CONF_ID,
        CONF_NAME,
        CONF_TYPE,
        DATA_AREA_OBJECT,
        META_AREA_GLOBAL,
        META_AREA_INTERIOR,
        MODULE_DATA,
    )

    def _make_area_stub(
        name: str, slug: str, area_type: str, state: AreaState
    ) -> MagicArea:
        area = MagicArea.__new__(MagicArea)
        area.hass = hass
        area.name = name
        area.slug = slug
        area.id = slug
        area.state = state
        area.config = {CONF_NAME: name, CONF_ID: slug, CONF_TYPE: area_type}
        area.entities = {
            LIGHT_DOMAIN: [{ATTR_ENTITY_ID: f"light.{slug}"}],
        }
        area.initialized = True
        return area

    kitchen = _make_area_stub(
        "Kitchen", "kitchen", AREA_TYPE_INTERIOR, AreaState.AREA_STATE_CLEAR
    )
    living_room = _make_area_stub(
        "Living Room", "living_room", AREA_TYPE_INTERIOR, AreaState.AREA_STATE_CLEAR
    )
    master_bedroom = _make_area_stub(
        "Master Bedroom",
        "master_bedroom",
        AREA_TYPE_BEDROOM,
        AreaState.AREA_STATE_OCCUPIED,
    )
    ensuite = _make_area_stub(
        "Master Ensuite", "master_ensuite", AREA_TYPE_INTERIOR, AreaState.AREA_STATE_OCCUPIED
    )

    assert kitchen.is_main_house_area() is True
    assert living_room.is_main_house_area() is True
    assert master_bedroom.is_bedroom_or_ensuite() is True
    assert master_bedroom.is_main_house_area() is False
    assert ensuite.is_bedroom_or_ensuite() is True
    assert ensuite.is_main_house_area() is False

    hass.data[MODULE_DATA] = {
        "entry_kitchen": {DATA_AREA_OBJECT: kitchen},
        "entry_living": {DATA_AREA_OBJECT: living_room},
        "entry_bedroom": {DATA_AREA_OBJECT: master_bedroom},
        "entry_ensuite": {DATA_AREA_OBJECT: ensuite},
    }
    hass.states.async_set(
        "sensor.simply_magic_areas_state_kitchen", AreaState.AREA_STATE_CLEAR
    )
    hass.states.async_set(
        "sensor.simply_magic_areas_state_living_room", AreaState.AREA_STATE_CLEAR
    )
    hass.states.async_set(
        "sensor.simply_magic_areas_state_master_bedroom", AreaState.AREA_STATE_OCCUPIED
    )
    hass.states.async_set(
        "sensor.simply_magic_areas_state_master_ensuite", AreaState.AREA_STATE_OCCUPIED
    )
    hass.states.async_set("light.master_bedroom", STATE_ON)
    hass.states.async_set("light.master_ensuite", STATE_ON)

    interior_meta = MagicMetaArea.__new__(MagicMetaArea)
    interior_meta.hass = hass
    interior_meta.name = META_AREA_INTERIOR
    interior_meta.slug = "interior"
    interior_meta.id = "interior"
    interior_meta.state = AreaState.AREA_STATE_CLEAR
    interior_meta.config = {
        CONF_NAME: META_AREA_INTERIOR,
        CONF_ID: "interior",
        CONF_TYPE: AREA_TYPE_META,
    }
    interior_meta.entities = {}
    interior_meta.initialized = True

    global_meta = MagicMetaArea.__new__(MagicMetaArea)
    global_meta.hass = hass
    global_meta.name = META_AREA_GLOBAL
    global_meta.slug = "global"
    global_meta.id = "global"
    global_meta.state = AreaState.AREA_STATE_CLEAR
    global_meta.config = {
        CONF_NAME: META_AREA_GLOBAL,
        CONF_ID: "global",
        CONF_TYPE: AREA_TYPE_META,
    }
    global_meta.entities = {}
    global_meta.initialized = True

    # Interior and Global meta-areas only include main house areas (kitchen, living_room),
    # excluding master_bedroom and master_ensuite.
    assert set(interior_meta.get_child_areas()) == {"kitchen", "living_room"}
    assert set(global_meta.get_child_areas()) == {"kitchen", "living_room"}
    assert interior_meta.get_active_areas() == []
    assert global_meta.get_active_areas() == []

    # Even with movement/occupancy in master_bedroom and master_ensuite, house state sensor is CLEAR
    interior_sensor = AreaStateSensor(interior_meta)
    interior_sensor.hass = hass
    interior_sensor.entity_id = "sensor.simply_magic_areas_state_interior"
    interior_sensor.schedule_update_ha_state = MagicMock()
    interior_sensor._load_presence_sensors()
    assert interior_sensor.get_current_area_state() == AreaState.AREA_STATE_CLEAR

    # When movement occurs in the Kitchen (main area), the house becomes OCCUPIED
    kitchen.state = AreaState.AREA_STATE_OCCUPIED
    hass.states.async_set(
        "sensor.simply_magic_areas_state_kitchen", AreaState.AREA_STATE_OCCUPIED
    )
    assert interior_meta.get_active_areas() == ["kitchen"]
    assert interior_sensor.get_current_area_state() == AreaState.AREA_STATE_OCCUPIED


async def test_bedroom_meta_area_ties_multiple_rooms_and_unoccupied_when_lights_off(
    hass: HomeAssistant,
) -> None:
    """Test that a bedroom meta-area ties multiple rooms together and becomes unoccupied when all lights are off."""
    from unittest.mock import MagicMock

    from ..base.area_state_sensor import AreaStateSensor
    from ..base.magic import MagicArea, MagicMetaArea
    from ..const import (
        AREA_TYPE_BEDROOM,
        AREA_TYPE_INTERIOR,
        AREA_TYPE_META,
        CONF_CHILD_AREAS,
        CONF_ID,
        CONF_NAME,
        CONF_TYPE,
        DATA_AREA_OBJECT,
        META_AREA_BEDROOMS,
        MODULE_DATA,
    )

    master_bedroom = MagicArea.__new__(MagicArea)
    master_bedroom.hass = hass
    master_bedroom.name = "Master Bedroom"
    master_bedroom.slug = "master_bedroom"
    master_bedroom.id = "master_bedroom"
    master_bedroom.state = AreaState.AREA_STATE_OCCUPIED
    master_bedroom.config = {
        CONF_NAME: "Master Bedroom",
        CONF_ID: "master_bedroom",
        CONF_TYPE: AREA_TYPE_BEDROOM,
    }
    master_bedroom.entities = {
        LIGHT_DOMAIN: [{ATTR_ENTITY_ID: "light.master_bedroom_ceiling"}],
    }
    master_bedroom.initialized = True

    ensuite = MagicArea.__new__(MagicArea)
    ensuite.hass = hass
    ensuite.name = "Ensuite"
    ensuite.slug = "ensuite"
    ensuite.id = "ensuite"
    ensuite.state = AreaState.AREA_STATE_OCCUPIED
    ensuite.config = {
        CONF_NAME: "Ensuite",
        CONF_ID: "ensuite",
        CONF_TYPE: AREA_TYPE_INTERIOR,
    }
    ensuite.entities = {
        LIGHT_DOMAIN: [{ATTR_ENTITY_ID: "light.ensuite_vanity"}],
    }
    ensuite.initialized = True

    hass.data[MODULE_DATA] = {
        "entry_bedroom": {DATA_AREA_OBJECT: master_bedroom},
        "entry_ensuite": {DATA_AREA_OBJECT: ensuite},
    }

    bedrooms_meta = MagicMetaArea.__new__(MagicMetaArea)
    bedrooms_meta.hass = hass
    bedrooms_meta.name = META_AREA_BEDROOMS
    bedrooms_meta.slug = "bedrooms"
    bedrooms_meta.id = "bedrooms"
    bedrooms_meta.state = AreaState.AREA_STATE_OCCUPIED
    bedrooms_meta.config = {
        CONF_NAME: META_AREA_BEDROOMS,
        CONF_ID: "bedrooms",
        CONF_TYPE: AREA_TYPE_META,
        CONF_CHILD_AREAS: ["master_bedroom", "ensuite"],
    }
    bedrooms_meta.entities = {
        LIGHT_DOMAIN: [
            {ATTR_ENTITY_ID: "light.master_bedroom_ceiling"},
            {ATTR_ENTITY_ID: "light.ensuite_vanity"},
        ]
    }
    bedrooms_meta.initialized = True

    hass.states.async_set(
        "sensor.simply_magic_areas_state_master_bedroom", AreaState.AREA_STATE_OCCUPIED
    )
    hass.states.async_set(
        "sensor.simply_magic_areas_state_ensuite", AreaState.AREA_STATE_OCCUPIED
    )
    hass.states.async_set("light.master_bedroom_ceiling", STATE_ON)
    hass.states.async_set("light.ensuite_vanity", STATE_OFF)

    bedroom_sensor = AreaStateSensor(bedrooms_meta)
    bedroom_sensor.hass = hass
    bedroom_sensor.entity_id = "sensor.simply_magic_areas_state_bedrooms"
    bedroom_sensor.schedule_update_ha_state = MagicMock()
    bedroom_sensor._load_presence_sensors()

    # While at least one light in the tied bedroom rooms is ON, the bedroom meta-area is OCCUPIED
    assert set(bedrooms_meta.get_child_areas()) == {"master_bedroom", "ensuite"}
    assert set(bedrooms_meta.get_active_areas()) == {"master_bedroom", "ensuite"}
    assert bedroom_sensor.get_current_area_state() == AreaState.AREA_STATE_OCCUPIED

    # When all lights across the tied bedroom rooms turn OFF, the bedroom meta-area is immediately UNOCCUPIED (CLEAR)
    hass.states.async_set("light.master_bedroom_ceiling", STATE_OFF)
    hass.states.async_set("light.ensuite_vanity", STATE_OFF)
    assert bedrooms_meta.get_active_areas() == []
    assert bedroom_sensor.get_current_area_state() == AreaState.AREA_STATE_CLEAR


async def test_auto_sleep_mode_after_10pm_when_house_or_room_unoccupied(
    hass: HomeAssistant,
) -> None:
    """Test that Sleep mode triggers when the house or room is unoccupied at night after 10pm and dims lights on re-entry."""
    from unittest.mock import MagicMock

    from ..base.area_lighting_profile_sensor import AreaLightingProfileSensor

    hass.states.async_set(
        "sun.sun", "below_horizon", {"elevation": -15.0, "local_hour": 20}
    )
    hass.states.async_set(
        "sensor.simply_magic_areas_state_interior", AreaState.AREA_STATE_OCCUPIED
    )

    area = MagicMock()
    area.hass = hass
    area.name = "Hallway"
    area.slug = "hallway"
    area.id = "hallway"
    area.state = AreaState.AREA_STATE_OCCUPIED
    area.lighting_profile = LightingProfile.STANDARD
    area.is_meta.return_value = False
    area.is_exterior.return_value = False
    area.is_bedroom_or_ensuite.return_value = False
    area.is_bedroom_meta.return_value = False
    area.is_main_house_area.return_value = True
    area.is_control_enabled.return_value = True
    area.config = {}
    area.feature_config.return_value = {"sleep_state_dim": 25, "occupied_state_dim": 100}
    area.all_state_configs.return_value = {}
    area.get_parent_meta_areas.return_value = []
    area.get_effective_illuminance.return_value = 0.0
    area.simply_magic_entity_id.side_effect = (
        lambda domain, name, area_name=None: f"{domain}.simply_magic_areas_{name}_{area_name or 'hallway'}"
    )

    profile_sensor = AreaLightingProfileSensor(area)
    profile_sensor.hass = hass
    profile_sensor.entity_id = "sensor.simply_magic_areas_lighting_profile_hallway"
    profile_sensor.schedule_update_ha_state = MagicMock()

    # 1. At 8:00 PM (20:00), even if unoccupied, profile is STANDARD (not after 10pm yet)
    area.state = AreaState.AREA_STATE_CLEAR
    assert profile_sensor._calculate_lighting_profile() == LightingProfile.STANDARD

    # 2. At 10:00 PM (22:00) while room and house are OCCUPIED, profile remains STANDARD
    hass.states.async_set(
        "sun.sun", "below_horizon", {"elevation": -25.0, "local_hour": 22}
    )
    area.state = AreaState.AREA_STATE_OCCUPIED
    hass.states.async_set(
        "sensor.simply_magic_areas_state_hallway", AreaState.AREA_STATE_OCCUPIED
    )
    assert profile_sensor._calculate_lighting_profile() == LightingProfile.STANDARD

    # 3. At 10:00 PM (22:00) when the room or house becomes UNOCCUPIED (CLEAR), Sleep mode triggers!
    hass.states.async_set(
        "sensor.simply_magic_areas_state_interior", AreaState.AREA_STATE_CLEAR
    )
    assert profile_sensor._calculate_lighting_profile() == LightingProfile.SLEEP
    area.lighting_profile = LightingProfile.SLEEP

    # 4. When someone walks into the room at 11:00 PM (23:00), Sleep mode stays latched and resolves to sleep_state_dim (25%)
    hass.states.async_set(
        "sun.sun", "below_horizon", {"elevation": -30.0, "local_hour": 23}
    )
    area.state = AreaState.AREA_STATE_OCCUPIED
    hass.states.async_set(
        "sensor.simply_magic_areas_state_hallway", AreaState.AREA_STATE_OCCUPIED
    )
    hass.states.async_set(
        "sensor.simply_magic_areas_state_interior", AreaState.AREA_STATE_OCCUPIED
    )
    assert profile_sensor._calculate_lighting_profile() == LightingProfile.SLEEP

    area.has_configured_presence_sensors.return_value = True
    area.has_configured_state.side_effect = (
        lambda st: st == AreaState.AREA_STATE_OCCUPIED
    )
    area.state_config.side_effect = lambda st: (
        StateConfigData(
            name="occupied",
            entity=None,
            entity_state_on=STATE_ON,
            dim_level=100,
            for_state=AreaState.AREA_STATE_OCCUPIED,
            icon="mdi:lightbulb",
            control_entity="switch.simply_magic_areas_light_control_hallway",
            lights=["light.hallway_main"],
        )
        if st == AreaState.AREA_STATE_OCCUPIED
        else None
    )
    area.entities = {LIGHT_DOMAIN: [{ATTR_ENTITY_ID: "light.hallway_main"}]}
    lg = AreaLightGroup(area, ["light.hallway_main"])
    lg.hass = hass
    active_conf = lg._get_active_profile_config()
    assert active_conf is not None
    assert active_conf.dim_level == 25

    # 5. In the morning (08:00, above_horizon), Sleep mode resets back to STANDARD
    hass.states.async_set(
        "sun.sun", "above_horizon", {"elevation": 15.0, "local_hour": 8}
    )
    assert profile_sensor._calculate_lighting_profile() == LightingProfile.STANDARD

    # 6. In a BEDROOM, Sleep mode after 10pm is ONLY set after the bedroom light is turned ON and then OFF
    hass.states.async_set(
        "sun.sun", "below_horizon", {"elevation": -25.0, "local_hour": 22}
    )
    bed_area = MagicMock()
    bed_area.hass = hass
    bed_area.name = "Master Bedroom"
    bed_area.slug = "master_bedroom"
    bed_area.id = "master_bedroom"
    bed_area.state = AreaState.AREA_STATE_CLEAR
    bed_area.lighting_profile = LightingProfile.STANDARD
    bed_area.is_meta.return_value = False
    bed_area.is_exterior.return_value = False
    bed_area.is_bedroom_or_ensuite.return_value = True
    bed_area.is_bedroom_meta.return_value = False
    bed_area.is_main_house_area.return_value = False
    bed_area.is_control_enabled.return_value = True
    bed_area.config = {}
    bed_area.feature_config.return_value = {"sleep_state_dim": 20}
    bed_area.all_state_configs.return_value = {}
    bed_area.get_parent_meta_areas.return_value = []
    bed_area.get_effective_illuminance.return_value = 0.0
    bed_area.entities = {LIGHT_DOMAIN: [{ATTR_ENTITY_ID: "light.master_bedroom_main"}]}
    bed_area.simply_magic_entity_id.side_effect = (
        lambda domain, name, area_name=None: f"{domain}.simply_magic_areas_{name}_{area_name or 'master_bedroom'}"
    )

    hass.states.async_set("light.master_bedroom_main", STATE_OFF)
    hass.states.async_set(
        "light.simply_magic_areas_light_master_bedroom", STATE_OFF
    )

    bed_profile_sensor = AreaLightingProfileSensor(bed_area)
    bed_profile_sensor.hass = hass
    bed_profile_sensor.entity_id = (
        "sensor.simply_magic_areas_lighting_profile_master_bedroom"
    )
    bed_profile_sensor.schedule_update_ha_state = MagicMock()

    # At 10:00 PM when bedroom light has NOT been turned ON yet, bedroom remains STANDARD (not SLEEP)
    assert bed_profile_sensor._calculate_lighting_profile() == LightingProfile.STANDARD

    # Occupant enters bedroom and turns the light ON -> still STANDARD
    hass.states.async_set("light.master_bedroom_main", STATE_ON)
    hass.states.async_set(
        "light.simply_magic_areas_light_master_bedroom", STATE_ON
    )
    assert bed_profile_sensor._calculate_lighting_profile() == LightingProfile.STANDARD

    # Occupant gets in bed and turns the bedroom light OFF -> now SLEEP mode is set!
    hass.states.async_set("light.master_bedroom_main", STATE_OFF)
    hass.states.async_set(
        "light.simply_magic_areas_light_master_bedroom", STATE_OFF
    )
    assert bed_profile_sensor._calculate_lighting_profile() == LightingProfile.SLEEP

    # Morning routine disables sleep mode immediately even before 7am
    bed_profile_sensor.disable_morning_sleep()
    assert bed_profile_sensor._calculate_lighting_profile() == LightingProfile.STANDARD


async def test_manual_sleep_mode_switches_and_flags_for_house_and_bedrooms(
    hass: HomeAssistant,
) -> None:
    """Test manual sleep mode switches/flags for the house (Interior/Global) and bedrooms/bedroom areas independently."""
    from unittest.mock import MagicMock

    from ..base.area_lighting_profile_sensor import AreaLightingProfileSensor
    from ..base.magic import MagicArea, MagicMetaArea
    from ..const import (
        AREA_TYPE_BEDROOM,
        AREA_TYPE_INTERIOR,
        AREA_TYPE_META,
        CONF_CHILD_AREAS,
        CONF_ID,
        CONF_NAME,
        CONF_TYPE,
        DATA_AREA_OBJECT,
        META_AREA_BEDROOMS,
        META_AREA_INTERIOR,
        MODULE_DATA,
    )
    from ..switch import AreaSleepModeSwitch

    def _build_area(
        name: str,
        slug: str,
        area_type: str,
        is_meta: bool = False,
        extra_conf: dict | None = None,
    ) -> MagicArea:
        cls = MagicMetaArea if is_meta else MagicArea
        area = cls.__new__(cls)
        area.hass = hass
        area.name = name
        area.slug = slug
        area.id = slug
        area.icon = "mdi:room"
        area.state = AreaState.AREA_STATE_OCCUPIED
        area.lighting_profile = LightingProfile.STANDARD
        area.sleep_mode_enabled = False
        area.config = {
            CONF_NAME: name,
            CONF_ID: slug,
            CONF_TYPE: area_type,
            **(extra_conf or {}),
        }
        area.entities = {LIGHT_DOMAIN: [{ATTR_ENTITY_ID: f"light.{slug}"}]}
        area._state_config = {}
        area.initialized = True
        return area

    living_room = _build_area("Living Room", "living_room", AREA_TYPE_INTERIOR)
    kitchen = _build_area("Kitchen", "kitchen", AREA_TYPE_INTERIOR)
    master_bedroom = _build_area(
        "Master Bedroom",
        "master_bedroom",
        AREA_TYPE_BEDROOM,
        extra_conf={CONF_CHILD_AREAS: ["master_bedroom", "master_ensuite"]},
    )
    master_ensuite = _build_area(
        "Master Ensuite", "master_ensuite", AREA_TYPE_INTERIOR
    )
    guest_bedroom = _build_area(
        "Guest Bedroom", "guest_bedroom", AREA_TYPE_BEDROOM
    )
    interior_meta = _build_area(
        META_AREA_INTERIOR, "interior", AREA_TYPE_META, is_meta=True
    )
    bedrooms_meta = _build_area(
        META_AREA_BEDROOMS, "bedrooms", AREA_TYPE_META, is_meta=True
    )

    hass.data[MODULE_DATA] = {
        "entry_living": {DATA_AREA_OBJECT: living_room},
        "entry_kitchen": {DATA_AREA_OBJECT: kitchen},
        "entry_master": {DATA_AREA_OBJECT: master_bedroom},
        "entry_ensuite": {DATA_AREA_OBJECT: master_ensuite},
        "entry_guest": {DATA_AREA_OBJECT: guest_bedroom},
        "entry_interior": {DATA_AREA_OBJECT: interior_meta},
        "entry_bedrooms": {DATA_AREA_OBJECT: bedrooms_meta},
    }

    sensors: dict[str, AreaLightingProfileSensor] = {}
    switches: dict[str, AreaSleepModeSwitch] = {}
    for area_obj in (
        living_room,
        kitchen,
        master_bedroom,
        master_ensuite,
        guest_bedroom,
        interior_meta,
        bedrooms_meta,
    ):
        sw = AreaSleepModeSwitch(area_obj)
        sw.hass = hass
        sw.entity_id = f"switch.simply_magic_areas_sleep_mode_{area_obj.slug}"
        sw.schedule_update_ha_state = MagicMock(
            side_effect=lambda s=sw: hass.states.async_set(
                s.entity_id, STATE_ON if s.is_on else STATE_OFF
            )
        )
        area_obj._sleep_mode_switch = sw  # type: ignore[attr-defined]
        hass.states.async_set(sw.entity_id, STATE_OFF)
        switches[area_obj.slug] = sw

        ps = AreaLightingProfileSensor(area_obj)
        ps.hass = hass
        ps.entity_id = (
            f"sensor.simply_magic_areas_lighting_profile_{area_obj.slug}"
        )
        ps.schedule_update_ha_state = MagicMock(
            side_effect=lambda p=ps: hass.states.async_set(
                p.entity_id, p.native_value, p.extra_state_attributes
            )
        )
        area_obj._lighting_profile_sensor = ps  # type: ignore[attr-defined]
        await ps._setup_listeners()
        sensors[area_obj.slug] = ps

    # 1. Turn ON House Sleep Mode via switch.simply_magic_areas_sleep_mode_interior (e.g. from a remote button)
    await switches["interior"].async_turn_on()
    await hass.async_block_till_done()

    assert interior_meta.lighting_profile == LightingProfile.SLEEP
    assert living_room.lighting_profile == LightingProfile.SLEEP
    assert kitchen.lighting_profile == LightingProfile.SLEEP
    # Bedrooms remain independent (STANDARD) when house sleep mode is turned ON
    assert master_bedroom.lighting_profile == LightingProfile.STANDARD
    assert master_ensuite.lighting_profile == LightingProfile.STANDARD
    assert guest_bedroom.lighting_profile == LightingProfile.STANDARD

    # 2. Turn OFF House Sleep Mode -> Living Room and Kitchen return to STANDARD
    await switches["interior"].async_turn_off()
    await hass.async_block_till_done()

    assert interior_meta.lighting_profile == LightingProfile.STANDARD
    assert living_room.lighting_profile == LightingProfile.STANDARD
    assert kitchen.lighting_profile == LightingProfile.STANDARD

    # 3. Turn ON Master Bedroom Sleep Mode via switch.simply_magic_areas_sleep_mode_master_bedroom
    # Master Bedroom and its tied child room (Master Ensuite) enter SLEEP mode; Guest Bedroom & House stay STANDARD
    await switches["master_bedroom"].async_turn_on()
    await hass.async_block_till_done()

    assert master_bedroom.lighting_profile == LightingProfile.SLEEP
    assert master_ensuite.lighting_profile == LightingProfile.SLEEP
    assert guest_bedroom.lighting_profile == LightingProfile.STANDARD
    assert living_room.lighting_profile == LightingProfile.STANDARD

    # Turn OFF Master Bedroom Sleep Mode -> Master Bedroom and Master Ensuite return to STANDARD
    await switches["master_bedroom"].async_turn_off()
    await hass.async_block_till_done()

    assert master_bedroom.lighting_profile == LightingProfile.STANDARD
    assert master_ensuite.lighting_profile == LightingProfile.STANDARD

    # 4. Turn ON Bedrooms Meta-Area Sleep Mode via switch.simply_magic_areas_sleep_mode_bedrooms
    # All bedrooms & ensuites enter SLEEP mode while main house stays STANDARD
    await switches["bedrooms"].async_turn_on()
    await hass.async_block_till_done()

    assert bedrooms_meta.lighting_profile == LightingProfile.SLEEP
    assert master_bedroom.lighting_profile == LightingProfile.SLEEP
    assert master_ensuite.lighting_profile == LightingProfile.SLEEP
    assert guest_bedroom.lighting_profile == LightingProfile.SLEEP
    assert living_room.lighting_profile == LightingProfile.STANDARD
    assert kitchen.lighting_profile == LightingProfile.STANDARD

    # 5. Morning routine disables manual sleep mode and resets the sleep mode switches to OFF
    sensors["master_bedroom"].disable_morning_sleep()
    assert master_bedroom.lighting_profile == LightingProfile.STANDARD
    assert switches["master_bedroom"].is_on is False


async def test_no_light_flashing_or_flip_flop_on_sync_dimmer_ramp_and_lux_feedback(
    hass: HomeAssistant,
) -> None:
    """Verify lights commit to a single value and never flash/flip-flop across multi-light sync, dimmer ramps, manual-off extended transitions, or self-illuminated lux spikes."""
    from unittest.mock import MagicMock

    from ..base.area_lighting_profile_sensor import AreaLightingProfileSensor

    on_calls = async_mock_service(hass, LIGHT_DOMAIN, SERVICE_TURN_ON)
    off_calls = async_mock_service(hass, LIGHT_DOMAIN, "turn_off")

    hass.states.async_set("light.room_light_1", STATE_ON, {ATTR_BRIGHTNESS: 255})
    hass.states.async_set("light.room_light_2", STATE_ON, {ATTR_BRIGHTNESS: 255})

    area = MagicMock()
    area.hass = hass
    area.name = "Family Room"
    area.slug = "family_room"
    area.id = "family_room"
    area.state = AreaState.AREA_STATE_OCCUPIED
    area.lighting_profile = LightingProfile.STANDARD
    area.is_meta.return_value = False
    area.is_exterior.return_value = False
    area.is_interior.return_value = True
    area.is_bedroom_or_ensuite.return_value = False
    area.is_bedroom_meta.return_value = False
    area.is_main_house_area.return_value = True
    area.is_control_enabled.return_value = True
    area.has_configured_presence_sensors.return_value = True
    area.has_configured_state.return_value = True
    area.config = {}
    area.feature_config.return_value = {}
    area.entities = {
        LIGHT_DOMAIN: [
            {ATTR_ENTITY_ID: "light.room_light_1"},
            {ATTR_ENTITY_ID: "light.room_light_2"},
        ],
        BINARY_SENSOR_DOMAIN: [
            {
                ATTR_ENTITY_ID: "binary_sensor.family_room_motion",
                ATTR_DEVICE_CLASS: BinarySensorDeviceClass.MOTION,
            }
        ],
    }
    area.state_config.side_effect = lambda st: (
        StateConfigData(
            name="occupied",
            entity=None,
            entity_state_on=STATE_ON,
            dim_level=100,
            for_state=AreaState.AREA_STATE_OCCUPIED,
            icon="mdi:lightbulb",
            control_entity="switch.simply_magic_areas_light_control_family_room",
            lights=["light.room_light_1", "light.room_light_2"],
        )
        if st == AreaState.AREA_STATE_OCCUPIED
        else StateConfigData(
            name="extended",
            entity=None,
            entity_state_on=STATE_ON,
            dim_level=0,
            for_state=AreaState.AREA_STATE_EXTENDED,
            icon="mdi:lightbulb",
            control_entity="switch.simply_magic_areas_light_control_family_room",
            lights=["light.room_light_1", "light.room_light_2"],
        )
    )
    area.all_state_configs.return_value = {}
    area.get_parent_meta_areas.return_value = []
    area.get_effective_illuminance.return_value = 5.0
    area.simply_magic_entity_id.side_effect = (
        lambda domain, name, area_name=None: (
            f"{domain}.simply_magic_areas_{name}_{area_name or 'family_room'}"
        )
    )

    lg = AreaLightGroup(area, ["light.room_light_1", "light.room_light_2"])
    lg.hass = hass
    lg._attr_name = "Family Room Lights"
    lg.entity_id = "light.simply_magic_areas_light_family_room"
    lg._attr_is_on = True
    lg.schedule_update_ha_state = MagicMock()
    lg.async_update_group_state = MagicMock()

    # 1. Multi-light turn-off commitment: light 1 turns OFF while light 2 is still ON (ramping down).
    #    Even when LightGroup recomputes group state as ON (because light 2 hasn't finished turning off yet)
    #    and light 2 reports intermediate dimmer ramp values (180 -> 90 -> OFF), NO turn_on call is ever fired!
    hass.states.async_set("light.room_light_1", STATE_OFF)
    lg._member_light_state_change(
        MagicMock(
            data={
                "entity_id": "light.room_light_1",
                "old_state": MagicMock(
                    state=STATE_ON, attributes={ATTR_BRIGHTNESS: 255}
                ),
                "new_state": MagicMock(state=STATE_OFF, attributes={}),
            }
        )
    )
    await hass.async_block_till_done()
    assert len(off_calls) == 1
    assert len(on_calls) == 0

    # Simulate LightGroup transiently reporting STATE_ON (because light 2 is still ramping down at 180)
    lg._update_group_state(
        MagicMock(
            data={
                "old_state": MagicMock(state=STATE_OFF, attributes={}),
                "new_state": MagicMock(
                    state=STATE_ON, attributes={ATTR_BRIGHTNESS: 180}
                ),
            }
        )
    )
    # Simulate light 2 reporting intermediate ramp-down brightness 180 -> 90 -> OFF
    hass.states.async_set("light.room_light_2", STATE_ON, {ATTR_BRIGHTNESS: 90})
    lg._member_light_state_change(
        MagicMock(
            data={
                "entity_id": "light.room_light_2",
                "old_state": MagicMock(
                    state=STATE_ON, attributes={ATTR_BRIGHTNESS: 180}
                ),
                "new_state": MagicMock(
                    state=STATE_ON, attributes={ATTR_BRIGHTNESS: 90}
                ),
            }
        )
    )
    await hass.async_block_till_done()
    assert len(on_calls) == 0

    # 2. Manual-off followed by OCCUPIED -> EXTENDED transition while light 2 is still ramping down:
    #    Must NOT trigger the 50% extended warning dim to turn the lights back ON!
    area.state = AreaState.AREA_STATE_EXTENDED
    lg._area_state_change(
        MagicMock(
            event_type="state_changed",
            data={
                "old_state": MagicMock(state=AreaState.AREA_STATE_OCCUPIED),
                "new_state": MagicMock(state=AreaState.AREA_STATE_EXTENDED),
            },
        )
    )
    await hass.async_block_till_done()
    assert len(on_calls) == 0
    lg._cancel_retry_timer()

    # 3. Self-illuminated indoor lux sensor immunity:
    #    When room lights are ON and illuminate the room's lux sensor above learned daylight (e.g. 150 lx),
    #    AreaLightingProfileSensor must NOT flip from STANDARD to BRIGHT (which would turn the lights off).
    hass.states.async_set("light.room_light_1", STATE_ON, {ATTR_BRIGHTNESS: 255})
    hass.states.async_set("light.room_light_2", STATE_ON, {ATTR_BRIGHTNESS: 255})
    hass.states.async_set(
        "light.simply_magic_areas_light_family_room",
        STATE_ON,
        {ATTR_BRIGHTNESS: 255},
    )
    area.state = AreaState.AREA_STATE_OCCUPIED
    area.lighting_profile = LightingProfile.STANDARD
    area.historical_daylight_lux = 80.0
    area.get_effective_illuminance.return_value = 150.0  # Self-illuminated by room lights!

    profile_sensor = AreaLightingProfileSensor(area)
    profile_sensor.hass = hass
    profile_sensor.entity_id = (
        "sensor.simply_magic_areas_lighting_profile_family_room"
    )
    profile_sensor.schedule_update_ha_state = MagicMock()
    profile_sensor._historical_daylight_lux = 80.0

    assert profile_sensor._is_bright_condition_met() is False
    assert profile_sensor._calculate_lighting_profile() == LightingProfile.STANDARD

    # 4. Smart light bulb disambiguation from manual override:
    #    (a) In a multi-light room under automatic control (Sleep mode dim=76), LightGroup's virtual
    #        average-brightness recalculation on self.entity_id (even after command grace expires)
    #        must NEVER set manual_control = True or re-turn-on bulbs.
    lg._reset_control()
    lg._expected_state = STATE_ON
    lg._expected_brightness = 76
    lg._command_in_flight_until = 0.0
    lg._sync_settle_until = 0.0
    lg._last_member_manual_ts = 0.0
    on_calls.clear()
    off_calls.clear()

    lg._update_group_state(
        MagicMock(
            data={
                "old_state": MagicMock(
                    state=STATE_ON, attributes={ATTR_BRIGHTNESS: 76}
                ),
                "new_state": MagicMock(
                    state=STATE_ON, attributes={ATTR_BRIGHTNESS: 165}
                ),
            }
        )
    )
    assert lg._is_controlled_by_this_entity() is True
    assert len(on_calls) == 0

    #    (b) Slow smart bulb finally reporting the expected state (STATE_ON) 4s after automation
    #        turn-on (after 2.5s command grace expires) must NEVER trigger manual_control = True.
    lg._last_turn_on_ts = datetime.now(UTC).timestamp() - 4.0
    lg._member_light_state_change(
        MagicMock(
            data={
                "entity_id": "light.room_light_2",
                "old_state": MagicMock(state=STATE_OFF, attributes={}),
                "new_state": MagicMock(
                    state=STATE_ON, attributes={ATTR_BRIGHTNESS: 76}
                ),
            }
        )
    )
    assert lg._is_controlled_by_this_entity() is True
    lg._last_turn_on_ts = 0.0

    #    (c) Smart bulb booting from unavailable -> STATE_ON at 100% (255) when switched power is
    #        restored in Sleep mode (_expected_brightness=76) must NOT trigger manual_control = True,
    #        and must schedule retry to dim the newly-booted bulb to 76.
    hass.states.async_set("light.room_light_2", STATE_ON, {ATTR_BRIGHTNESS: 255})
    lg._member_light_state_change(
        MagicMock(
            data={
                "entity_id": "light.room_light_2",
                "old_state": MagicMock(state=STATE_UNAVAILABLE, attributes={}),
                "new_state": MagicMock(
                    state=STATE_ON, attributes={ATTR_BRIGHTNESS: 255}
                ),
            }
        )
    )
    assert lg._is_controlled_by_this_entity() is True
    assert lg._retry_timer_cb is not None
    lg._cancel_retry_timer()

    # 5. Settling & smoothing of source dimmer ramps and hardware PWM brightness jitter:
    #    (a) Small hardware brightness jitter (<= 8 units, e.g. 76 -> 72) is smoothed/ignored
    lg._reset_control()
    lg._expected_state = STATE_ON
    lg._expected_brightness = 76
    lg._command_in_flight_until = 0.0
    lg._sync_settle_until = 0.0
    on_calls.clear()
    off_calls.clear()
    lg._member_light_state_change(
        MagicMock(
            data={
                "entity_id": "light.room_light_1",
                "old_state": MagicMock(
                    state=STATE_ON, attributes={ATTR_BRIGHTNESS: 76}
                ),
                "new_state": MagicMock(
                    state=STATE_ON, attributes={ATTR_BRIGHTNESS: 72}
                ),
            }
        )
    )
    assert lg._is_controlled_by_this_entity() is True
    assert len(on_calls) == 0

    #    (b) Source dimmer turning ON and ramping 30 -> 140 -> 255 coalesces intermediate steps
    #        into the settle timer and flushes the final settled brightness (255) once settled
    hass.states.async_set("light.room_light_1", STATE_ON, {ATTR_BRIGHTNESS: 30})
    hass.states.async_set("light.room_light_2", STATE_OFF)
    lg._member_light_state_change(
        MagicMock(
            data={
                "entity_id": "light.room_light_1",
                "old_state": MagicMock(state=STATE_OFF, attributes={}),
                "new_state": MagicMock(
                    state=STATE_ON, attributes={ATTR_BRIGHTNESS: 30}
                ),
            }
        )
    )
    await hass.async_block_till_done()
    assert len(on_calls) == 1
    assert on_calls[-1].data[ATTR_BRIGHTNESS] == 30

    # Source dimmer continues ramping 30 -> 140 -> 255 within settle window:
    # coalesced without spamming service calls until settle timer flushes!
    on_calls.clear()
    hass.states.async_set("light.room_light_1", STATE_ON, {ATTR_BRIGHTNESS: 140})
    lg._member_light_state_change(
        MagicMock(
            data={
                "entity_id": "light.room_light_1",
                "old_state": MagicMock(
                    state=STATE_ON, attributes={ATTR_BRIGHTNESS: 30}
                ),
                "new_state": MagicMock(
                    state=STATE_ON, attributes={ATTR_BRIGHTNESS: 140}
                ),
            }
        )
    )
    hass.states.async_set("light.room_light_1", STATE_ON, {ATTR_BRIGHTNESS: 255})
    lg._member_light_state_change(
        MagicMock(
            data={
                "entity_id": "light.room_light_1",
                "old_state": MagicMock(
                    state=STATE_ON, attributes={ATTR_BRIGHTNESS: 140}
                ),
                "new_state": MagicMock(
                    state=STATE_ON, attributes={ATTR_BRIGHTNESS: 255}
                ),
            }
        )
    )
    assert len(on_calls) == 0
    assert lg._settle_timer_cb is not None
    assert lg._expected_brightness == 255

    # When the dimmer finishes settling, _flush_settled_room_lights sends a single sync to 255
    lg._cancel_settle_timer()
    lg._flush_settled_room_lights()
    await hass.async_block_till_done()
    assert len(on_calls) == 1
    assert on_calls[-1].data[ATTR_BRIGHTNESS] == 255

    #    (c) Source dimmer ramping down 255 -> 180 -> STATE_OFF immediately supersedes the
    #        initial 180 brightness report and turns the room OFF cleanly
    on_calls.clear()
    off_calls.clear()
    hass.states.async_set("light.room_light_2", STATE_ON, {ATTR_BRIGHTNESS: 255})
    hass.states.async_set("light.room_light_1", STATE_ON, {ATTR_BRIGHTNESS: 180})
    lg._command_in_flight_until = 0.0
    lg._sync_settle_until = 0.0
    lg._member_light_state_change(
        MagicMock(
            data={
                "entity_id": "light.room_light_1",
                "old_state": MagicMock(
                    state=STATE_ON, attributes={ATTR_BRIGHTNESS: 255}
                ),
                "new_state": MagicMock(
                    state=STATE_ON, attributes={ATTR_BRIGHTNESS: 180}
                ),
            }
        )
    )
    # Now light_1 finishes ramping down to OFF while in_sync_settle:
    hass.states.async_set("light.room_light_1", STATE_OFF)
    lg._member_light_state_change(
        MagicMock(
            data={
                "entity_id": "light.room_light_1",
                "old_state": MagicMock(
                    state=STATE_ON, attributes={ATTR_BRIGHTNESS: 180}
                ),
                "new_state": MagicMock(state=STATE_OFF, attributes={}),
            }
        )
    )
    await hass.async_block_till_done()
    assert len(off_calls) == 1
    assert lg._expected_state == STATE_OFF
    lg._cancel_retry_timer()

    # 6. Multi-light desired-state tracking & anti-feedback-loop over time:
    #    (a) Even after command grace and sync settle expire, a second bulb slowly ramping
    #        toward _expected_brightness (255 -> 180 -> 110 -> 76) NEVER triggers a manual
    #        override feedback loop or alters _expected_brightness.
    on_calls.clear()
    off_calls.clear()
    lg._expected_state = STATE_ON
    lg._expected_brightness = 76
    lg._command_in_flight_until = 0.0
    lg._sync_settle_until = 0.0
    lg._automation_transition_until = 0.0
    hass.states.async_set("light.room_light_1", STATE_ON, {ATTR_BRIGHTNESS: 76})
    hass.states.async_set("light.room_light_2", STATE_ON, {ATTR_BRIGHTNESS: 180})
    lg._member_light_state_change(
        MagicMock(
            data={
                "entity_id": "light.room_light_2",
                "old_state": MagicMock(
                    state=STATE_ON, attributes={ATTR_BRIGHTNESS: 255}
                ),
                "new_state": MagicMock(
                    state=STATE_ON, attributes={ATTR_BRIGHTNESS: 180}
                ),
            }
        )
    )
    hass.states.async_set("light.room_light_2", STATE_ON, {ATTR_BRIGHTNESS: 110})
    lg._member_light_state_change(
        MagicMock(
            data={
                "entity_id": "light.room_light_2",
                "old_state": MagicMock(
                    state=STATE_ON, attributes={ATTR_BRIGHTNESS: 180}
                ),
                "new_state": MagicMock(
                    state=STATE_ON, attributes={ATTR_BRIGHTNESS: 110}
                ),
            }
        )
    )
    assert len(on_calls) == 0
    assert lg._expected_brightness == 76

    #    (b) Out-of-phase command echo: turn_off at t-4s superseded by turn_on at t-3s.
    #        When a slow bulb reports STATE_OFF at t=0 (after 2.5s command grace), it is caught
    #        as a superseded echo, does NOT set manual_control, and schedules retry for STATE_ON.
    lg._reset_control()
    now_ts = datetime.now(UTC).timestamp()
    lg._expected_state = STATE_ON
    lg._expected_brightness = 255
    lg._last_turn_off_ts = now_ts - 4.0
    lg._last_turn_on_ts = now_ts - 3.0
    lg._command_in_flight_until = 0.0
    lg._sync_settle_until = 0.0
    hass.states.async_set("light.room_light_2", STATE_OFF)
    lg._member_light_state_change(
        MagicMock(
            data={
                "entity_id": "light.room_light_2",
                "old_state": MagicMock(
                    state=STATE_ON, attributes={ATTR_BRIGHTNESS: 255}
                ),
                "new_state": MagicMock(state=STATE_OFF, attributes={}),
            }
        )
    )
    assert lg._is_controlled_by_this_entity() is True
    assert lg._expected_state == STATE_ON
    assert lg._retry_timer_cb is not None
    lg._cancel_retry_timer()


async def test_system_control_off_immediately_halts_all_light_changes_and_timers(
    hass: HomeAssistant,
) -> None:
    """Verify turning off system_control immediately cancels timers and blocks all light changes."""
    from unittest.mock import MagicMock

    from custom_components.simply_magic_areas.switch import AreaSystemControlSwitch

    area = MagicMock()
    area.hass = hass
    area.name = "Family Room"
    area.slug = "family_room"
    area.id = "family_room"
    area.state = AreaState.AREA_STATE_OCCUPIED
    area.lighting_profile = LightingProfile.STANDARD
    area.system_control_enabled = True
    area.is_meta.return_value = False
    area.is_exterior.return_value = False
    area.is_control_enabled.side_effect = (
        lambda ct: area.system_control_enabled
    )
    area.has_configured_state.return_value = True
    area.state_config.return_value = StateConfigData(
        name="occupied",
        entity=None,
        entity_state_on=STATE_ON,
        dim_level=100,
        for_state=AreaState.AREA_STATE_OCCUPIED,
        icon="mdi:lightbulb",
        control_entity="switch.simply_magic_areas_light_control_family_room",
        lights=["light.room_light_1", "light.room_light_2"],
    )
    area.config = {}
    area.feature_config.return_value = {"sleep_state_dim": 30}
    area.get_effective_illuminance.return_value = 0.0
    area.entities = {
        LIGHT_DOMAIN: [
            {ATTR_ENTITY_ID: "light.room_light_1"},
            {ATTR_ENTITY_ID: "light.room_light_2"},
        ],
    }
    area.simply_magic_entity_id.side_effect = (
        lambda domain, name, area_name=None: (
            f"{domain}.simply_magic_areas_{name}_{area_name or 'family_room'}"
        )
    )

    hass.states.async_set(
        "sensor.simply_magic_areas_state_family_room",
        AreaState.AREA_STATE_OCCUPIED,
        {"presence_sensors": ["binary_sensor.family_motion"]},
    )
    hass.states.async_set(
        "sensor.simply_magic_areas_lighting_profile_family_room",
        LightingProfile.STANDARD,
    )
    hass.states.async_set("light.room_light_1", STATE_ON, {ATTR_BRIGHTNESS: 255})
    hass.states.async_set("light.room_light_2", STATE_OFF)

    lg = AreaLightGroup(area, ["light.room_light_1", "light.room_light_2"])
    lg.hass = hass
    lg._attr_name = "Family Room Lights"
    lg.entity_id = "light.simply_magic_areas_light_family_room"
    lg.schedule_update_ha_state = MagicMock()
    lg.async_update_group_state = MagicMock()
    area._light_group = lg

    sys_sw = AreaSystemControlSwitch(area)
    sys_sw.hass = hass
    sys_sw.entity_id = "switch.simply_magic_areas_system_control_family_room"
    sys_sw.schedule_update_ha_state = MagicMock()
    area._system_control_switch = sys_sw

    def _set_sys_control(enabled: bool) -> None:
        area.system_control_enabled = enabled
        if not enabled:
            lg.stop_all_active_control()
            area.state = AreaState.AREA_STATE_MANUAL

    area.set_system_control.side_effect = _set_sys_control

    on_calls = async_mock_service(hass, LIGHT_DOMAIN, SERVICE_TURN_ON)
    off_calls = async_mock_service(hass, LIGHT_DOMAIN, "turn_off")

    # 1. Verify single-flight command coalescing & deduplication:
    #    Rapid back-to-back commands before the event loop flushes must cancel superseded
    #    tasks rather than stacking up a queue of changes.
    lg._dispatch_light_command(
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: lg.entity_id, ATTR_BRIGHTNESS: 255},
    )
    first_task = lg._active_service_task
    assert first_task is not None and not first_task.done()

    # Duplicate command while in flight is ignored (same task stays active)
    lg._dispatch_light_command(
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: lg.entity_id, ATTR_BRIGHTNESS: 255},
    )
    assert lg._active_service_task is first_task

    # Superseding command (dim to 76) cancels the first task before it can execute
    lg._dispatch_light_command(
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: lg.entity_id, ATTR_BRIGHTNESS: 76},
    )
    second_task = lg._active_service_task
    assert second_task is not first_task
    await hass.async_block_till_done()
    assert first_task.cancelled()
    assert len(on_calls) == 1
    assert on_calls[-1].data[ATTR_BRIGHTNESS] == 76
    on_calls.clear()

    # 2. Queue a command and arm retry/settle timers, then immediately turn OFF system_control:
    #    The queued command task and all timers must be cancelled before executing!
    lg._dispatch_light_command(
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: lg.entity_id, ATTR_BRIGHTNESS: 200},
    )
    queued_task = lg._active_service_task
    assert queued_task is not None and not queued_task.done()

    lg._schedule_actuator_retry(STATE_ON, 255)
    lg._schedule_light_settle("light.room_light_1")
    assert lg._retry_timer_cb is not None
    assert lg._settle_timer_cb is not None

    # Turn OFF system control switch
    await sys_sw.async_turn_off()
    await hass.async_block_till_done()

    # Queued task and timers must be immediately cancelled (0 service calls executed!)
    assert queued_task.cancelled()
    assert lg._active_service_task is None
    assert len(on_calls) == 0
    assert lg._retry_timer_cb is None
    assert lg._settle_timer_cb is None
    assert area.system_control_enabled is False
    assert area.state == AreaState.AREA_STATE_MANUAL

    # Member light state changes, group brightness changes, profile changes, and area state changes
    # must NOT issue any light commands while system control is OFF
    lg._member_light_state_change(
        MagicMock(
            data={
                "entity_id": "light.room_light_1",
                "old_state": MagicMock(
                    state=STATE_ON, attributes={ATTR_BRIGHTNESS: 255}
                ),
                "new_state": MagicMock(state=STATE_OFF, attributes={}),
            }
        )
    )
    lg._update_group_state(
        MagicMock(
            data={
                "old_state": MagicMock(
                    state=STATE_ON, attributes={ATTR_BRIGHTNESS: 255}
                ),
                "new_state": MagicMock(
                    state=STATE_ON, attributes={ATTR_BRIGHTNESS: 120}
                ),
            }
        )
    )
    lg._profile_change(
        MagicMock(
            data={
                "old_state": MagicMock(state=LightingProfile.STANDARD),
                "new_state": MagicMock(state=LightingProfile.SLEEP),
            }
        )
    )
    lg._execute_actuator_retry()
    lg._flush_settled_room_lights()
    await hass.async_block_till_done()

    assert len(on_calls) == 0
    assert len(off_calls) == 0

    # Verify AreaLightingProfileSensor and AreaFanGroup also do nothing automatically while system_control is OFF
    from custom_components.simply_magic_areas.base.area_lighting_profile_sensor import (
        AreaLightingProfileSensor,
    )
    from custom_components.simply_magic_areas.fan import AreaFanGroup

    ps = AreaLightingProfileSensor(area)
    ps.hass = hass
    ps.entity_id = "sensor.simply_magic_areas_lighting_profile_family_room"
    ps.schedule_update_ha_state = MagicMock()
    ps._attr_native_value = LightingProfile.STANDARD
    ps._context_state_change(
        MagicMock(
            data={
                "entity_id": "sun.sun",
                "old_state": MagicMock(state="above_horizon"),
                "new_state": MagicMock(
                    state="below_horizon", attributes={"local_hour": 23}
                ),
            }
        )
    )
    assert ps._attr_native_value == LightingProfile.STANDARD

    fan_on_calls = async_mock_service(hass, "fan", SERVICE_TURN_ON)
    fg = AreaFanGroup(area, ["fan.family_fan"])
    fg.hass = hass
    fg.entity_id = "fan.simply_magic_areas_fan_family_room"
    fg.schedule_update_ha_state = MagicMock()
    fg._start_watchdog_timer()
    assert fg._watchdog_unsub is not None
    fg._humidity_fan_control()
    assert fg._watchdog_unsub is None
    assert len(fan_on_calls) == 0


async def test_bedroom_meta_area_child_areas_config_flow_only_magic_area_devices(
    hass: HomeAssistant,
) -> None:
    """Test that Bedroom Meta-Area child_areas config flow only allows other Simply Magic Area devices."""
    import pytest
    from homeassistant.data_entry_flow import FlowResultType, InvalidData
    from homeassistant.helpers.area_registry import async_get as async_get_ar
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.simply_magic_areas.base.magic import MagicArea, MagicMetaArea
    from custom_components.simply_magic_areas.const import (
        AREA_TYPE_BEDROOM,
        AREA_TYPE_INTERIOR,
        AREA_TYPE_META,
        CONF_CHILD_AREAS,
        CONF_ID,
        CONF_NAME,
        CONF_TYPE,
        DATA_AREA_OBJECT,
        DOMAIN,
        META_AREA_BEDROOMS,
        META_AREA_INTERIOR,
        MODULE_DATA,
    )

    # Create raw Home Assistant areas including an unconfigured 'attic' area
    ar = async_get_ar(hass)
    ar.async_get_or_create("Master Bedroom")
    ar.async_get_or_create("Master Ensuite")
    ar.async_get_or_create("Kitchen")
    ar.async_get_or_create("Attic")  # NOT configured as a Simply Magic Area device

    def _make_magic_area(name: str, slug: str, area_type: str) -> MagicArea:
        a = MagicArea.__new__(MagicArea)
        a.hass = hass
        a.name = name
        a.slug = slug
        a.id = slug
        a.state = AreaState.AREA_STATE_CLEAR
        a.config = {CONF_NAME: name, CONF_ID: slug, CONF_TYPE: area_type}
        a.entities = {}
        a.initialized = True
        return a

    master_bedroom = _make_magic_area(
        "Master Bedroom", "master_bedroom", AREA_TYPE_BEDROOM
    )
    master_ensuite = _make_magic_area(
        "Master Ensuite", "master_ensuite", AREA_TYPE_INTERIOR
    )
    kitchen = _make_magic_area("Kitchen", "kitchen", AREA_TYPE_INTERIOR)

    interior_meta = MagicMetaArea.__new__(MagicMetaArea)
    interior_meta.hass = hass
    interior_meta.name = META_AREA_INTERIOR
    interior_meta.slug = "interior"
    interior_meta.id = "interior"
    interior_meta.state = AreaState.AREA_STATE_CLEAR
    interior_meta.config = {
        CONF_NAME: META_AREA_INTERIOR,
        CONF_ID: "interior",
        CONF_TYPE: AREA_TYPE_META,
    }
    interior_meta.entities = {}
    interior_meta.initialized = True

    bedrooms_entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_NAME: META_AREA_BEDROOMS,
            CONF_ID: "bedrooms",
            CONF_TYPE: AREA_TYPE_META,
        },
        entry_id="entry_bedrooms_meta",
    )
    bedrooms_entry.add_to_hass(hass)

    bedrooms_meta = MagicMetaArea.__new__(MagicMetaArea)
    bedrooms_meta.hass = hass
    bedrooms_meta.name = META_AREA_BEDROOMS
    bedrooms_meta.slug = "bedrooms"
    bedrooms_meta.id = "bedrooms"
    bedrooms_meta.hass_config = bedrooms_entry
    bedrooms_meta.state = AreaState.AREA_STATE_CLEAR
    bedrooms_meta.config = {
        CONF_NAME: META_AREA_BEDROOMS,
        CONF_ID: "bedrooms",
        CONF_TYPE: AREA_TYPE_META,
    }
    bedrooms_meta.entities = {}
    bedrooms_meta.initialized = True

    hass.data[MODULE_DATA] = {
        "entry_master_bedroom": {DATA_AREA_OBJECT: master_bedroom},
        "entry_master_ensuite": {DATA_AREA_OBJECT: master_ensuite},
        "entry_kitchen": {DATA_AREA_OBJECT: kitchen},
        "entry_interior_meta": {DATA_AREA_OBJECT: interior_meta},
        bedrooms_entry.entry_id: {DATA_AREA_OBJECT: bedrooms_meta},
    }

    # 1. Verify get_other_magic_area_devices only returns other non-meta Simply Magic Area devices
    other_devices = bedrooms_meta.get_other_magic_area_devices()
    other_slugs = [d["slug"] for d in other_devices]
    assert set(other_slugs) == {"master_bedroom", "master_ensuite", "kitchen"}
    assert "attic" not in other_slugs
    assert "interior" not in other_slugs
    assert "bedrooms" not in other_slugs

    # 2. Open OptionsFlow on the Bedrooms Meta-Area
    result = await hass.config_entries.options.async_init(bedrooms_entry.entry_id)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "area_config"

    # Rejects an area ('attic') that is not a Simply Magic Area device at schema validation
    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={CONF_CHILD_AREAS: ["master_bedroom", "attic"]},
        )

    # Accepts valid Simply Magic Area devices ('master_bedroom', 'master_ensuite')
    good_result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={CONF_CHILD_AREAS: ["master_bedroom", "master_ensuite"]},
    )
    assert good_result["type"] == FlowResultType.FORM
    assert good_result["step_id"] == "select_features"

    final_result = await hass.config_entries.options.async_configure(
        good_result["flow_id"],
        user_input={},
    )
    assert final_result["type"] == FlowResultType.CREATE_ENTRY
    assert final_result["data"][CONF_CHILD_AREAS] == [
        "master_bedroom",
        "master_ensuite",
    ]

    # Verify get_child_areas strictly filters to configured Simply Magic Area devices
    bedrooms_meta.config[CONF_CHILD_AREAS] = [
        "master_bedroom",
        "master_ensuite",
        "attic",
    ]
    assert bedrooms_meta.get_child_areas() == ["master_bedroom", "master_ensuite"]


