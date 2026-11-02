"""The cards are registered as Lovelace resources exactly once, whoever rewrote the URL.

protocol_wizard issue 82 (Nov 2026), the same construct as here: HA 2026.11's
Marketplace (the successor of HACS) serves dashboard resources from
/local/community/ and rewrites existing /hacsfiles/... entries in place,
keeping their ID.  `async_register_card` compared the full URL with
/hacsfiles/..., no longer found its own entry and added another one — one
duplicate per restart (Spook: lovelace_duplicate_resources; the browser:
"the name ... has already been used").
"""
from __future__ import annotations

import asyncio
import importlib.util as ilu
import pathlib
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

_PKG = pathlib.Path(__file__).parent.parent / "custom_components" / "ha_felicity"


def _load_init():
    for missing in ("homeassistant.components.frontend", "homeassistant.components.http"):
        sys.modules.setdefault(missing, MagicMock())
    spec = ilu.spec_from_file_location("custom_components.ha_felicity._lovelace_init",
                                       _PKG / "__init__.py")
    module = ilu.module_from_spec(spec)
    module.__package__ = "custom_components.ha_felicity"
    spec.loader.exec_module(module)
    return module


INIT = _load_init()

CARD = "/local/community/ha_felicity/ha_felicity.js"
EMS = "/local/community/ha_felicity/ha_felicity_ems.js"


class FakeResources:
    """A storage-mode Lovelace resource collection."""

    loaded = True

    def __init__(self, urls=()):
        self._items = {}
        self._next = 0
        for url in urls:
            self._add(url)

    def _add(self, url):
        self._next += 1
        item_id = f"id{self._next}"
        self._items[item_id] = {"id": item_id, "url": url, "type": "module"}
        return item_id

    def async_items(self):
        return list(self._items.values())

    async def async_create_item(self, data):
        self._add(data["url"])

    async def async_delete_item(self, item_id):
        del self._items[item_id]

    def marketplace_rewrite(self):
        """What the 2026.11 Marketplace does at start: same ID, new prefix."""
        for item in self._items.values():
            item["url"] = item["url"].replace("/hacsfiles/", "/local/community/")

    def urls(self):
        return sorted(item["url"] for item in self._items.values())


def _register(resources):
    hass = SimpleNamespace(data={"lovelace": SimpleNamespace(resources=resources)})
    asyncio.run(INIT.async_register_card(hass, MagicMock()))


def test_a_fresh_install_registers_each_card_once_under_local_community():
    resources = FakeResources()
    _register(resources)
    _register(resources)
    assert resources.urls() == sorted([CARD, EMS])


def test_restarts_after_a_marketplace_rewrite_add_no_duplicates():
    resources = FakeResources([
        "/hacsfiles/ha_felicity/ha_felicity.js",
        "/hacsfiles/ha_felicity/ha_felicity_ems.js",
    ])
    for _ in range(3):  # three restarts
        resources.marketplace_rewrite()
        _register(resources)
    assert resources.urls() == sorted([CARD, EMS])


def test_an_existing_hacsfiles_entry_is_left_as_it_is():
    """HACS still installed: its own URL keeps working, nothing is added."""
    resources = FakeResources(["/hacsfiles/ha_felicity/ha_felicity.js?hacstag=123",
                               "/hacsfiles/ha_felicity/ha_felicity_ems.js"])
    _register(resources)
    assert len(resources.urls()) == 2


def test_duplicates_left_by_earlier_versions_are_removed_keeping_the_first():
    resources = FakeResources([
        CARD,  # the entry that kept its ID through the rewrite
        CARD,
        "/hacsfiles/ha_felicity/ha_felicity.js",
        EMS,
        "/hacsfiles/ha_felicity/ha_felicity_ems.js",
    ])
    _register(resources)
    assert resources.urls() == sorted([CARD, EMS])
    assert [i["id"] for i in resources.async_items()] == ["id1", "id4"]


def test_the_two_cards_are_told_apart():
    """ha_felicity.js must not match ha_felicity_ems.js, nor the other way round."""
    assert INIT._is_card_resource(CARD, "ha_felicity.js")
    assert not INIT._is_card_resource(EMS, "ha_felicity.js")
    assert INIT._is_card_resource(EMS, "ha_felicity_ems.js")
    assert not INIT._is_card_resource(CARD, "ha_felicity_ems.js")


def test_unrelated_resources_are_never_touched():
    other = "/local/community/other_card/other_card.js"
    resources = FakeResources([other, other])
    _register(resources)
    assert resources.urls() == sorted([other, other, CARD, EMS])


def test_yaml_mode_writes_nothing():
    class YamlResources:
        loaded = True

        def async_items(self):
            return []

    _register(YamlResources())  # no create method: must not raise
