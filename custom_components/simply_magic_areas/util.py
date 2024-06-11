"""Utility details for the system."""

from collections.abc import Generator, Iterable
import inspect
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.area_registry import AreaEntry
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_registry import async_get as async_get_er
from homeassistant.util import slugify

basestring = (str, bytes)

_LOGGER = logging.getLogger(__name__)


def cleanup_magic_entities(
    hass: HomeAssistant, new_ids: list[Entity], old_ids: list[str]
) -> None:
    """Clean up a list of magic entities."""
    entity_registry = async_get_er(hass)
    new_entity_ids = [e.entity_id for e in new_ids]
    for ent_id in old_ids:
        if ent_id in new_entity_ids:
            continue
        _LOGGER.warning("Deleting old entity %s", ent_id)
        entity_registry.async_remove(ent_id)


def is_entity_list(item: Any) -> bool:
    """If this is an entity list."""
    return isinstance(item, Iterable) and not isinstance(item, basestring)


def flatten_entity_list(input_list: list[Any]) -> Generator[str, Any, Any]:
    """Flatten the entity list."""
    for i in input_list:
        if is_entity_list(i):
            yield from flatten_entity_list(i)
        else:
            yield i


def get_meta_area_object(name: str) -> AreaEntry:
    """Get the meta area object from the entity."""
    area_slug = slugify(name)

    params: dict[str, Any] = {
        "name": name,
        "normalized_name": area_slug,
        "aliases": set(),
        "id": area_slug,
        "picture": None,
        "icon": None,
        "floor_id": None,
        "labels": set(),
    }

    # We have to introspect the AreaEntry constructor
    # to know if a given param is available because usually
    # Home Assistant updates this object with new parameters in
    # the constructor without defaults and breaks this function
    # in particular.

    available_params = {}
    constructor_params = inspect.signature(AreaEntry.__init__).parameters  # type: ignore  # noqa: PGH003

    for k, v in params.items():
        if k in constructor_params:
            available_params[k] = v

    return AreaEntry(**available_params)  # type: ignore  # noqa: PGH003
