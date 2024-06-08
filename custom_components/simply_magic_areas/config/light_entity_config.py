"""Config setup for the light entities."""

from dataclasses import dataclass

import voluptuous as vol

from homeassistant.const import STATE_ON
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.selector import EntitySelectorConfig, selector

from .area_state import AreaState
from .nullable_entity_selector import NullableEntitySelector


# Light group setup options
@dataclass
class LightEntityConf:
    """LightEntityConf configures how the light setups are used in the group."""

    name: str
    default_dim_level: float
    enable_state: AreaState
    icon: str
    has_entity: bool
    is_advanced: bool

    def entity_name(self) -> str:
        """Return the name of the entity to lookup."""
        return self.name + "_entity"

    def state_dim_level(self) -> str:
        """Return the name in the state to lookup the dim level."""
        return self.name + "_state_dim"

    def advanced_lights_to_control(self) -> str:
        """Return the name of the state to track the entities to use."""
        return self.name + "_lights"

    def advanced_state_check(self) -> str:
        """Return the advanced state check for the setup, defaults to STATE_ON."""
        return self.name + "_state_check"

    def number_selector(self) -> dict[str, any]:
        """Return a number selector to use for the dim percentage."""
        return {
            "number": {
                # "initial": self.default_dim_level,
                "min": 0,
                "max": 100,
                "mode": "box",
                "unit_of_measurement": "percent",
            }
        }

    def config_flow_schema(self) -> dict:
        """Return the options for the schema for the simply magic areas."""
        if self.is_advanced:
            return {}
        if self.has_entity:
            return {
                vol.Optional(self.entity_name(), default=""): vol.Any(cv.entity_id, ""),
            }
        return {}

    def config_flow_options(self) -> list[tuple[str, any, any]]:
        """Return the options for the schema for the simply magic areas."""
        if self.is_advanced:
            return []
        if self.has_entity:
            return [
                (self.entity_name(), "", cv.entity_id),
            ]
        return []

    def config_flow_dynamic_validators(self, all_entities: list[str]) -> dict:
        """Return the dynamic validators to use in the config flow."""
        if self.is_advanced:
            return {}
        if self.has_entity:
            return {
                self.entity_name(): vol.In(["", *all_entities]),
            }
        return {}

    def config_flow_selectors(self, all_entities: list[str]) -> dict:
        """Return the config for the main section with the timeouts and name bits."""
        if self.is_advanced:
            return {}
        if self.has_entity:
            return {
                self.entity_name(): NullableEntitySelector(
                    EntitySelectorConfig(include_entities=all_entities, multiple=False)
                ),
                self.state_dim_level(): selector(self.number_selector()),
            }
        return {
            self.state_dim_level(): selector(self.number_selector()),
        }

    def advanced_config_flow_schema(self) -> vol.Schema:
        """Return the options for the schema for the simply magic areas."""
        ret = {
            vol.Optional(self.advanced_lights_to_control(), default=[]): cv.entity_ids,
            vol.Optional(self.advanced_state_check(), default=STATE_ON): str,
        }
        if self.is_advanced and self.has_entity:
            ret.update(
                {
                    self.entity_name(): vol.Any(cv.entity_id, ""),
                    self.state_dim_level(): vol.Range(min=0, max=100),
                }
            )
        else:
            ret.update(
                {
                    self.state_dim_level(): vol.Range(min=0, max=100),
                }
            )

        return ret

    def advanced_config_flow_options(self) -> list[tuple[str, any, any]]:
        """Return the options for the schema for the simply magic areas."""
        ret = [
            (self.advanced_lights_to_control(), [], cv.entity_ids),
            (self.advanced_state_check(), STATE_ON, str),
        ]
        if self.is_advanced and self.has_entity:
            ret.extend(
                [
                    (self.entity_name(), "", cv.entity_id),
                    (
                        self.state_dim_level(),
                        self.default_dim_level,
                        float,
                    ),
                ]
            )
        else:
            ret.extend(
                [
                    (
                        self.state_dim_level(),
                        self.default_dim_level,
                        float,
                    ),
                ]
            )
        return ret

    def advanced_config_flow_dynamic_validators(
        self, all_lights: list[str], all_entities: list[str]
    ) -> dict:
        """Return the dynamic validators to use in the config flow."""
        ret = {
            self.advanced_lights_to_control(): cv.multi_select(all_lights),
            self.advanced_state_check(): str,
        }
        if self.is_advanced and self.has_entity:
            ret.update(
                {
                    self.entity_name(): vol.In(["", *all_entities]),
                    self.state_dim_level(): vol.Range(min=0, max=100),
                }
            )
        else:
            ret.update(
                {
                    self.state_dim_level(): vol.Range(min=0, max=100),
                }
            )
        return ret

    def advanced_config_flow_selectors(
        self, all_lights: list[str], all_entities: list[str]
    ) -> dict:
        """Return the config for the main section with the timeouts and name bits."""
        ret = {
            self.advanced_lights_to_control(): NullableEntitySelector(
                EntitySelectorConfig(include_entities=all_lights, multiple=True)
            ),
            self.advanced_state_check(): cv.string,
        }
        if self.is_advanced and self.has_entity:
            ret.update(
                {
                    self.entity_name(): NullableEntitySelector(
                        EntitySelectorConfig(
                            include_entities=all_entities, multiple=False
                        )
                    ),
                    self.state_dim_level(): selector(self.number_selector()),
                }
            )
        else:
            ret.update(
                {
                    self.state_dim_level(): selector(self.number_selector()),
                }
            )
        return ret
