"""The basic entities for magic areas."""

from functools import cached_property

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.restore_state import RestoreEntity

from ..const import DOMAIN, MAGIC_DEVICE_ID_PREFIX
from ..util import slugify
from .magic import MagicArea


class MagicEntity(RestoreEntity):
    """MagicEntity is the base entity for use with all the magic classes."""

    _attr_has_entity_name = True

    def __init__(self, area: MagicArea, domain: str, translation_key: str) -> None:
        """Initialize the magic area."""
        # Avoiding using super() due multiple inheritance issues
        RestoreEntity.__init__(self)

        self.area = area
        self._attr_translation_key = slugify(translation_key)
        self._attr_unique_id = f"{MAGIC_DEVICE_ID_PREFIX}{translation_key}_{area.slug}"
        self.entity_id = area.simply_magic_entity_id(domain, translation_key)
        self._attr_translation_placeholders = {"area_name": area.name}
        self._attr_should_poll = False

    @cached_property
    def device_info(self) -> DeviceInfo:
        """Return the device info."""
        return DeviceInfo(
            identifiers={
                # Serial numbers are unique identifiers within a specific domain
                (DOMAIN, f"{MAGIC_DEVICE_ID_PREFIX}{self.area.id}")
            },
            name=self.area.name,
            manufacturer="Simply Magic Areas",
            model="Simply Magic Area",
        )
