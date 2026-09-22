"""Remove Home Labs automations/scenes from the UI-managed files once the package owns them."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .fsutil import atomic_write, backup_file
from .hlyaml import contains_tagged, dump_plain, load_tolerant

log = logging.getLogger(__name__)

DOMAIN_FILES = {"automations": "automations.yaml", "scenes": "scenes.yaml"}


@dataclass
class DomainResult:
    domain: str
    file: str
    removed_ids: list[str] = field(default_factory=list)
    skipped: str | None = None
    aborted: bool = False
    backup: Path | None = None


@dataclass
class TakeoverResult:
    domains: list[DomainResult] = field(default_factory=list)

    @property
    def changed_scopes(self) -> set[str]:
        return {d.domain for d in self.domains if d.removed_ids and not d.aborted}

    @property
    def aborted(self) -> list[str]:
        return [d.domain for d in self.domains if d.aborted]

    @property
    def warnings(self) -> list[str]:
        out = [f"{d.file}: {d.skipped}" for d in self.domains if d.skipped]
        out += [
            f"{d.file}: plik zmienił się w trakcie — spróbuję ponownie"
            for d in self.domains
            if d.aborted
        ]
        return out


def _stat_sig(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except FileNotFoundError:
        return None
    return (st.st_mtime_ns, st.st_size)


def run(
    config_dir: Path,
    backup_dir: Path,
    managed_ids: dict[str, list[str]],
    scopes: tuple[str, ...],
    on_before_write: Callable[[Path], None] | None = None,
) -> TakeoverResult:
    result = TakeoverResult()
    for domain, filename in DOMAIN_FILES.items():
        if domain not in scopes:
            continue
        ids = {str(i) for i in managed_ids.get(domain, []) or []}
        if not ids:
            continue
        result.domains.append(
            _take_over(config_dir, backup_dir, domain, filename, ids, on_before_write)
        )
    return result


def _take_over(
    config_dir: Path,
    backup_dir: Path,
    domain: str,
    filename: str,
    ids: set[str],
    on_before_write: Callable[[Path], None] | None,
) -> DomainResult:
    res = DomainResult(domain, filename)
    path = Path(config_dir) / filename
    sig = _stat_sig(path)
    if sig is None:
        return res
    try:
        raw = path.read_bytes()
    except OSError as exc:
        res.skipped = f"nie udało się odczytać ({exc})"
        return res
    try:
        data = load_tolerant(raw.decode("utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        res.skipped = f"nie parsuje się ({str(exc).splitlines()[0]})"
        log.warning("%s nie parsuje się — pomijam przejęcie: %s", filename, exc)
        return res
    if data is None:
        data = []
    if not isinstance(data, list):
        res.skipped = "nie jest listą — pomijam"
        log.warning("%s nie jest listą — pomijam przejęcie", filename)
        return res
    if contains_tagged(data):
        res.skipped = "zawiera tagi YAML (!secret/!include) — nie mogę bezpiecznie przepisać"
        log.warning("%s zawiera tagi YAML — pomijam przejęcie", filename)
        return res

    kept = []
    for item in data:
        item_id = item.get("id") if isinstance(item, dict) else None
        if item_id is not None and str(item_id) in ids:
            res.removed_ids.append(str(item_id))
        else:
            kept.append(item)
    if not res.removed_ids:
        return res

    res.backup = backup_file(path, backup_dir, filename)
    if on_before_write is not None:
        on_before_write(path)
    if _stat_sig(path) != sig:
        res.aborted = True
        res.removed_ids = []
        log.warning(
            "%s zmienił się w trakcie przejęcia — przerywam, spróbuję przy następnym cyklu",
            filename,
        )
        return res
    atomic_write(path, dump_plain(kept).encode("utf-8"))
    log.info(
        "Przejęto %d wpis(ów) z %s: %s", len(res.removed_ids), filename, ", ".join(res.removed_ids)
    )
    return res
