"""Base entity."""

from __future__ import annotations

from typing import Any

from homeassistant.const import EntityCategory
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, PictureSetting
from .coordinator import XgimiCoordinator


class XgimiEntity(CoordinatorEntity[XgimiCoordinator]):
    """Common device info."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: XgimiCoordinator, unique_suffix: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.unique_id}_{unique_suffix}"
        props = coordinator.data.state.props if coordinator.data else {}
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.unique_id or entry.entry_id)},
            manufacturer=MANUFACTURER,
            model="H20",
            model_id=props.get("ro.product.model"),
            name="XGIMI H20",
            serial_number=props.get("ro.serialno"),
            sw_version=props.get("ro.build.display.id"),
            configuration_url=None,
        )

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data.state.available


class XgimiSettingEntity(XgimiEntity):
    """Entity bound to one picture setting."""

    def __init__(self, coordinator: XgimiCoordinator, setting: PictureSetting) -> None:
        super().__init__(coordinator, setting.key)
        self.setting = setting
        self._attr_translation_key = setting.translation_key
        if setting.icon:
            self._attr_icon = setting.icon
        self._attr_entity_registry_enabled_default = setting.enabled_default
        if setting.entity_category == "config":
            self._attr_entity_category = EntityCategory.CONFIG

    @property
    def available(self) -> bool:
        data = self.coordinator.data
        return super().available and data.state.awake

    @property
    def raw_value(self) -> Any:
        return self.coordinator.data.values.get(self.setting.key)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        source = self.coordinator.data.sources.get(self.setting.key)
        attrs: dict[str, Any] = {"source": source, "menu_key": self.setting.key}
        if source == "live":
            attrs["persistent"] = False
        return attrs

    async def _async_write(self, value: Any) -> None:
        await self.coordinator.async_set_values({self.setting.key: value})
