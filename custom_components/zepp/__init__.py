"""The Zepp (Amazfit) integration."""
from __future__ import annotations

import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

import homeassistant.helpers.config_validation as cv

from .const import DOMAIN
from .coordinator import ZeppCoordinator
from .device_catalog import async_init_device_catalog
from .history_sync import async_sync_historical_data
from .services import async_register_services

_LOGGER = logging.getLogger(__name__)


HISTORY_SYNC_STORE_VERSION = 1


async def _async_initial_history_sync(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Run the initial 365-day backfill once and persist successful completion."""
    store = Store(hass, HISTORY_SYNC_STORE_VERSION, f"{DOMAIN}_history_sync_{entry.entry_id}")
    stored = await store.async_load()
    if isinstance(stored, dict) and stored.get("initial_365_day_sync_completed") is True:
        hass.data[DOMAIN]["history_sync_status"] = {
            "status": "completed",
            "progress": 100,
            "synced_days": stored.get("synced_days", 0),
            "total_days": 365,
            "heart_rate_points": stored.get("heart_rate_points", 0),
            "last_synced_date": stored.get("last_synced_date"),
            "skipped": True,
        }
        _LOGGER.debug("Initial Zepp 365-day history sync already completed; skipping")
        return

    await async_sync_historical_data(hass, entry.data, days=365)
    status = hass.data.get(DOMAIN, {}).get("history_sync_status", {})
    if status.get("status") == "completed":
        await store.async_save({
            "initial_365_day_sync_completed": True,
            "synced_days": status.get("synced_days", 0),
            "heart_rate_points": status.get("heart_rate_points", 0),
            "last_synced_date": status.get("last_synced_date"),
        })
    else:
        _LOGGER.warning("Initial Zepp history sync did not complete; it will be retried on next startup")

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BUTTON]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Zepp component."""
    await async_register_services(hass)
    await async_init_device_catalog(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Zepp from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    await async_init_device_catalog(hass)

    coordinator = ZeppCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    # Run the 365-day backfill once; successful completion survives HA restarts.
    sync_task = hass.async_create_task(_async_initial_history_sync(hass, entry))

    hass.data[DOMAIN][entry.entry_id] = {
        "data": entry.data,
        "coordinator": coordinator,
        "history_sync_task": sync_task,
    }

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry when options are updated."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    entry_info = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if entry_info:
        sync_task = entry_info.get("history_sync_task")
        if sync_task and not sync_task.done():
            _LOGGER.debug("Cancelling active historical sync task during unload")
            sync_task.cancel()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
