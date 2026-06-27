"""Generate the planted-identifier synthetic benchmark for the PHI head-to-head.

Why this exists
---------------
Indo-VAP cannot score the *leakage* axis: its raw corpus contains essentially no
regex-detectable structured identifiers (1 gov-ID-shaped hit in 1.495M cells), so
BOTH RePORTal and stock Presidio leave 0 residual — degenerate, not a tie
(tmp/manuscript/headtohead/FINDINGS.md, Finding 1). To measure recall (does each
tool remove a planted identifier?) and precision (does it wrongly destroy benign
clinical data?) we need a corpus where the ground truth is KNOWN per cell.

What this builds
----------------
Two jurisdiction arms, each a small CDISC-style clinical study modelled on the
Indo-VAP form anatomy (SUBJID/FID keys, form-prefixed columns, *DAT date cols):

    tmp/manuscript/synthetic/data/Synth-US/datasets/*.xlsx      (US-only PHI)
    tmp/manuscript/synthetic/data/Synth-India/datasets/*.xlsx   (India-specific PHI)

Plus, alongside them:
    ground_truth.jsonl          — one row per planted/benign cell: study, form, row,
                                  column, category, placement, edge_case, is_identifier
    ground_truth_columns.json   — per-column catalog + the category→behaviour matrix
    data/<arm>/_forms_manifest.yaml + _study_privacy.yaml  — so each arm can also be
                                  fed through the real `make study` pipeline

Three placements per category (the fairness lever)
--------------------------------------------------
  named      — header reveals the category (DM_AADHAAR). Header classifiers win here.
  mislabeled — benign header, the cell holds the identifier (LB_RESULT = an Aadhaar).
  freetext   — benign header, identifier embedded in a sentence (AE_NARRATIVE).
A header-only system can only catch `named`; a value scanner must earn the other two.

Edge-case matrix per category
-----------------------------
valid · valid_formatted · invalid(checksum/shape) · placeholder(repeating) ·
null_unk · null_na · null_dot · empty · embedded · prefixed · whitespace_case
(+ date-specific: DMY/MDY/ambiguous/2-digit-year/textual/datetime/future/sentinel;
 + age-specific: <90 benign vs >89 identifier).

All values are SYNTHETIC (fake). The generator self-checks every label against the
PRODUCTION detector (`scripts.security.phi_patterns`) so "valid" really trips the
real ruler and "invalid/placeholder" really evades it — the ground truth is verified,
not asserted.

Run
---
    uv run --all-groups python tmp/manuscript/synthetic/generate_synthetic.py
    uv run --all-groups python tmp/manuscript/synthetic/generate_synthetic.py --selfcheck-only
"""

from __future__ import annotations

import argparse
import json
import random
import string
import sys
from pathlib import Path
from typing import Any

import openpyxl
import yaml

# Production detector — used ONLY to self-validate generated labels (no real PHI).
from scripts.security.phi_patterns import (
    BLOCKING_PATTERNS,
    SUBJECT_ID_PATTERNS,
    IndianPhonePattern,
    VerhoeffPattern,
    _verhoeff_validate,
)

HERE = Path(__file__).resolve().parent
DATA_ROOT = HERE / "data"
SEED = 20260626
N_EDGE_ROWS = 16          # systematic edge-case scenarios (rows 0..15)
N_NORMAL_ROWS = 24        # bulk valid+benign rows -> realistic size + precision denominator

# ---------------------------------------------------------------------------
# Verhoeff check-digit (brute force — obviously correct, runs once per value)
# ---------------------------------------------------------------------------


def _verhoeff_append(body11: str) -> str:
    """Return the 12-digit Aadhaar = body11 + the digit that makes it Verhoeff-valid."""
    for d in range(10):
        cand = body11 + str(d)
        if _verhoeff_validate(cand):
            return cand
    raise AssertionError("no valid Verhoeff check digit (unreachable)")


# ---------------------------------------------------------------------------
# Synthetic value generators (fresh VALID value per call, seeded RNG)
# ---------------------------------------------------------------------------

INDIA_FIRST = ["Rajesh", "Priya", "Amit", "Sunita", "Vijay", "Kavita", "Arjun",
               "Meena", "Sanjay", "Lakshmi", "Ravi", "Anjali", "Deepak", "Pooja"]
INDIA_LAST = ["Kumar", "Sharma", "Patel", "Reddy", "Nair", "Gupta", "Singh",
              "Iyer", "Das", "Verma", "Joshi", "Rao"]
US_FIRST = ["John", "Mary", "Robert", "Jennifer", "Michael", "Linda", "David",
            "Patricia", "James", "Barbara", "William", "Susan"]
US_LAST = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
           "Davis", "Wilson", "Martinez", "Anderson", "Taylor"]
# Surnames that ALSO read as clinical eponyms -> Presidio PERSON false-positive bait.
EPONYM_TRAPS = ["Koch", "Bell", "Graham", "Mantoux", "Pott", "Ghon", "Ranke",
                "Addison", "Cushing", "Hodgkin"]
CLINICAL_TERMS = ["Ziehl-Neelsen stain positive", "Mycobacterium tuberculosis",
                  "acid-fast bacilli seen", "cavitary lesion right upper lobe",
                  "sputum smear grade 2+", "Lowenstein-Jensen culture"]
INDIA_CITY = ["Pune", "Chennai", "Hyderabad", "Vellore", "Mumbai", "Bengaluru"]
US_CITY = ["Boston", "Houston", "Denver", "Seattle", "Atlanta", "Phoenix"]


def _digits(rng: random.Random, n: int) -> str:
    return "".join(rng.choice("0123456789") for _ in range(n))


def _letters(rng: random.Random, n: int) -> str:
    return "".join(rng.choice(string.ascii_uppercase) for _ in range(n))


def gen_aadhaar(rng: random.Random) -> str:
    body = rng.choice("23456789") + _digits(rng, 10)   # first digit 2-9
    return _verhoeff_append(body)


def gen_pan(rng: random.Random) -> str:
    return _letters(rng, 5) + _digits(rng, 4) + _letters(rng, 1)


def gen_voter(rng: random.Random) -> str:
    return _letters(rng, 3) + _digits(rng, 7)


def gen_passport_in(rng: random.Random) -> str:
    return _letters(rng, 1) + _digits(rng, 7)


def gen_dl_in(rng: random.Random) -> str:
    return _letters(rng, 2) + _digits(rng, 2) + _digits(rng, 11)   # MH12 + 11 digits


def gen_ration(rng: random.Random) -> str:
    return f"{_letters(rng, 2)}-{_digits(rng, 2)}-{_digits(rng, 3)}-{_digits(rng, 6)}"


def gen_abha(rng: random.Random) -> str:
    return f"{_digits(rng, 2)}-{_digits(rng, 4)}-{_digits(rng, 4)}-{_digits(rng, 4)}"


def gen_uhid(rng: random.Random) -> str:
    return f"UHID{_digits(rng, 8)}"


def gen_gstin(rng: random.Random) -> str:
    return f"{_digits(rng, 2)}{_letters(rng, 5)}{_digits(rng, 4)}{_letters(rng, 1)}1Z{_digits(rng, 1)}"


def _valid_indian_mobile(rng: random.Random) -> str:
    while True:
        v = rng.choice("6789") + _digits(rng, 9)
        counts = {c: v.count(c) for c in set(v)}
        if len(set(v)) > 1 and max(counts.values()) < 8:
            return v


def gen_mobile_in(rng: random.Random) -> str:
    return _valid_indian_mobile(rng)


def gen_ssn(rng: random.Random) -> str:
    area = rng.randint(1, 899)
    while area in (666,):
        area = rng.randint(1, 899)
    return f"{area:03d}-{rng.randint(1, 99):02d}-{rng.randint(1, 9999):04d}"


def gen_mrn(rng: random.Random) -> str:
    return f"MRN: {_digits(rng, 7)}"


def gen_medicare(rng: random.Random) -> str:  # MBI shape 1EG4-TE5-MK73
    def c() -> str:
        return rng.choice("ACDEFGHJKMNPQRTUVWXY")
    return f"{rng.randint(1,9)}{c()}{c()}{rng.randint(0,9)}-{c()}{c()}{rng.randint(0,9)}-{c()}{c()}{rng.randint(10,99)}"


def gen_insurance(rng: random.Random) -> str:
    return f"{_letters(rng, 3)}{_digits(rng, 9)}"


def gen_dl_us(rng: random.Random) -> str:
    return f"{_letters(rng, 1)}{_digits(rng, 7)}"


def gen_passport_us(rng: random.Random) -> str:
    return _digits(rng, 9)


def gen_credit_card(rng: random.Random) -> str:
    # 15 random digits + Luhn check digit (Presidio CREDIT_CARD validates Luhn).
    body = "4" + _digits(rng, 14)
    total, alt = 0, True
    for ch in reversed(body):
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    check = (10 - total % 10) % 10
    return body + str(check)


def gen_bank_acct(rng: random.Random) -> str:
    return _digits(rng, rng.randint(10, 12))


def gen_vin(rng: random.Random) -> str:
    chars = "ABCDEFGHJKLMNPRSTUVWXYZ0123456789"  # VIN excludes I,O,Q
    return "".join(rng.choice(chars) for _ in range(17))


def gen_plate(rng: random.Random) -> str:
    return f"{rng.randint(1,9)}{_letters(rng, 3)}{_digits(rng, 3)}"


def gen_device(rng: random.Random) -> str:
    return f"SN-{_letters(rng, 2)}{_digits(rng, 8)}"


def gen_biometric(rng: random.Random) -> str:
    return f"FP-TMPL-{_digits(rng, 10)}"


def gen_accession(rng: random.Random) -> str:
    return f"ACC{_digits(rng, 8)}"


def gen_photo(rng: random.Random) -> str:
    return f"subject_{_digits(rng, 4)}_face.jpg"


def gen_email(rng: random.Random) -> str:
    return f"{rng.choice(US_FIRST + INDIA_FIRST).lower()}.{_digits(rng, 3)}@example-clinic.org"


def gen_url(rng: random.Random) -> str:
    return f"https://patient-portal.example.org/p/{_digits(rng, 6)}"


def gen_ip(rng: random.Random) -> str:
    return f"{rng.randint(1,223)}.{rng.randint(0,255)}.{rng.randint(0,255)}.{rng.randint(1,254)}"


def gen_us_phone(rng: random.Random) -> str:
    return f"({rng.randint(200,989)}) 555-{rng.randint(100,999):03d}{rng.randint(0,9)}"


def gen_us_zip(rng: random.Random) -> str:
    return f"{rng.randint(10000,99999)}"


def gen_pin(rng: random.Random) -> str:           # bare 6-digit Indian PIN
    return f"{rng.randint(110000,855999)}"


def gen_subject_id(rng: random.Random) -> str:
    return f"SC{_digits(rng, 5)}"


def gen_family_id(rng: random.Random) -> str:
    return f"FID{_digits(rng, 5)}"


def gen_name(rng: random.Random, jur: str) -> str:
    if jur == "INDIA":
        return f"{rng.choice(INDIA_FIRST)} {rng.choice(INDIA_LAST)}"
    return f"{rng.choice(US_FIRST)} {rng.choice(US_LAST)}"


def gen_street(rng: random.Random, jur: str) -> str:
    if jur == "INDIA":
        return f"{rng.randint(1,200)}, {rng.choice(['MG Road','Gandhi Nagar','Station Road'])}"
    return f"{rng.randint(1,9999)} {rng.choice(['Oak St','Maple Ave','2nd Street'])}"


def gen_city(rng: random.Random, jur: str) -> str:
    return rng.choice(INDIA_CITY if jur == "INDIA" else US_CITY)


# Map a category -> its fresh-valid generator. Some take (rng) only, some (rng, jur).
VALUE_GEN: dict[str, Any] = {
    "aadhaar": gen_aadhaar, "pan": gen_pan, "voter_id": gen_voter,
    "passport_in": gen_passport_in, "dl_in": gen_dl_in, "ration": gen_ration,
    "abha": gen_abha, "uhid": gen_uhid, "gstin": gen_gstin, "mobile_in": gen_mobile_in,
    "pin": gen_pin,
    "ssn": gen_ssn, "mrn": gen_mrn, "medicare": gen_medicare, "insurance": gen_insurance,
    "dl_us": gen_dl_us, "passport_us": gen_passport_us, "credit_card": gen_credit_card,
    "bank_acct": gen_bank_acct, "vin": gen_vin, "plate": gen_plate, "device": gen_device,
    "biometric": gen_biometric, "accession": gen_accession, "photo": gen_photo,
    "us_phone": gen_us_phone, "fax": gen_us_phone, "zip": gen_us_zip,
    "email": gen_email, "url": gen_url, "ip": gen_ip,
    "subject_id": gen_subject_id, "family_id": gen_family_id,
}

# Categories whose value is purely numeric/alnum (placeholder = repeat a digit).
_NUMERICISH = {"aadhaar", "mobile_in", "ssn", "us_phone", "passport_us", "bank_acct",
               "pin", "zip", "ip", "credit_card"}


# ---------------------------------------------------------------------------
# Edge-case transforms
# ---------------------------------------------------------------------------


def _format_variant(cat: str, base: str, rng: random.Random) -> str:
    if cat == "aadhaar":
        d = base.replace(" ", "")
        return f"{d[:4]} {d[4:8]} {d[8:]}"
    if cat == "ssn":
        return base.replace("-", "")                  # 9-digit, no dashes
    if cat in ("mobile_in",):
        return f"+91 {base}"
    if cat == "us_phone":
        return base.replace("(", "").replace(") ", "-")
    if cat == "zip":
        return f"{base}-{_digits(rng, 4)}"
    return base


def _invalid_variant(cat: str, rng: random.Random) -> str:
    if cat == "aadhaar":                               # break Verhoeff: flip last digit
        v = gen_aadhaar(rng)
        bad = str((int(v[-1]) + 1) % 10)
        return v[:-1] + bad
    if cat == "credit_card":                           # break Luhn
        v = gen_credit_card(rng)
        return v[:-1] + str((int(v[-1]) + 1) % 10)
    if cat == "pan":
        return _letters(rng, 3) + _digits(rng, 6)      # wrong shape
    if cat == "ssn":
        return f"{_digits(rng,2)}-{_digits(rng,3)}-{_digits(rng,4)}"  # 2-3-4 misgrouped
    if cat in ("mobile_in",):
        return "5" + _digits(rng, 9)                   # starts with 5 -> not a mobile
    if cat in _NUMERICISH:
        return _digits(rng, 4)                          # too short for any shape
    return VALUE_GEN[cat](rng)[:-2] if cat in VALUE_GEN else "??"


def _placeholder_variant(cat: str, rng: random.Random) -> str:
    if cat in ("aadhaar",):
        return "9999 9999 9999"
    if cat in ("mobile_in", "us_phone"):
        return "9999999999"
    if cat == "ssn":
        return "000-00-0000"
    if cat in _NUMERICISH:
        return "0000000000"
    return "XXXXXXXX"


def _embed(value: str, rng: random.Random, jur: str) -> str:
    nm = gen_name(rng, jur)
    return rng.choice([
        f"Patient {nm} can be reached at {value} for follow-up.",
        f"Caregiver gave reference {value}; see chart note.",
        f"Per {nm}: contact details on file are {value}.",
    ])


def _prefix_variant(cat: str, value: str) -> str:
    if cat == "pin":
        return f"PIN: {value}"
    if cat in ("mobile_in",):
        return f"+91-{value}"
    if cat == "us_phone":
        return f"+1 {value}"
    return value


def _id_scenarios(cat: str, rng: random.Random, jur: str) -> list[tuple[str, str, bool, str]]:
    """16 labelled scenarios for a structured-identifier category."""
    out: list[tuple[str, str, bool, str]] = [
        ("valid", VALUE_GEN[cat](rng), True, "canonical"),
        ("valid_formatted", _format_variant(cat, VALUE_GEN[cat](rng), rng), True, "alt formatting"),
        ("invalid", _invalid_variant(cat, rng), False, "malformed / checksum-fail"),
        ("placeholder", _placeholder_variant(cat, rng), False, "repeating sentinel"),
        ("null_unk", "UNK", False, "null token"),
        ("null_na", "NA", False, "null token"),
        ("null_dot", ".", False, "null token"),
        ("empty", "", False, "blank"),
        ("embedded", _embed(VALUE_GEN[cat](rng), rng, jur), True, "id inside free text"),
        ("prefixed", _prefix_variant(cat, VALUE_GEN[cat](rng)), True, "keyword/country prefix"),
        ("whitespace_case", f"  {VALUE_GEN[cat](rng).lower()} ", True, "ws + lowercase"),
    ]
    while len(out) < N_EDGE_ROWS:
        out.append(("valid", VALUE_GEN[cat](rng), True, "bulk"))
    return out


def _date_scenarios(rng: random.Random, jur: str) -> list[tuple[str, str, bool, str]]:
    y = rng.randint(2015, 2024)
    m = rng.randint(1, 12)
    d = rng.randint(1, 28)
    iso = f"{y:04d}-{m:02d}-{d:02d}"
    out = [
        ("valid_iso", iso, True, "ISO yyyy-mm-dd"),
        ("dmy", f"{d:02d}/{m:02d}/{y}", True, "DMY"),
        ("mdy", f"{m:02d}/{d:02d}/{y}", True, "MDY"),
        ("ambiguous", f"{rng.randint(1,12):02d}/{rng.randint(1,12):02d}/{y}", True, "ambiguous d/m"),
        ("two_digit_year", f"{d:02d}/{m:02d}/{y % 100:02d}", True, "2-digit year"),
        ("textual", f"{d:02d} {['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][m-1]} {y}", True, "textual"),
        ("datetime", f"{iso} {rng.randint(0,23):02d}:{rng.randint(0,59):02d}:00", True, "datetime"),
        ("future", f"2099-{m:02d}-{d:02d}", True, "future > plausible_max_year"),
        ("sentinel_nines", "99999999", False, "all-9s sentinel"),
        ("sentinel_zeros", "00000000", False, "all-0s sentinel"),
        ("null_unk", "UNK", False, "null token"),
        ("null_na", "NA", False, "null token"),
        ("null_dot", ".", False, "null token"),
        ("empty", "", False, "blank"),
        ("embedded", f"Symptom onset around {iso}; reviewed at clinic.", True, "date in free text"),
        ("partial_year", f"{y}", True, "year only"),
    ]
    return out


def _age_scenarios(rng: random.Random) -> list[tuple[str, str, bool, str]]:
    out = [
        ("age_normal", str(rng.randint(18, 64)), False, "age <90: not an identifier"),
        ("age_normal", str(rng.randint(18, 64)), False, "age <90"),
        ("age_over89", "92", True, "age >89: HIPAA identifier (must cap)"),
        ("age_over89", "95", True, "age >89"),
        ("age_over89", "103", True, "age >89 (centenarian)"),
        ("age_boundary", "89", False, "exactly 89: still benign"),
        ("age_boundary", "90", True, "exactly 90: identifier"),
        ("null_unk", "UNK", False, "null token"),
        ("empty", "", False, "blank"),
    ]
    while len(out) < N_EDGE_ROWS:
        out.append(("age_normal", str(rng.randint(18, 64)), False, "bulk"))
    return out


def _name_scenarios(rng: random.Random, jur: str) -> list[tuple[str, str, bool, str]]:
    fn = rng.choice(INDIA_FIRST if jur == "INDIA" else US_FIRST)
    ln = rng.choice(INDIA_LAST if jur == "INDIA" else US_LAST)
    out = [
        ("valid", f"{fn} {ln}", True, "full name"),
        ("single", fn, True, "given name only"),
        ("hyphenated", f"{fn} {ln}-{rng.choice(US_LAST)}", True, "hyphenated surname"),
        ("titled", f"Dr. {fn} {ln}", True, "name with title (Dr.)"),
        ("lowercase", f"{fn.lower()} {ln.lower()}", True, "lowercased"),
        ("eponym_trap", rng.choice(EPONYM_TRAPS), False, "clinical eponym, NOT a person (FP bait)"),
        ("embedded", f"Spoke with {fn} {ln} regarding consent.", True, "name in free text"),
        ("null_unk", "UNK", False, "null token"),
        ("empty", "", False, "blank"),
    ]
    while len(out) < N_EDGE_ROWS:
        out.append(("valid", gen_name(rng, jur), True, "bulk"))
    return out


def _geo_scenarios(rng: random.Random, jur: str) -> list[tuple[str, str, bool, str]]:
    out = [
        ("street", gen_street(rng, jur), True, "street address (< state)"),
        ("city", gen_city(rng, jur), True, "city (< state)"),
        ("full", f"{gen_street(rng, jur)}, {gen_city(rng, jur)}", True, "full address"),
        ("null_unk", "UNK", False, "null token"),
        ("empty", "", False, "blank"),
    ]
    while len(out) < N_EDGE_ROWS:
        out.append(("city", gen_city(rng, jur), True, "bulk"))
    return out


def _benign_scenarios(subtype: str, rng: random.Random) -> list[tuple[str, str, bool, str]]:
    out: list[tuple[str, str, bool, str]] = []
    for _ in range(N_EDGE_ROWS):
        if subtype == "surname_like":
            out.append(("surname_like", rng.choice(EPONYM_TRAPS), False, "eponym FP bait"))
        elif subtype == "clinical_term":
            out.append(("clinical_term", rng.choice(CLINICAL_TERMS), False, "clinical free text"))
        elif subtype == "numeric":
            out.append(("numeric", str(rng.randint(50, 1200)), False, "lab/vital value"))
        else:  # categorical
            out.append(("categorical", rng.choice(["M", "F", "Yes", "No", "Hindu", "Muslim", "Positive", "Negative"]), False, "categorical"))
    return out


def scenarios_for(category: str, subtype: str, rng: random.Random, jur: str) -> list[tuple[str, str, bool, str]]:
    """Dispatch a category to its 16-row labelled scenario list."""
    if category == "date" or category == "birthdate":
        return _date_scenarios(rng, jur)
    if category == "age":
        return _age_scenarios(rng)
    if category == "name":
        return _name_scenarios(rng, jur)
    if category == "geo":
        return _geo_scenarios(rng, jur)
    if category == "non_phi":
        return _benign_scenarios(subtype, rng)
    if category in VALUE_GEN:
        return _id_scenarios(category, rng, jur)
    raise KeyError(f"no scenario builder for category {category!r}")


# ---------------------------------------------------------------------------
# Form specs: (column, category, placement, subtype)
#   placement: named | mislabeled | freetext
#   subtype only used for non_phi bait
# ---------------------------------------------------------------------------

KEYS = [("SUBJID", "subject_id", "named", ""), ("FID", "family_id", "named", "")]


def _india_forms() -> dict[str, list[tuple[str, str, str, str]]]:
    return {
        "01_DM_Demographics": KEYS + [
            ("DM_FNAME", "name", "named", ""),
            ("DM_LNAME", "name", "named", ""),
            ("DM_DOB", "birthdate", "named", ""),
            ("DM_AGE", "age", "named", ""),
            ("DM_STREET", "geo", "named", ""),
            ("DM_CITY", "geo", "named", ""),
            ("DM_PIN", "pin", "named", ""),
            ("DM_SEX", "non_phi", "named", "categorical"),
            ("DM_RELIGION", "non_phi", "named", "categorical"),
            ("DM_COMPDAT", "date", "named", ""),
        ],
        "02_CON_Contact": KEYS + [
            ("CON_MOBILE", "mobile_in", "named", ""),
            ("CON_ALTMOBILE", "mobile_in", "named", ""),
            ("CON_FAX", "fax", "named", ""),
            ("CON_EMAIL", "email", "named", ""),
            ("CON_URL", "url", "named", ""),
            ("CON_IP", "ip", "named", ""),
            ("CON_EMERGNAME", "name", "named", ""),
            ("CON_NOTE", "mobile_in", "freetext", ""),     # phone embedded in benign note
        ],
        "03_GID_GovIDs": KEYS + [
            ("GID_AADHAAR", "aadhaar", "named", ""),
            ("GID_PAN", "pan", "named", ""),
            ("GID_VOTERID", "voter_id", "named", ""),
            ("GID_PASSPORT", "passport_in", "named", ""),
            ("GID_DL", "dl_in", "named", ""),
            ("GID_RATIONNO", "ration", "named", ""),  # card *number* (bare "RATION"=APL/BPL category)
            ("GID_ABHA", "abha", "named", ""),
            ("GID_UHID", "uhid", "named", ""),
            ("GID_GSTIN", "gstin", "named", ""),
        ],
        "04_LB_Specimen": KEYS + [
            ("LB_ACCESSION", "accession", "named", ""),
            ("LB_DEVICE", "device", "named", ""),
            ("LB_COLLDAT", "date", "named", ""),
            ("LB_CD4", "non_phi", "named", "numeric"),
            ("LB_HGB", "non_phi", "named", "numeric"),
            ("LB_RESULT", "aadhaar", "mislabeled", ""),    # benign header, holds an Aadhaar
            ("LB_PHOTO", "photo", "named", ""),
            ("LB_FINGERPRINT", "biometric", "named", ""),
        ],
        "05_AE_AdverseEvent": KEYS + [
            ("AE_TERM", "non_phi", "named", "surname_like"),  # eponym FP bait
            ("AE_DESC", "non_phi", "named", "clinical_term"),
            ("AE_NARRATIVE", "name", "freetext", ""),        # name embedded in narrative
            ("AE_CONTACTLINE", "mobile_in", "freetext", ""),  # phone embedded
            ("AE_ONSETDAT", "date", "named", ""),
            ("AE_REFID", "pan", "mislabeled", ""),           # PAN under a benign ref column
        ],
        "06_FU_FollowUp": KEYS + [
            ("FU_VISDAT", "date", "named", ""),
            ("FU_DTHDAT", "date", "named", ""),
            ("FU_AGE", "age", "named", ""),
            ("FU_VITAL", "non_phi", "named", "numeric"),
            ("FU_NOTES", "aadhaar", "freetext", ""),         # Aadhaar embedded in notes
        ],
    }


def _us_forms() -> dict[str, list[tuple[str, str, str, str]]]:
    return {
        "01_DM_Demographics": KEYS + [
            ("DM_FNAME", "name", "named", ""),
            ("DM_LNAME", "name", "named", ""),
            ("DM_DOB", "birthdate", "named", ""),
            ("DM_AGE", "age", "named", ""),
            ("DM_STREET", "geo", "named", ""),
            ("DM_CITY", "geo", "named", ""),
            ("DM_ZIP", "zip", "named", ""),
            ("DM_SEX", "non_phi", "named", "categorical"),
            ("DM_RACE", "non_phi", "named", "categorical"),
            ("DM_COMPDAT", "date", "named", ""),
        ],
        "02_CON_Contact": KEYS + [
            ("CON_PHONE", "us_phone", "named", ""),
            ("CON_MOBILE", "us_phone", "named", ""),
            ("CON_FAX", "fax", "named", ""),
            ("CON_EMAIL", "email", "named", ""),
            ("CON_URL", "url", "named", ""),
            ("CON_IP", "ip", "named", ""),
            ("CON_EMERGNAME", "name", "named", ""),
            ("CON_NOTE", "us_phone", "freetext", ""),
        ],
        "03_GID_GovIDs": KEYS + [
            ("GID_SSN", "ssn", "named", ""),
            ("GID_MRN", "mrn", "named", ""),
            ("GID_MEDICARE", "medicare", "named", ""),
            ("GID_INSID", "insurance", "named", ""),
            ("GID_DL", "dl_us", "named", ""),
            ("GID_PASSPORT", "passport_us", "named", ""),
            ("GID_CREDITCARD", "credit_card", "named", ""),
            ("GID_BANKACCT", "bank_acct", "named", ""),
            ("GID_VIN", "vin", "named", ""),
            ("GID_PLATE", "plate", "named", ""),
        ],
        "04_LB_Specimen": KEYS + [
            ("LB_ACCESSION", "accession", "named", ""),
            ("LB_DEVICE", "device", "named", ""),
            ("LB_COLLDAT", "date", "named", ""),
            ("LB_CD4", "non_phi", "named", "numeric"),
            ("LB_HGB", "non_phi", "named", "numeric"),
            ("LB_RESULT", "ssn", "mislabeled", ""),          # benign header, holds an SSN
            ("LB_PHOTO", "photo", "named", ""),
            ("LB_FINGERPRINT", "biometric", "named", ""),
        ],
        "05_AE_AdverseEvent": KEYS + [
            ("AE_TERM", "non_phi", "named", "surname_like"),
            ("AE_DESC", "non_phi", "named", "clinical_term"),
            ("AE_NARRATIVE", "name", "freetext", ""),
            ("AE_CONTACTLINE", "us_phone", "freetext", ""),
            ("AE_ONSETDAT", "date", "named", ""),
            ("AE_REFID", "ssn", "mislabeled", ""),
        ],
        "06_FU_FollowUp": KEYS + [
            ("FU_VISDAT", "date", "named", ""),
            ("FU_DTHDAT", "date", "named", ""),
            ("FU_AGE", "age", "named", ""),
            ("FU_VITAL", "non_phi", "named", "numeric"),
            ("FU_NOTES", "ssn", "freetext", ""),
        ],
    }


ARMS = {"Synth-India": ("INDIA", _india_forms), "Synth-US": ("USA", _us_forms)}


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def build() -> dict[str, Any]:
    rng = random.Random(SEED)
    gt_cells: list[dict[str, Any]] = []
    gt_columns: dict[str, Any] = {}
    counts = {"forms": 0, "cells": 0, "identifier_cells": 0}

    for arm, (jur, forms_fn) in ARMS.items():
        arm_dir = DATA_ROOT / arm / "datasets"
        arm_dir.mkdir(parents=True, exist_ok=True)
        forms = forms_fn()
        for form_name, spec in forms.items():
            columns = [c[0] for c in spec]
            # Per-column scenario lists (length N_EDGE_ROWS), then normal rows.
            per_col_scen: dict[str, list[tuple[str, str, bool, str]]] = {}
            for col, cat, _placement, subtype in spec:
                per_col_scen[col] = scenarios_for(cat, subtype, rng, jur)

            n_rows = N_EDGE_ROWS + N_NORMAL_ROWS
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.append(columns)

            for r in range(n_rows):
                row_vals: list[str] = []
                for col, cat, placement, subtype in spec:
                    if r < N_EDGE_ROWS:
                        label, value, is_id, note = per_col_scen[col][r]
                    else:
                        # normal bulk: fresh valid value, or benign for non_phi
                        scen = scenarios_for(cat, subtype, rng, jur)[0]
                        label, value, is_id, note = ("valid", scen[1], scen[2], "bulk")
                    row_vals.append(value)
                    gt_cells.append({
                        "study": arm, "jurisdiction": jur, "form": form_name,
                        "row": r, "column": col, "category": cat,
                        "placement": placement, "edge_case": label,
                        "is_identifier": is_id, "note": note,
                    })
                    counts["cells"] += 1
                    if is_id:
                        counts["identifier_cells"] += 1
                ws.append(row_vals)

            wb.save(arm_dir / f"{form_name}.xlsx")
            counts["forms"] += 1
            for col, cat, placement, subtype in spec:
                gt_columns[f"{arm}/{form_name}/{col}"] = {
                    "category": cat, "placement": placement, "subtype": subtype,
                    "header_reveals_category": placement == "named",
                }

        # Per-arm manifest + privacy config so the arm can feed the real pipeline.
        man = {"required": [f"{f}.xlsx" for f in forms], "optional": [], "reject": []}
        (DATA_ROOT / arm / "_forms_manifest.yaml").write_text(
            yaml.dump(man, sort_keys=True), encoding="utf-8")
        (DATA_ROOT / arm / "_study_privacy.yaml").write_text(yaml.dump({
            "jurisdictions": [jur] if jur != "USA" else ["USA"],
            "data_as_of": "2026-06-26",
            "rule_refresh": "offline", "conflict_policy": "strictest_wins",
            "approval": {"mode": "auto", "max_synthetic_attempts": 5},
        }, sort_keys=False), encoding="utf-8")

    # Emit ground truth.
    with (HERE / "ground_truth.jsonl").open("w", encoding="utf-8") as fh:
        for cell in gt_cells:
            fh.write(json.dumps(cell) + "\n")
    (HERE / "ground_truth_columns.json").write_text(
        json.dumps(gt_columns, indent=2, sort_keys=True), encoding="utf-8")
    return counts


# ---------------------------------------------------------------------------
# Self-check: verify generated labels against the PRODUCTION detector
# ---------------------------------------------------------------------------


def _ruler() -> dict[str, Any]:
    return {name: pat for name, pat in BLOCKING_PATTERNS}


def _matches(pat: Any, value: str) -> bool:
    if isinstance(pat, (VerhoeffPattern, IndianPhonePattern)):
        return pat.search(value) is not None
    return pat.search(value) is not None


def selfcheck() -> None:
    """Prove the ground-truth labels are honest w.r.t. the real ruler."""
    rng = random.Random(SEED + 1)
    R = _ruler()
    fails: list[str] = []

    # Aadhaar: valid trips, invalid + placeholder evade.
    for _ in range(50):
        v = gen_aadhaar(rng)
        if not _matches(R["AADHAAR"], v):
            fails.append(f"valid aadhaar evaded ruler: {v}")
        bad = v[:-1] + str((int(v[-1]) + 1) % 10)
        if _matches(R["AADHAAR"], bad):
            fails.append(f"invalid aadhaar matched ruler: {bad}")
    if _matches(R["AADHAAR"], "9999 9999 9999"):
        fails.append("aadhaar placeholder matched ruler")

    # Indian phone: valid validates, placeholder rejected.
    for _ in range(50):
        v = gen_mobile_in(rng)
        if R["INDIAN_PHONE"].search(v) is None:
            fails.append(f"valid indian phone evaded ruler: {v}")
    if R["INDIAN_PHONE"].search("9999999999") is not None:
        fails.append("indian phone placeholder matched ruler")

    # PAN / voter / SSN / email shapes.
    for _ in range(20):
        if not _matches(R["PAN"], gen_pan(rng)):
            fails.append("valid PAN evaded ruler")
        if not _matches(R["INDIAN_VOTER_ID"], gen_voter(rng)):
            fails.append("valid voter id evaded ruler")
        if not _matches(R["SSN"], gen_ssn(rng)):
            fails.append("valid SSN evaded ruler")
        if not _matches(R["EMAIL"], gen_email(rng)):
            fails.append("valid email evaded ruler")

    # Subject IDs match the subject-id catalogue.
    for _ in range(20):
        sid = gen_subject_id(rng)
        if not any(p.search(sid) for p in SUBJECT_ID_PATTERNS):
            fails.append(f"subject id evaded SUBJECT_ID_PATTERNS: {sid}")

    # Bare PIN must NOT match value-ruler (keyword-gated) but prefixed MUST.
    bare = gen_pin(rng)
    if _matches(R["INDIAN_PIN"], bare):
        fails.append(f"bare PIN unexpectedly matched value-ruler: {bare}")
    if not _matches(R["INDIAN_PIN"], f"PIN: {bare}"):
        fails.append("prefixed PIN failed to match value-ruler")

    if fails:
        for f in fails:
            print(f"  SELFCHECK FAIL: {f}", file=sys.stderr)
        raise SystemExit(f"selfcheck failed with {len(fails)} error(s)")
    print("selfcheck OK: generated labels are consistent with scripts.security.phi_patterns")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selfcheck-only", action="store_true")
    args = ap.parse_args()
    selfcheck()
    if args.selfcheck_only:
        return
    counts = build()
    print(f"built {counts['forms']} forms, {counts['cells']} cells "
          f"({counts['identifier_cells']} planted identifier cells)")
    print(f"  -> {DATA_ROOT}/Synth-India, {DATA_ROOT}/Synth-US")
    print(f"  -> ground_truth.jsonl ({counts['cells']} rows), ground_truth_columns.json")


if __name__ == "__main__":
    main()
