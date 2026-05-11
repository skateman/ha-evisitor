"""Minimal example: log in, list 5 facilities, run a check-in dry shape.

Reads credentials from ``.env`` (see ``.env.example``). Run from the repo root::

    python examples/check_in.py
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, time

from dotenv import load_dotenv

from pyevisitor import (
    CheckInRequest,
    EVisitorClient,
    EVisitorConfig,
)

logging.basicConfig(level=logging.INFO)


async def main() -> None:
    load_dotenv()
    config = EVisitorConfig.from_env()
    print(f"Using environment: {config.environment.value}")

    async with EVisitorClient(config) as client:
        # 1. List the obveznik's facilities so we know which Code to use.
        facilities = await client.browses.list_facilities()
        records = (facilities or {}).get("Records") or []
        print(f"Found {len(records)} facility records")
        for f in records[:5]:
            print(f"  {f.get('Code')!s:<10} {f.get('Name')}")

        # 2. Sample of the most useful lookups (handy for an HA config flow).
        countries = await client.lookups.countries()
        doc_types = await client.lookups.document_types()
        arrivals = await client.lookups.arrival_organisations()
        services = await client.lookups.offered_service_types()
        print(f"Countries: {len(countries)} (HRV in list: "
              f"{any(c['CodeThreeLetters'] == 'HRV' for c in countries)})")
        print(f"Document types: {len(doc_types)}, e.g. "
              f"{[(d['Code'], d['Name']) for d in doc_types[:3]]}")
        print(f"Arrival organisations: {[(a['CodeMI'], a['Name']) for a in arrivals]}")
        print(f"Offered services: {[s['Name'] for s in services]}")

        # 3. Repeat-check-in helper: deduplicate previous guests.
        unique = await client.guests.unique()
        print(f"\nUnique past guests: {len(unique)}")
        for g in unique[:5]:
            print(f"  {g.name:30} dob={g.date_of_birth}  visits={g.visit_count}")

        # 4. Show the payload a CheckInTourist call WOULD send for guest #1
        # (do NOT actually post). Replace the lookup codes with values that
        # match your facility/guest before pointing this at production.
        if records and unique:
            facility_code = str(records[0].get("Code"))
            guest = unique[0]
            request = CheckInRequest(
                # id intentionally unset -> a uuid4 is auto-generated.
                facility=facility_code,
                stay_from=date.today(),
                foreseen_stay_until=date.today(),
                time_stay_from=time(15, 0),
                time_estimated_stay_until=time(10, 0),
                document_type=os.environ.get("EVISITOR_DEMO_DOCTYPE",
                                             doc_types[0]["Code"]),
                document_number="000000000",
                tourist_name=guest.name.split()[-1],
                tourist_surname=" ".join(guest.name.split()[:-1]) or guest.name,
                gender="muški",
                date_of_birth=guest.date_of_birth or date(1990, 1, 1),
                citizenship="HRV",
                country_of_birth="HRV",
                country_of_residence="HRV",
                arrival_organisation=arrivals[0]["CodeMI"],
                offered_service_type=services[0]["Name"],
                tt_payment_category="14",
            )
            payload = request.to_payload()
            print("\nCheckInTourist payload would be:")
            for k, v in payload.items():
                print(f"  {k}: {v}")


if __name__ == "__main__":
    asyncio.run(main())
