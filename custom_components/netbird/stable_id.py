"""Entitäts-IDs ohne Bereichs-Präfix.

Neuere Home-Assistant-Versionen setzen neue Entitäts-IDs standardmäßig aus
Bereich, Gerät und Entität zusammen. Für Dienst-Integrationen ergibt der
Bereich keinen Sinn; die ID soll unabhängig davon immer gleich lauten.

Home Assistant übernimmt eine ID, die die Integration vor dem Hinzufügen
vorschlägt, ohne Bereich oder Gerätenamen davorzusetzen. Der Vorschlag wird
erst beim Hinzufügen gebildet, weil der übersetzte Name vorher nicht bekannt
ist. Bestehende Entitäten behalten ihre ID aus der Registry.
"""

from __future__ import annotations

from homeassistant.helpers.entity import UNDEFINED
from homeassistant.util import slugify


class StableEntityIdMixin:
    """Schlägt ``<domain>.<gerät>_<name>`` als Entitäts-ID vor."""

    _id_prefix: str = ""
    _assigned_entity_id: str | None = None

    @property
    def entity_id(self) -> str | None:  # type: ignore[override]
        if self._assigned_entity_id is not None:
            return self._assigned_entity_id
        platform = getattr(self, "platform", None)
        if platform is None:
            return None
        name = self.name  # type: ignore[attr-defined]
        if name is UNDEFINED or not name:
            return None
        object_id = slugify(f"{self._id_prefix} {name}".strip())
        return f"{platform.domain}.{object_id}" if object_id else None

    @entity_id.setter
    def entity_id(self, value: str | None) -> None:
        self._assigned_entity_id = value
