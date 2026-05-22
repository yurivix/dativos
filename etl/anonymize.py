"""Deterministic pseudo-anonymization of advogado identities.

The same `normalize_name(nome)` always maps to the same `ADV_xxxxxxxx`
pseudonym across runs and yearly files, as long as the salt is stable.

**Why name-only, not (name + masked CPF):**
The SEFAZ files publish the masked CPF in inconsistent formats across years:
  - `***42351***`   (5 digits visible, old format)
  - `***423517**`   (6 digits visible, newer)
  - `***.423.5**`   (with dot punctuation, occasional)
Same person, different masks → different (name+cpf) keys → different ADV_ids,
which silently splits one lawyer's payments into multiple pseudonyms. We saw
3,199 names duplicated in the base before fixing.

By keying on normalize_name alone we collapse those duplicates correctly.
The trade-off is that genuine namesakes with different CPFs get merged —
but in this corpus (≈11k names, mostly multi-token full names) collisions
are rare. The `cpfs_vistos` array column in `advogados` keeps every masked
CPF observed for auditing.
"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
import unicodedata
from dataclasses import dataclass
from pathlib import Path


def _strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)
    )


def normalize_name(name: str) -> str:
    """Lowercase, strip accents, collapse whitespace. Used for dedupe + hashing."""
    s = _strip_accents(name).lower()
    s = re.sub(r"\s+", " ", s).strip()
    return s


def load_or_create_salt(salt_path: Path) -> str:
    """Read salt from file or env var, or create a new random one.

    Resolution order:
      1. DATIVOS_SALT env var (set by CI / locally for reproducible builds).
      2. salt_path file (created on first local run).
      3. Generate a new random salt and persist it to salt_path.
    """
    env_salt = os.environ.get("DATIVOS_SALT")
    if env_salt:
        return env_salt
    if salt_path.exists():
        s = salt_path.read_text(encoding="utf-8").strip()
        if s:
            return s
    salt_path.parent.mkdir(parents=True, exist_ok=True)
    new_salt = secrets.token_hex(32)
    salt_path.write_text(new_salt, encoding="utf-8")
    return new_salt


PSEUDONYM_HEX_LEN = 12  # ~2.8e14 buckets; collision probability with ~11k IDs is ~2e-7.


def pseudonym(nome: str, cpf_mascarado: str | None, salt: str) -> str:
    """Compute the deterministic ADV_xxxxxxxxxxxx pseudonym.

    `cpf_mascarado` is accepted for backwards compatibility but **not used** —
    see module docstring for why. Same nome → same ADV.
    """
    key = f"{normalize_name(nome)}|{salt}"
    h = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return f"ADV_{h[:PSEUDONYM_HEX_LEN]}"


@dataclass(frozen=True)
class AdvogadoIdentity:
    advogado_id: str          # ADV_xxxxxxxxxxxx (the pseudonym)
    nome: str                 # real name (only persisted in the full DB)
    nome_normalizado: str     # for dedupe / search
    cpfs_vistos: tuple[str, ...] = ()  # all masked CPFs observed across files
