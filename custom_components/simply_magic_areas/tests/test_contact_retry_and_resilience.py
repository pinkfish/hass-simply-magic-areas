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

    # 2. Test manual override holds across EXTENDED -> OCCUPIED until CLEAR, then CLEAR -> OCCUPIED resets it
    light_group._set_controlled_by_this_entity(False)
    light_group._attr_is_on = False
    state_sensor_id = "sensor.simply_magic_areas_state_kitchen"
    hass.states.async_set(state_sensor_id, AreaState.AREA_STATE_EXTENDED)
    await hass.async_block_till_done()

    turn_on_calls = async_mock_service(hass, LIGHT_DOMAIN, SERVICE_TURN_ON)
    hass.states.async_set(state_sensor_id, AreaState.AREA_STATE_OCCUPIED)
    await hass.async_block_till_done()
    assert light_group._is_controlled_by_this_entity() is False
    assert len(turn_on_calls) == 0

    # Once area clears and re-enters OCCUPIED, manual override resets and turns light ON
    hass.states.async_set(state_sensor_id, AreaState.AREA_STATE_CLEAR)
    await hass.async_block_till_done()
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

    # 5. Manual brightness override while ON is respected and prevents profile changes from overwriting brightness
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

    # Profile change BRIGHT -> SLEEP while in manual brightness override is ignored
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
    assert len(turn_on_calls) == 0
    assert len(turn_off_calls) == 0

    # Even direct _turn_off_light(force=True) never turns off a sensorless room's light
    light_group._turn_off_light(force=True)
    await hass.async_block_till_done()
    assert len(turn_off_calls) == 0

    # 6. Turning light OFF externally in a sensorless room resets manual_control so future profile changes work
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

    from homeassistant.const import SERVICE_TURN_OFF

    on_calls = async_mock_service(hass, LIGHT_DOMAIN, SERVICE_TURN_ON)
    off_calls = async_mock_service(hass, LIGHT_DOMAIN, SERVICE_TURN_OFF)

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




