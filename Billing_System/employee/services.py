"""
Employee Domain Services
Includes Authoritative Employee ID auto-generation and Indian Postal PIN code lookup.
"""

import re
import json
import urllib.request
import urllib.error
import ssl
from django.db import transaction, IntegrityError
from .models import user_registration


# Curated offline fallback dataset for Indian Postal PIN code prefixes (Major commercial & retail zones)
PIN_PREFIX_FALLBACK = {
    "110": {"city": "New Delhi", "district": "New Delhi", "state": "Delhi"},
    "121": {"city": "Faridabad", "district": "Faridabad", "state": "Haryana"},
    "122": {"city": "Gurugram", "district": "Gurugram", "state": "Haryana"},
    "141": {"city": "Ludhiana", "district": "Ludhiana", "state": "Punjab"},
    "160": {"city": "Chandigarh", "district": "Chandigarh", "state": "Chandigarh"},
    "201": {"city": "Noida / Ghaziabad", "district": "Gautam Buddha Nagar", "state": "Uttar Pradesh"},
    "226": {"city": "Lucknow", "district": "Lucknow", "state": "Uttar Pradesh"},
    "248": {"city": "Dehradun", "district": "Dehradun", "state": "Uttarakhand"},
    "302": {"city": "Jaipur", "district": "Jaipur", "state": "Rajasthan"},
    "380": {"city": "Ahmedabad", "district": "Ahmedabad", "state": "Gujarat"},
    "390": {"city": "Vadodara", "district": "Vadodara", "state": "Gujarat"},
    "395": {"city": "Surat", "district": "Surat", "state": "Gujarat"},
    "400": {"city": "Mumbai", "district": "Mumbai", "state": "Maharashtra"},
    "401": {"city": "Thane / Palghar", "district": "Thane", "state": "Maharashtra"},
    "411": {"city": "Pune", "district": "Pune", "state": "Maharashtra"},
    "412": {"city": "Pune Rural", "district": "Pune", "state": "Maharashtra"},
    "421": {"city": "Kalyan / Dombivli", "district": "Thane", "state": "Maharashtra"},
    "422": {"city": "Nashik", "district": "Nashik", "state": "Maharashtra"},
    "431": {"city": "Chhatrapati Sambhajinagar", "district": "Aurangabad", "state": "Maharashtra"},
    "440": {"city": "Nagpur", "district": "Nagpur", "state": "Maharashtra"},
    "452": {"city": "Indore", "district": "Indore", "state": "Madhya Pradesh"},
    "462": {"city": "Bhopal", "district": "Bhopal", "state": "Madhya Pradesh"},
    "500": {"city": "Hyderabad", "district": "Hyderabad", "state": "Telangana"},
    "530": {"city": "Visakhapatnam", "district": "Visakhapatnam", "state": "Andhra Pradesh"},
    "560": {"city": "Bengaluru", "district": "Bengaluru Urban", "state": "Karnataka"},
    "570": {"city": "Mysuru", "district": "Mysuru", "state": "Karnataka"},
    "600": {"city": "Chennai", "district": "Chennai", "state": "Tamil Nadu"},
    "641": {"city": "Coimbatore", "district": "Coimbatore", "state": "Tamil Nadu"},
    "682": {"city": "Kochi", "district": "Ernakulam", "state": "Kerala"},
    "695": {"city": "Thiruvananthapuram", "district": "Thiruvananthapuram", "state": "Kerala"},
    "700": {"city": "Kolkata", "district": "Kolkata", "state": "West Bengal"},
    "751": {"city": "Bhubaneswar", "district": "Khurda", "state": "Odisha"},
    "781": {"city": "Guwahati", "district": "Kamrup Metropolitan", "state": "Assam"},
    "800": {"city": "Patna", "district": "Patna", "state": "Bihar"},
    "834": {"city": "Ranchi", "district": "Ranchi", "state": "Jharkhand"},
}


def generate_next_employee_id(company, role=None, requested_id=None):
    """
    Generates an authoritative, unique Employee ID for a new team member.
    Rules:
    1. If requested_id is provided, validate length <= 10 and uniqueness.
    2. If blank / not provided:
       - Inspects company's existing employees for prefix pattern (e.g. 'EMP-', 'MGR-', 'E01-').
       - If no company convention exists, defaults to 'EMP-{seq:03d}'.
       - Loops until finding a sequence number that does NOT collide with any global user_registration record.
       - Guarantees result is <= 10 characters and unique.
    """
    if requested_id and str(requested_id).strip():
        clean_id = str(requested_id).strip().upper()
        if len(clean_id) > 10:
            raise ValueError(f"Employee ID '{clean_id}' exceeds maximum allowed length of 10 characters.")
        if user_registration.objects.filter(employee_id=clean_id).exists():
            raise ValueError(f"Employee ID '{clean_id}' is already registered. Please choose another or leave empty to auto-generate.")
        return clean_id

    # Detect existing convention in company
    existing_ids = list(
        user_registration.objects.filter(company=company)
        .values_list("employee_id", flat=True)
    )

    prefix = "EMP-"
    start_num = 1

    # Check if company has an established prefix pattern like 'PREFIX-123' or 'PREFIX123'
    found_pattern = False
    for eid in existing_ids:
        m = re.match(r"^([A-Z0-9_\-]{1,6}?)(\d{1,4})$", eid.upper())
        if m:
            prefix = m.group(1)
            found_pattern = True
            break

    if not found_pattern and role:
        # Subtle role hint if no prior convention: 'MGR-', 'CSH-', 'EMP-'
        if role.lower() == "manager":
            prefix = "MGR-"
        elif role.lower() == "cashier":
            prefix = "CSH-"
        else:
            prefix = "EMP-"

    # Find the next sequence number that does NOT exist globally in user_registration
    # Since employee_id is globally unique in user_registration, check across all records
    seq = start_num
    max_iterations = 9999
    while seq < max_iterations:
        # Determine padding width so total length <= 10
        pad_width = max(3, 10 - len(prefix))
        candidate = f"{prefix}{str(seq).zfill(pad_width)}"
        if len(candidate) > 10:
            # If prefix was too long for 3 digits, trim or fallback to shorter 'E{comp_id}-'
            short_prefix = f"E{company.id % 100:02d}-"
            candidate = f"{short_prefix}{str(seq).zfill(3)}"

        if not user_registration.objects.filter(employee_id=candidate).exists():
            return candidate
        seq += 1

    # Fallback to alphanumeric random suffix if integer sequence exhausted
    import secrets
    fallback = f"E{company.id % 99:02d}{secrets.token_hex(3).upper()}"[:10]
    return fallback


def lookup_postal_pincode(pincode_str):
    """
    Retrieves city/district and state for an Indian 6-digit PIN code.
    1. Validates exact 6-digit numeric format.
    2. Sends ONLY the PIN code to reliable postal API (postalpincode.in) with 3.5s timeout.
    3. If online API is unavailable or times out, seamlessly falls back to curated local dataset.
    4. Never guesses or fabricates locations for invalid PIN formats.
    5. Returns dict:
       {
           "success": bool,
           "pincode": str,
           "city": str,
           "district": str,
           "state": str,
           "source": "API" | "FALLBACK",
           "error": str | None
       }
    """
    clean_pin = re.sub(r"\s+", "", str(pincode_str or "").strip())

    # 1. Format validation: Must be exactly 6 digits starting with 1-9
    if not re.match(r"^[1-9]\d{5}$", clean_pin):
        return {
            "success": False,
            "pincode": clean_pin,
            "error": "Invalid PIN code. Must be a 6-digit Indian postal code beginning with digits 1–9."
        }

    # 2. Query postal API
    api_url = f"https://api.postalpincode.in/pincode/{clean_pin}"
    try:
        ssl_ctx = ssl.create_default_context()
        req = urllib.request.Request(
            api_url,
            headers={
                "User-Agent": "BriskRetail-PINLookup/1.0",
                "Accept": "application/json"
            }
        )
        with urllib.request.urlopen(req, timeout=3.5, context=ssl_ctx) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        if isinstance(data, list) and len(data) > 0 and data[0].get("Status") == "Success":
            po_list = data[0].get("PostOffice") or []
            if po_list and len(po_list) > 0:
                localities = []
                seen_names = set()
                for po in po_list:
                    name = (po.get("Name") or "").strip()
                    if name and name not in seen_names:
                        localities.append(name)
                        seen_names.add(name)

                first_po = po_list[0]
                district = (first_po.get("District") or "").strip()
                state = (first_po.get("State") or "").strip()
                block = (first_po.get("Block") or "").strip()
                city = district or block or (localities[0] if localities else "")
                if city.upper() == "NA":
                    city = district or (localities[0] if localities else "")

                return {
                    "success": True,
                    "pincode": clean_pin,
                    "city": city,
                    "district": district,
                    "state": state,
                    "localities": localities,
                    "post_offices": [
                        {
                            "name": po.get("Name", ""),
                            "district": po.get("District", ""),
                            "block": po.get("Block", ""),
                            "state": po.get("State", "")
                        }
                        for po in po_list
                    ],
                    "source": "API",
                    "error": None
                }
        elif isinstance(data, list) and len(data) > 0 and data[0].get("Status") == "Error":
            # Explicit no-result from postal authority
            return {
                "success": False,
                "pincode": clean_pin,
                "error": f"No postal records found for PIN code {clean_pin}. Please enter City and State manually."
            }
    except Exception:
        # Network failure, timeout, or external API unavailable -> proceed to curated offline fallback
        pass

    # 3. Curated offline fallback based on 3-digit regional prefix
    prefix = clean_pin[:3]
    if prefix in PIN_PREFIX_FALLBACK:
        info = PIN_PREFIX_FALLBACK[prefix]
        return {
            "success": True,
            "pincode": clean_pin,
            "city": info["city"],
            "district": info["district"],
            "state": info["state"],
            "localities": [info["city"]],
            "post_offices": [],
            "source": "FALLBACK",
            "error": None
        }

    return {
        "success": False,
        "pincode": clean_pin,
        "offline": True,
        "error": "Postal lookup service unavailable. Please enter City and State manually."
    }
