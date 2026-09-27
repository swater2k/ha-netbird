"""Config- und Options-Flow."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any
from urllib.parse import urlsplit

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_URL, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import voluptuous as vol

from . import build_client
from .api import (
    NetBirdAuthError,
    NetBirdConnectionError,
    NetBirdError,
    NetBirdPermissionError,
    normalize_url,
)
from .const import (
    CONF_AUDIT_EVENTS,
    CONF_SCAN_INTERVAL,
    CONF_TOKEN,
    CONF_TOKEN_WARNING_DAYS,
    CONTROL_OPTIONS,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TOKEN_WARNING_DAYS,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

_TOKEN = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
_URL = TextSelector(TextSelectorConfig(type=TextSelectorType.URL))

# Hassfest verbietet URLs in Übersetzungen – das Beispiel kommt als Platzhalter.
PLACEHOLDERS = {"example_url": "https://netbird.example.com"}


def _schema(defaults: Mapping[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_URL, default=defaults.get(CONF_URL, "")): _URL,
            vol.Required(CONF_TOKEN): _TOKEN,
            vol.Required(CONF_VERIFY_SSL, default=defaults.get(CONF_VERIFY_SSL, True)): (
                BooleanSelector()
            ),
        }
    )


def _normalize(user_input: Mapping[str, Any]) -> dict[str, Any]:
    return {
        CONF_URL: normalize_url(user_input[CONF_URL]),
        CONF_TOKEN: user_input[CONF_TOKEN].strip(),
        CONF_VERIFY_SSL: bool(user_input.get(CONF_VERIFY_SSL, True)),
    }


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, str]:
    """Verbindung und Token testen; leeres Dict bedeutet Erfolg."""
    client = build_client(hass, data)
    try:
        await client.current_user()
        await client.peers()
    except NetBirdAuthError:
        return {"base": "invalid_auth"}
    except NetBirdPermissionError:
        return {"base": "insufficient_permissions"}
    except NetBirdConnectionError:
        return {"base": "cannot_connect"}
    except NetBirdError:
        return {"base": "invalid_response"}
    except Exception:
        _LOGGER.exception("Unerwarteter Fehler bei der Validierung")
        return {"base": "unknown"}
    return {}


class NetBirdConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            data = _normalize(user_input)
            await self.async_set_unique_id(data[CONF_URL])
            self._abort_if_unique_id_configured()
            if not (errors := await validate_input(self.hass, data)):
                host = urlsplit(data[CONF_URL]).hostname or data[CONF_URL]
                return self.async_create_entry(title=f"NetBird ({host})", data=data)
        return self.async_show_form(
            step_id="user",
            data_schema=_schema(user_input or {}),
            errors=errors,
            description_placeholders=PLACEHOLDERS,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**entry.data, CONF_TOKEN: user_input[CONF_TOKEN].strip()}
            if not (errors := await validate_input(self.hass, data)):
                return self.async_update_reload_and_abort(entry, data_updates=data)
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_TOKEN): _TOKEN}),
            description_placeholders={"url": entry.data[CONF_URL]},
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = _normalize(user_input)
            await self.async_set_unique_id(data[CONF_URL])
            self._abort_if_unique_id_mismatch(reason="different_instance")
            if not (errors := await validate_input(self.hass, data)):
                return self.async_update_reload_and_abort(entry, data_updates=data)
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_schema(user_input or dict(entry.data)),
            errors=errors,
            description_placeholders=PLACEHOLDERS,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> NetBirdOptionsFlow:
        return NetBirdOptionsFlow()


class NetBirdOptionsFlow(OptionsFlowWithReload):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            data = {
                CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                CONF_TOKEN_WARNING_DAYS: int(user_input[CONF_TOKEN_WARNING_DAYS]),
                CONF_AUDIT_EVENTS: bool(user_input.get(CONF_AUDIT_EVENTS, True)),
            }
            for option in CONTROL_OPTIONS:
                data[option] = bool(user_input.get(option, False))
            return self.async_create_entry(data=data)

        opts = self.config_entry.options
        fields: dict[Any, Any] = {
            vol.Required(
                CONF_SCAN_INTERVAL, default=opts.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ): NumberSelector(
                NumberSelectorConfig(
                    min=MIN_SCAN_INTERVAL,
                    max=MAX_SCAN_INTERVAL,
                    step=5,
                    unit_of_measurement="s",
                    mode=NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_TOKEN_WARNING_DAYS,
                default=opts.get(CONF_TOKEN_WARNING_DAYS, DEFAULT_TOKEN_WARNING_DAYS),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=1, max=90, step=1, unit_of_measurement="d", mode=NumberSelectorMode.BOX
                )
            ),
            vol.Optional(
                CONF_AUDIT_EVENTS, default=opts.get(CONF_AUDIT_EVENTS, True)
            ): BooleanSelector(),
        }
        for option in CONTROL_OPTIONS:
            fields[vol.Optional(option, default=opts.get(option, False))] = BooleanSelector()
        return self.async_show_form(step_id="init", data_schema=vol.Schema(fields))
