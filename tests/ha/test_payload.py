"""Unit tests for the pure helpers in ``_payload``.

These are imported directly without going through Home Assistant, so we
avoid pulling the HA runtime into the regular ``pytest`` invocation.
The tests stub the one HA import the module needs (``homeassistant.util.dt``).
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import date, datetime
from pathlib import Path

# Import _payload.py directly from its file path. It has no HA imports
# (by design), so this works without pulling Home Assistant into pytest.
_PAYLOAD_PATH = (
    Path(__file__).resolve().parents[2]
    / "custom_components"
    / "evisitor"
    / "_payload.py"
)
_spec = importlib.util.spec_from_file_location("evisitor_payload_under_test", _PAYLOAD_PATH)
assert _spec is not None and _spec.loader is not None
_payload = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _payload
_spec.loader.exec_module(_payload)

PersonOptions = _payload.PersonOptions
StayWindow = _payload.StayWindow
build_check_in_request = _payload.build_check_in_request
_split_surname_and_name = _payload._split_surname_and_name
_split_document_number = _payload._split_document_number
_city_of_birth = _payload._city_of_birth
_split_address = _payload._split_address
_normalize_gender = _payload._normalize_gender


def _guest(latest: dict, dob: date | None = date(1985, 1, 15)):
    """Build a tiny pyevisitor.Guest stub with just `.latest` and `.date_of_birth`."""
    from pyevisitor import Guest

    return Guest(
        name=latest.get("SurnameAndName", "X"),
        date_of_birth=dob,
        stays=(latest,),
    )


# --- _country_code_by_citizenship --------------------------------------------


def test_country_code_by_citizenship_matches_genitive_form() -> None:
    countries = [
        {"CodeThreeLetters": "SVK", "NameCitizenships": "Slovačke Republike"},
        {"CodeThreeLetters": "DEU", "NameCitizenships": "Njemačke"},
    ]
    assert _payload._country_code_by_citizenship("Slovačke Republike", countries) == "SVK"
    assert _payload._country_code_by_citizenship("Njemačke", countries) == "DEU"
    assert _payload._country_code_by_citizenship("Unknown", countries) is None
    assert _payload._country_code_by_citizenship(None, countries) is None


def test_country_code_by_national_matches_nominative_form() -> None:
    countries = [
        {"CodeThreeLetters": "SVK", "NameNational": "Slovačka Republika"},
        {"CodeThreeLetters": "HUN", "NameNational": "Mađarska"},
    ]
    assert _payload._country_code_by_national("Slovačka Republika", countries) == "SVK"
    assert _payload._country_code_by_national("Mađarska", countries) == "HUN"


def test_country_label_at_end_handles_two_word_country() -> None:
    assert (
        _payload._country_label_at_end(
            "15.01.1985 (40) Bratislava Slovačka Republika"
        )
        == "Slovačka Republika"
    )


def test_country_label_at_end_handles_one_word_country() -> None:
    assert (
        _payload._country_label_at_end("30.10.1997 (28) Bratislava Mađarska")
        == "Mađarska"
    )


def test_document_type_code_picks_longest_matching_prefix() -> None:
    doc_types = [
        {"Code": "027", "Name": "Osobna iskaznica (strana)"},
        {"Code": "028", "Name": "Osobna iskaznica"},  # generic prefix
    ]
    assert (
        _payload._document_type_code(
            "Osobna iskaznica (strana) XX111111", doc_types
        )
        == "027"
    )


def test_payment_category_code_matches_note() -> None:
    cats = [
        {"Code": "16", "Name": "Prijatelji i ostale osobe vlasnika kuće ili stana za odmor"},
        {"Code": "18", "Name": "Vlasnici kuće za odmor i članovi njegove obitelji"},
    ]
    assert (
        _payload._payment_category_code(
            "Vlasnici kuće za odmor i članovi njegove obitelji", cats
        )
        == "18"
    )
    assert _payload._payment_category_code(None, cats) is None


# --- _split_surname_and_name --------------------------------------------------


def test_split_surname_and_name_simple() -> None:
    assert _split_surname_and_name("Novák Marek") == ("Novák", "Marek")


def test_split_surname_and_name_multi_token() -> None:
    assert _split_surname_and_name("Vasík (László) János") == (
        "Vasík",
        "(László) János",
    )


def test_split_surname_and_name_single_word() -> None:
    assert _split_surname_and_name("Madonna") == ("Madonna", "Madonna")


def test_split_surname_and_name_empty() -> None:
    assert _split_surname_and_name(None) == ("", "")
    assert _split_surname_and_name("") == ("", "")


# --- _split_document_number ---------------------------------------------------


def test_split_document_number_typical() -> None:
    assert _split_document_number("Osobna iskaznica (strana) XX111111") == "XX111111"


def test_split_document_number_handles_missing_space() -> None:
    assert _split_document_number("OnlyOneToken") is None


def test_split_document_number_none() -> None:
    assert _split_document_number(None) is None


# --- _city_of_birth -----------------------------------------------------------


def test_city_of_birth_with_city() -> None:
    assert (
        _city_of_birth("15.01.1985 (40) Bratislava Slovačka Republika")
        == "Bratislava"
    )


def test_city_of_birth_country_only() -> None:
    # Two-token country, nothing else -> no city.
    assert (
        _city_of_birth("15.01.1985 (40)  Slovačka Republika") is None
    )


def test_city_of_birth_handles_missing() -> None:
    assert _city_of_birth(None) is None
    assert _city_of_birth("") is None


# --- _split_address -----------------------------------------------------------


def test_split_address_typical() -> None:
    parts = _split_address("Hlavná 12 Trnava Slovačka Republika")
    assert parts.street == "Hlavná 12"
    assert parts.city == "Trnava"


def test_split_address_short_string_returns_none_parts() -> None:
    parts = _split_address("Foo")
    assert parts.street is None and parts.city is None


def test_split_address_none() -> None:
    parts = _split_address(None)
    assert parts.street is None and parts.city is None


# --- _normalize_gender --------------------------------------------------------


def test_normalize_gender_known_variants() -> None:
    assert _normalize_gender("Muški") == "muški"
    assert _normalize_gender("Ženski") == "ženski"
    assert _normalize_gender("muski") == "muški"
    assert _normalize_gender(None) == "muški"


# --- StayWindow ---------------------------------------------------------------


def test_stay_window_default_uses_now_plus_48h_at_10am() -> None:
    fixed = datetime(2026, 5, 8, 12, 30, 0)
    window = StayWindow.default_from_now(now=fixed)
    assert window.stay_from == fixed
    # 48h later → 2026-05-10 at 10:00
    assert window.foreseen_stay_until == datetime(2026, 5, 10, 10, 0, 0)


# --- build_check_in_request ---------------------------------------------------


_LATEST = {
    "ID": "stay-1",
    "SurnameAndName": "Novák Marek",
    "DatePlaceOfBirth": "15.01.1985 (40) Bratislava Slovačka Republika",
    "Gender": "Muški",
    "Citizenship": "Slovačke Republike",
    "TravelDocumentTypeNumber": "Osobna iskaznica (strana) XX111111",
    "Address": "Hlavná 12 Trnava Slovačka Republika",
    "Note": "Vlasnici kuće za odmor i članovi njegove obitelji",
}


_LOOKUPS = {
    "country": [
        {
            "CodeThreeLetters": "SVK",
            "NameNational": "Slovačka Republika",
            "NameCitizenships": "Slovačke Republike",
        },
        {"CodeThreeLetters": "HRV", "NameNational": "Hrvatska", "NameCitizenships": "Hrvatske"},
    ],
    "document_type": [
        {"Code": "027", "Name": "Osobna iskaznica (strana)"},
    ],
    "tt_payment_category": [
        {"Code": "18", "Name": "Vlasnici kuće za odmor i članovi njegove obitelji"},
        {"Code": "14", "Name": "Turist u ugostiteljskom objektu"},
    ],
}


def test_build_check_in_request_resolves_codes_from_live_data() -> None:
    opts = PersonOptions(check_in_id_seed="stay-1")
    window = StayWindow(
        stay_from=datetime(2026, 5, 9, 15, 0, 0),
        foreseen_stay_until=datetime(2026, 5, 11, 10, 0, 0),
    )
    req = build_check_in_request(
        _guest(_LATEST),
        opts,
        facility_code="0000001",
        stay_window=window,
        lookup_cache=_LOOKUPS,
    )
    payload = req.to_payload()

    assert payload["Facility"] == "0000001"
    # Codes resolved from live data:
    assert payload["DocumentType"] == "027"
    assert payload["DocumentNumber"] == "XX111111"
    assert payload["Citizenship"] == "SVK"
    assert payload["CountryOfBirth"] == "SVK"
    assert payload["CountryOfResidence"] == "SVK"
    assert payload["TTPaymentCategory"] == "18"
    # Defaults baked in for fields that the past-stay data does not carry:
    assert payload["ArrivalOrganisation"] == "I"
    assert payload["OfferedServiceType"] == "Noćenje"
    # Direct identifiers parsed from labels:
    assert payload["TouristSurname"] == "Novák"
    assert payload["TouristName"] == "Marek"
    assert payload["Gender"] == "muški"
    assert payload["DateOfBirth"] == "19850115"
    assert payload["CityOfBirth"] == "Bratislava"
    assert payload["CityOfResidence"] == "Trnava"
    assert payload["ResidenceAddress"] == "Hlavná 12"
    # New check-ins get an auto-generated ID.
    assert "ID" in payload


def test_build_check_in_request_falls_back_when_lookups_miss() -> None:
    opts = PersonOptions(check_in_id_seed="stay-1")
    window = StayWindow(
        stay_from=datetime(2026, 5, 9, 15, 0, 0),
        foreseen_stay_until=datetime(2026, 5, 10, 10, 0, 0),
    )
    # Empty lookup cache -> all codes fall through to defaults.
    req = build_check_in_request(
        _guest(_LATEST),
        opts,
        facility_code="0000001",
        stay_window=window,
        lookup_cache={},
    )
    payload = req.to_payload()
    assert payload["DocumentType"] == "027"  # _FALLBACK_DOCUMENT_TYPE
    assert payload["Citizenship"] == "HRV"  # _FALLBACK_COUNTRY
    assert payload["CountryOfBirth"] == "HRV"
    assert payload["CountryOfResidence"] == "HRV"
    assert payload["TTPaymentCategory"] == "18"  # _DEFAULT_TT_PAYMENT_CATEGORY (owner-family)


def test_build_check_in_request_preserves_explicit_id_for_edits() -> None:
    opts = PersonOptions(check_in_id_seed="stay-1")
    window = StayWindow(
        stay_from=datetime(2026, 5, 9, 15, 0, 0),
        foreseen_stay_until=datetime(2026, 5, 10, 10, 0, 0),
    )
    req = build_check_in_request(
        _guest(_LATEST),
        opts,
        facility_code="0000001",
        stay_window=window,
        lookup_cache=_LOOKUPS,
        check_in_id="explicit-id-123",
    )
    assert req.to_payload()["ID"] == "explicit-id-123"
