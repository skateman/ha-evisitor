# ha-evisitor

[![tests](https://github.com/skateman/ha-evisitor/actions/workflows/tests.yml/badge.svg)](https://github.com/skateman/ha-evisitor/actions/workflows/tests.yml)

Async Python client (`pyevisitor`) **and** a Home Assistant custom integration for the Croatian [**e-Visitor**](http://www.evisitor.hr/eVisitorWiki/Javno.Web-API.ashx) tourist registration system.

## What it does

- **Cookie-based authentication** against either the production (`https://www.evisitor.hr/eVisitorRhetos_API/`) or test (`https://www.evisitor.hr/testApi/`) environment.
- **Actions** -- typed payloads for the documented obveznik flow: 
  - `CheckInTourist` (also handles edits when `id` is set; auto-generates a `uuid4` when it isn't, because the eVisitor server requires one).
  - `CheckOutTourist`
  - `CancelTouristCheckIn`
  - `CancelTouristCheckOut`
- **Browses** -- ergonomic wrappers around the documented browses:
  - `list_tourists()` / `list_tourists(extended=True)` (`ListOfTourists` / `ListOfTouristsExtended`)
  - `list_facilities()` (`FacilityBrowse`) + `get_facility_by_code(code)`
  - `list_cancelled_tourists()` (`TouristCancelledBrowse`) + `get_cancelled_tourist_by_id(id)` -- the only public way to look up cancelled check-ins (the wiki doesn't document it)
  - generic `browse(path, ...)` for everything else, with `with_total_count=True` to hit `/RecordsAndTotalCount`.
- **Guests** -- a deduplicated view over `ListOfTouristsExtended` for
  repeat check-ins:
  - `client.guests.stays()` -- raw flat list (one dict per stay)
  - `client.guests.unique()` -- one `Guest` per unique `(name, date_of_birth)` with all stays preserved verbatim; `guest.latest` is the most recent raw record, ready to be reused when checking the same person in again.
- **Lookups (codetables)** scoped to what an obveznik account can read: countries, document types, arrival organisations, TT payment categories, border crossings, offered service types, settlements, facility-tourist-checkin info, accommodation unit types, distances, cash desks, tourist agencies. Admin-only lookups (HTZ/TZ-role) are intentionally excluded.
- **Filter encoding** matching the wiki's literal examples  (`[{"Property":"Code","Operation":"equal","Value":"123456"}]`) and proper `.NET` JSON `Date` round-tripping.
- **Error layer** that decodes `{UserMessage, SystemMessage}` bodies into `EVisitorAuthError`, `EVisitorValidationError`, `EVisitorHTTPError`.

## Server quirks the library papers over

Discovered by probing the live API; you don't have to think about
these:

- eVisitor's IIS server still negotiates a 1024-bit Diffie-Hellman group, which modern OpenSSL rejects with `[SSL: DH_KEY_TOO_SMALL]`. The client sets `DEFAULT@SECLEVEL=1` on its `SSLContext` (gated by `EVisitorConfig.relax_tls=True`, default).
- Rhetos rejects `psize` without `page` and `psize` greater than the total record count. The library doesn't ship default `page`/`psize` for browses and lookups -- they're only sent if you ask.
- The test environment requires an `apikey`; production currently doesn't. `EVisitorConfig` warns instead of raising when the key is missing for test, so callers can attempt and observe the failure.
- `CheckInTourist` rejects new prijave without an `ID` (`[[[Nije zadan ID.]]]`). `CheckInRequest.to_payload()` auto-generates a `uuid4` when `id` is unset and writes it back on the dataclass so you can read it after POST.
- `Environment.parse` strips inline `# comments` from `.env` values.

## Install

Library only (for embedding `pyevisitor` in your own code):

```bash
pip install pyevisitor                 # from PyPI
# or develop against a local checkout:
pip install -e '.[test,dotenv]'
```

For the Home Assistant integration, see the *Install* section under
*Home Assistant integration* below — both HACS and manual paths are
covered there.

## Configure

```bash
cp .env.example .env
# edit .env
```

```ini
EVISITOR_ENVIRONMENT=production    # or "test" (test requires EVISITOR_API_KEY)
EVISITOR_USERNAME=...
EVISITOR_PASSWORD=...
EVISITOR_API_KEY=                  # only used by the test environment
```

How credentials are obtained:

- **Username/password**: opened by your obveznik (or your turistička zajednica) as a *podkorisnički račun* via the eVisitor GUI. The recommended setup is two sub-accounts: one for API, one for the GUI to verify what the API wrote.
- **API key**: vendor-level key, only used on the test environment. No self-service flow -- request from eVisitor support (HTZ) when registering the integration. Production works fine without an apikey.

## Use

### Check-in

```python
import asyncio
from datetime import date, time

from pyevisitor import CheckInRequest, EVisitorClient, EVisitorConfig


async def main() -> None:
    async with EVisitorClient(EVisitorConfig.from_env()) as client:
        facility = await client.browses.get_facility_by_code("0000001")
        assert facility is not None

        request = CheckInRequest(
            # id=...   # omit -> a fresh uuid4 is allocated for you
            facility=facility["Code"],
            stay_from=date(2026, 5, 9),
            foreseen_stay_until=date(2026, 5, 10),
            time_stay_from=time(15, 0),
            time_estimated_stay_until=time(10, 0),
            document_type="027",        # from client.lookups.document_types()
            document_number="XX111111",
            tourist_name="Marek",
            tourist_surname="Novák",
            gender="muški",
            date_of_birth=date(1985, 1, 15),
            citizenship="SVK",
            country_of_birth="SVK",
            city_of_birth="Bratislava",
            country_of_residence="SVK",
            city_of_residence="Trnava",
            residence_address="Hlavná 12",
            arrival_organisation="I",            # from client.lookups.arrival_organisations()
            offered_service_type="Noćenje",      # from client.lookups.offered_service_types()
            tt_payment_category="18",            # from client.lookups.tt_payment_categories()
        )
        await client.actions.check_in_tourist(request)
        print(f"Checked in. ID = {request.id}")


asyncio.run(main())
```

### Edit (extend / shorten an active stay)

```python
# Re-call CheckInTourist with the original ID and the FULL payload
# (per the wiki: send all fields, not just the ones you want to change).
request.id = "<existing-check-in-id>"
request.foreseen_stay_until = date(2026, 5, 11)
await client.actions.check_in_tourist(request)
```

### Check-out / cancel

```python
from pyevisitor import (
    CancelCheckInRequest, CancelCheckOutRequest, CheckOutRequest,
)

await client.actions.check_out_tourist(
    CheckOutRequest(
        id=check_in_id,
        check_out_date=date(2026, 5, 10),
        check_out_time=time(9, 30),
    )
)

await client.actions.cancel_tourist_check_in(
    CancelCheckInRequest(id=check_in_id, reason="Mistake")
)
```

### Repeat check-in (returning guest)

```python
unique = await client.guests.unique()  # deduplicated by name + DOB
for g in unique:
    print(f"{g.name}  dob={g.date_of_birth}  visits={g.visit_count}")

# `g.latest` is the raw ListOfTouristsExtended dict for the most recent
# stay, useful for prefilling a UI before constructing a new
# CheckInRequest. Note: it carries Croatian-localised display strings
# (e.g. Citizenship='Slovačke Republike'), not ISO codes -- the codes
# need to be resolved via client.lookups.* before posting.
```

### Look up a cancelled prijava

```python
row = await client.browses.get_cancelled_tourist_by_id(check_in_id)
# or all of them, with arbitrary filters/pagination/sort:
res = await client.browses.list_cancelled_tourists()
```

There's a runnable example at
[`examples/check_in.py`](examples/check_in.py) that lists facilities
and lookups and prints (but does **not** POST) a sample check-in
payload.

## Tests

```bash
# Unit tests (no network, mocked HTTP via aioresponses)
pytest

# Live e2e tests against whatever EVISITOR_ENVIRONMENT selects.
# All tests in the e2e suite are read-only.
EVISITOR_E2E=1 pytest -m e2e
```

The e2e suite is intentionally read-only -- nothing in it calls
`CheckInTourist` / `CheckOutTourist` / `Cancel*`, so it is safe to run
against production with a normal obveznik account.

## TODOs

- Check-in flow for **guests who aren't HA-registered persons** (visitors, friends, family without a `person.*` entity in your HA).
- A **Lovelace custom card** showing live occupancy with one-click check-in/out + an "ad-hoc guest" button.
- Caching of stable lookups (countries, document types) across HA restarts.

## Home Assistant integration

### Install

The integration lives under `custom_components/evisitor/`. Two install paths:

#### HACS (recommended)

This repository ships an `hacs.json` and is structured for HACS as a **custom integration**. Until the index PR lands, add it as a custom
repository:

1. In HACS → ⋮ → *Custom repositories*.
2. URL: `https://github.com/skateman/ha-evisitor`, type *Integration*.
3. Install **eVisitor** from the HACS list, restart Home Assistant, then add the integration via *Settings → Devices & services → Add integration → eVisitor*.

#### Manual

Copy `custom_components/evisitor/` into your HA `config/custom_components/`,
restart HA, then add the integration via the UI. Home Assistant will
`pip install pyevisitor` from PyPI on first setup automatically.

### Privacy model

The integration is designed to **minimise** the personally-identifiable information stored in Home Assistant.

**Stored** in the config entry (`.storage/core.config_entries`):

- credentials (username, password, optional apikey) and the chosen facility code -- anyone with these can log into eVisitor and pull everything regardless of what else HA stores;
- per mapped HA person: a single ``check_in_id_seed`` -- the GUID of one of the guest's past stays. Opaque without the credentials.

**Never stored** in HA -- always derived on demand from eVisitor and held only in coordinator memory:

- direct identifiers: full name, date of birth, document number, city of birth, city of residence, street address, gender, telephone, email;
- per-person lookup codes: document type code, citizenship ISO code, country-of-birth and country-of-residence ISO codes, payment-category code. These are recovered at check-in time by translating the Croatian-localised labels on the most recent past stay against the integration's cached eVisitor lookups.

Two values that the past-stay browse data does not carry -- ``arrival_organisation`` and ``offered_service_type`` -- default to ``I`` (Osobno) and ``Noćenje``, the most common values for household-member check-ins.

The **TT payment category** (boravišna pristojba category — controls whether tourist tax is owed for the stay) is recovered from the past
stay's ``Note`` field via the lookup table. If the note doesn't parse or is missing, the integration falls back to **code 18** — ``Vlasnici kuće za odmor i članovi njegove obitelji`` ("owners of the holiday house and members of their family"). This is the right default for the integration's intended use case: HA ``person.*`` entities track household members, who in this scenario are the owner's family. Friends of the owner (code 16) and paying tourists (code 14) are deliberately *not* the fallback, because the integration has no way to recognise them automatically — those check-ins should go through other channels (the eVisitor portal, or future ad-hoc-guest support).

Restarting HA wipes the in-memory cache; the next coordinator update re-fetches it. Calendar event titles render the live name from this in-memory snapshot, plus a persistent on-disk archive of past check-outs (see *Calendar archive* below) -- the archive stores only the four fields the calendar UI shows (summary, start, end, location), never DOB, document, address, citizenship, telephone, e-mail.

The persisted seed is **auto-refreshed after every successful check-in** so the mapping survives eVisitor archiving older prijave (seed = whichever past-stay ID the integration most recently created for that person). The integration's update listener recognises a seed-only delta and skips the entry reload — adding or removing a person mapping still reloads (entities need (de)registration).

**Out-of-band cancellation recovery.** If the seeded prijava is manually cancelled in the eVisitor web UI, the next check-in falls back to `TouristCancelledBrowse`, matches by
`(SurnameAndName, DatePlaceOfBirth)` against the current `unique()` snapshot, recovers the guest and self-heals the seed to a still-valid stay ID. If the seed is also gone from the cancelled browse (full archival), the integration fails loud with a clear "please re-map" message via `evisitor_check_in_failed`.

### Config flow

1. **Credentials** — environment, username, password, optional apikey.
2. **Facility** — pick from the list returned by `FacilityBrowse`.

### Options flow (manage person mappings + per-entry settings)

A menu under "Configure":

- *Integration settings* — adjust the per-entry knobs:
  - Coordinator scan interval (minutes, default 5)
  - Default stay duration (hours, default 48)
  - Default check-out time (`HH:MM`, default `10:00`)

  Times are validated `HH:MM` 24-hour. Saving these triggers a normal entry reload to apply the new schedule + scan interval.

- *Add person* — pick an HA `person.*`, then pick the matching past guest from the dedup'd list.
  **One-shot PII display only** — the dropdown shows `<name> · <DOB> · <visits>` so you can identify the right guest, but only the chosen guest's check-in ID is persisted; everything
  else is rebuilt at check-in time from the live data.
- *Remove person* — drop a mapping.
- *Done*.

### Services

| Service | What it does |
|---------|--------------|
| `evisitor.check_in_person` | New `CheckInTourist` for the mapped person, prefilled from `Guest.latest`. Auto-allocates a `uuid4` for the prijava ID. |
| `evisitor.check_out_person` | `CheckOutTourist` against the active prijava resolved from coordinator state. |
| `evisitor.cancel_check_in` | `CancelTouristCheckIn` (poništavanje). |
| `evisitor.extend_stay` | Re-`CheckInTourist` with same ID and a new `ForeseenStayUntil` (the "edit" path). Accepts either `foreseen_stay_until` (datetime) or `stay_days` (int — today + N days at the integration's default check-out time). |

Each service fires a corresponding `evisitor_*_succeeded` / `evisitor_*_failed` event so user automations can react / notify.

### Entities

- `binary_sensor.<person>_checked_in` (one per mapped person) — true while there's an active prijava for them.
- `sensor.<facility>_active_guests` — count of currently checked-in guests at the configured facility.
- `calendar.<facility>_guests` — events derived from a union of (a) the in-memory active+historical-prijave snapshot returned by the API and (b) a persistent on-disk archive of past check-outs. Only the four calendar-event fields (summary, start, end, location) hit `.storage`.

### Calendar archive

The integration keeps a small local archive of every checked-out prijava it has ever seen at `<config>/.storage/evisitor_archive_<entry_id>`. The archive:

- is **populated implicitly** on the first poll after installing / upgrading -- every stay currently flagged ``CheckedOutTourist=True`` in eVisitor lands on disk in one batch (so existing history is preserved without a manual step);
- grows by **one entry per check-out** on subsequent polls (and is also kept in sync inside the same poll that ``evisitor.check_out_person`` triggers via the coordinator's post-call refresh -- the user never sees an archive lag);
- **drops entries** for any uid that turns up in ``TouristCancelledBrowse`` (so the rare case of a checked-out prijava being cancelled afterwards doesn't leave a void event behind);
- stores **only the four calendar-event fields** (summary, start, end, location). DOB, document number, address, citizenship, telephone, e-mail are *never* written to disk;
- survives Home Assistant restarts and config-entry reloads -- so the calendar can show last month's guests even if eVisitor stops returning them server-side.

Two integration-wide services manage the archive:

| Service | What it does |
|---------|--------------|
| `evisitor.purge_calendar_archive` | Wipe every persisted event. The live tier is untouched; the next coordinator poll re-archives any checked-out stays still returned by eVisitor. Useful as an escape hatch. |
| `evisitor.rebuild_calendar_archive` | Purge + immediately refresh. The fresh snapshot's checked-out stays repopulate the archive in one step. Useful after restoring a backup. |

Both accept an optional ``config_entry_id`` to scope the action to a single entry; omit it to operate on every loaded entry.

### The integration never writes to eVisitor on its own

Every POST to eVisitor (check-in, check-out, cancel, extend) happens ÷*only** in response to an explicit `evisitor.*` service call. The coordinator's periodic refresh is read-only (browses + lookups only). Anything that should "happen automatically" — auto check-in on arrival, auto check-out after a grace period, nightly sliding extension of an open-ended stay — is wired up by you, the user, via Home Assistant automations. To make that easy, the integration ships three **blueprints** covering the standard patterns; you import them once and click *Create automation* to instantiate them.

### Shipped automation blueprints

Three automation blueprints ship with the integration. They live under `custom_components/evisitor/blueprints/automation/evisitor/` (so they travel together with the integration when you copy / pull updates), and each one declares a public `source_url:` pointing at its raw GitHub URL so end users can import them with one click via Home Assistant's standard *Import blueprint* workflow.

#### Importing into Home Assistant

1. Open *Settings → Automations & Scenes → Blueprints*.
2. Click **Import blueprint** in the bottom-right corner.
3. Paste the raw GitHub URL of the blueprint you want, then click *Preview blueprint* → *Import blueprint*. The YAML is downloaded, validated, and saved to `<config>/blueprints/automation/evisitor/`. 
4. Click **Create automation** on the imported blueprint, fill in the inputs, save.

The three URLs:

```
https://raw.githubusercontent.com/skateman/ha-evisitor/main/custom_components/evisitor/blueprints/automation/evisitor/auto_check_in.yaml
https://raw.githubusercontent.com/skateman/ha-evisitor/main/custom_components/evisitor/blueprints/automation/evisitor/auto_check_out.yaml
https://raw.githubusercontent.com/skateman/ha-evisitor/main/custom_components/evisitor/blueprints/automation/evisitor/nightly_sliding_extender.yaml
```

If you keep your config in git, you can also drop the YAML files manually into `<config>/blueprints/automation/evisitor/` and skip the import dialog.

#### What each blueprint does

**Each blueprint accepts one or many `person.*` entities** — the trigger fires per person and the action targets `{{ trigger.entity_id }}`, so a single automation can cover the whole household. Persons that aren't mapped in the integration's options trigger an `evisitor_<op>_failed` event with a clear "no mapping" message.

| Blueprint | Inputs |
|---|---|
| `auto_check_in.yaml` | `person` (one or many), `mode` (`silent` / `notify` / `confirm`, default `confirm`), `presence_debounce_minutes` (default 1), `stay_days` (default 0 = use integration's 48 h default), `check_out_time` (default `10:00`, applied when `stay_days ≥ 1`), `platform` (`companion` / `telegram`, default `companion`, used for `notify` + `confirm`), `notify_service` (Companion only), `telegram_chat_id` (Telegram only), `notification_title`, `notification_message` (use `{name}` placeholder), `check_in_action_label`, `skip_action_label`, `notification_timeout_minutes` (default 60, `confirm` mode only). See the *Modes and platforms* subsection below for what each mode does. |
| `auto_check_out.yaml` | `person` (one or many), `mode` (`silent` / `notify` / `confirm`, default `confirm`), `grace_minutes` (default 60, range 5–1440), `only_during_window` (bool, default false), `window_start` / `window_end` (`HH:MM`, defaults `08:00`/`23:00`) — guards against nighttime presence blips triggering a real check-out, `platform` (`companion` / `telegram`, default `companion`, used for `notify` + `confirm`), `notify_service` (Companion only), `telegram_chat_id` (Telegram only), `notification_title`, `notification_message` (use `{name}` placeholder), `check_out_action_label`, `keep_in_action_label`, `notification_timeout_minutes` (default 60, `confirm` mode only). Same three-mode design as `auto_check_in.yaml` — see the *Modes and platforms* subsection below. |
| `nightly_sliding_extender.yaml` | `person` (one or many), `schedule` (time, default `00:05:00`), `stay_days` (default 2 — today + N days at the integration's default check-out time), `only_if_home` (bool, default true). Replaces the old built-in extender; recommended for any open-ended stay so the prijava never expires while the guest is home. |

All three run in `mode: parallel` so concurrent state changes from multiple persons don't block each other.

#### Modes and platforms (auto_check_in + auto_check_out)

The two presence-triggered blueprints share an identical three-mode UX via their `mode` input:

| mode | What happens on the trigger |
|---|---|
| **silent** | The mapped person is checked in (or out) immediately. No notification. |
| **notify** | The mapped person is checked in (or out) immediately, **then** a passive notification fires telling you it happened. |
| **confirm** (default) | A notification with two buttons fires first (Check in / Skip on arrival; Check out / Keep them on departure). The action only fires on the affirmative button; on the negative button or on timeout (after `notification_timeout_minutes`) nothing is registered. |

For `notify` and `confirm`, two notification platforms are supported via the `platform` input:

- **HA Companion app** (default) — push notification via your `notify.mobile_app_*` service; buttons render natively on the lockscreen.
- **Telegram** — message via `telegram_bot.send_message` with an inline keyboard; buttons render inside the Telegram chat. To use Telegram:
  1. Configure HA's [Telegram bot integration](https://www.home-assistant.io/integrations/telegram_bot/) (create a bot via @BotFather, register its token in HA, whitelist your chat IDs).
  2. Get your numeric chat ID from @userinfobot on Telegram. For group chats the ID is negative.
  3. In the blueprint's *Create automation* form, set `Notification platform` → `Telegram bot`, `Telegram chat ID` → the numeric ID, and leave `Companion notify service` blank.

Both blueprints always pass the actual presence-transition timestamp to the integration's service (`stay_from = trigger.to_state.last_changed` on the way in, `check_out_at = trigger.from_state.last_changed` on the way out), so the registered prijava's `StayFrom` / check-out time matches reality — not the debounced / grace-period trigger time, not when the user tapped a button.

### Tests

The test suite has three layers:

```bash
# Library unit tests + integration's pure-helper unit tests + HA-runtime
# integration tests (config flow, services, entities). Mocks all I/O.
pytest

# Live e2e tests for the library against the real eVisitor server.
# Read-only; safe against production. Run separately because the
# pHACC plugin (used by tests/ha) globally blocks sockets and DNS,
# so we disable it for this run.
EVISITOR_E2E=1 pytest -p no:homeassistant -m e2e
```

## Reference

- API overview:
  <http://www.evisitor.hr/eVisitorWiki/Javno.Web-API.ashx>
- Codetable resources:
  <https://www.evisitor.hr/eVisitorWiki/Javno.Web-API-lista-sifrarnika.ashx>
- Test-env access guide (linked from the API page):
  *Testna okolina - pristupni podaci.docx*.

## Licence

MIT — see [`LICENSE`](LICENSE) for the full text. Permissive: use, modify, fork, sell, sublicense, all fine; no warranty.
