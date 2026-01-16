import re
from typing import Any, Dict, List, Optional, Tuple

# Try rapidfuzz; fall back to difflib if not installed
try:
    from rapidfuzz import fuzz
    _HAS_RAPIDFUZZ = True
except ImportError:
    import difflib
    _HAS_RAPIDFUZZ = False


_SUFFIXES = {
    "llc", "l.l.c", "inc", "inc.", "corp", "corp.", "corporation",
    "co", "co.", "company", "ltd", "ltd.", "limited",
    "pllc", "p.c", "pc", "llp", "l.l.p", "lp", "l.p",
    "group", "holdings", "holding", "services", "service",
    "construction", "consulting",  # optional: comment these out if too aggressive
}

# Phrases you often want to drop when buyouts add location fluff
_LOCATION_FILLER = {"of", "the", "state", "new", "jersey", "ny", "nj", "ct", "ma", "incorporated"}

def _normalize_company_name(name: str) -> str:
    """
    Normalize a company name for fuzzy matching.
    - lowercases
    - removes punctuation
    - removes common legal suffixes
    - strips extra whitespace
    """
    if not name:
        return ""

    s = name.lower()

    # Replace & with 'and' (common variation)
    s = s.replace("&", " and ")

    # Remove UUID-like junk sometimes appended in sandboxes
    # e.g. "Company - d5d27260-b3b8-42ed-957b-4f5c8227f5ee"
    s = re.sub(r"\b[0-9a-f]{8}\b-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-\b[0-9a-f]{12}\b", " ", s)

    # Drop everything after " - " if your sandbox/vendor names append identifiers
    # (comment out if this could remove meaningful info in production)
    s = re.split(r"\s-\s", s, maxsplit=1)[0]

    # Remove punctuation -> spaces
    s = re.sub(r"[^a-z0-9\s]", " ", s)

    # Collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()

    # Tokenize and remove suffix tokens
    tokens = [t for t in s.split() if t not in _SUFFIXES]

    # Optionally remove obvious location filler (helps "of New Jersey" vs not)
    # If you think this is too aggressive, remove this block.
    tokens = [t for t in tokens if t not in _LOCATION_FILLER]

    return " ".join(tokens).strip()


def _score(a: str, b: str) -> float:
    """
    Return similarity score 0..100.
    Uses rapidfuzz if available; otherwise difflib.
    """
    if not a or not b:
        return 0.0

    if _HAS_RAPIDFUZZ:
        # token_set_ratio handles extra words well; partial_ratio handles shortened names
        s1 = fuzz.token_set_ratio(a, b)
        s2 = fuzz.partial_ratio(a, b)
        s3 = fuzz.token_sort_ratio(a, b)
        return max(s1, s2, s3)

    # Fallback: difflib ratio (0..1) -> (0..100)
    return difflib.SequenceMatcher(None, a, b).ratio() * 100.0


def subNameMatcher(
    projDirData: List[Dict[str, Any]],
    subName: str,
    *,
    min_score: float = 85.0,
) -> Optional[Dict[str, Any]]:
    """
    Match a buyout subcontractor name (subName) to a vendor entry
    in the Procore project directory.

    Returns:
        The matched vendor dict if a confident match is found,
        otherwise None.
    """
    if not subName or not projDirData:
        return None

    target_norm = _normalize_company_name(subName)

    best_score = 0.0
    best_vendor = None

    for v in projDirData:
        vendor_name = v['company'] or ""
        vendor_norm = _normalize_company_name(vendor_name)

        score = _score(target_norm, vendor_norm)

        if score > best_score:
            best_score = score
            best_vendor = v

    if best_vendor and best_score >= min_score:
        return best_vendor

    return None