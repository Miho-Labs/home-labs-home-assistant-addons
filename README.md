# Home Labs Add-ons

Repozytorium dodatków Home Assistant dla klientów [Home Labs](https://home-labs.pl). W sklepie
dodatków oba widać w jednej sekcji **Home Labs Add-ons**.

| Dodatek | Co robi | Dokumentacja |
|---|---|---|
| **Home Labs Sync** | pobiera konfigurację opublikowaną dla domu przez Home Labs (dashboardy, motyw, automatyzacje i sceny, zdjęcia wygaszacza), eksportuje listę encji | [home_labs_sync/DOCS.md](home_labs_sync/DOCS.md) |
| **Home Labs Backup** | kopie zapasowe Home Assistant w chmurze Home Labs jako natywna lokalizacja kopii (Ustawienia → System → Kopie zapasowe) | [home_labs_backup/DOCS.md](home_labs_backup/DOCS.md) |

Oba używają tego samego tokenu z panelu Home Labs (`hls_…`).

## Dodaj repozytorium

1. W Home Assistant: **Ustawienia → Dodatki → Sklep z dodatkami**.
2. Prawy górny róg **⋮ → Repozytoria**.
3. Wklej adres i kliknij **Dodaj**:

   ```
   https://github.com/Miho-Labs/home-labs-home-assistant-addons
   ```

4. Odśwież sklep; w sekcji **Home Labs Add-ons** zainstaluj potrzebne dodatki i w zakładce
   **Konfiguracja** każdego wklej token.

Albo jednym kliknięciem:

[![Dodaj repozytorium do Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2FMiho-Labs%2Fhome-labs-home-assistant-addons)

> Repozytorium musi pozostać **publiczne** — Supervisor klonuje je bez uwierzytelnienia. Obrazy
> `ghcr.io/miho-labs/home-labs-sync-*` i `home-labs-backup-*` również muszą być publiczne.

### Przejście ze starych repozytoriów

Dodatki były wcześniej w `hl-ha-config-sync-plugin` i `hl-ha-backup-plugin`. Home Assistant wiąże
zainstalowany dodatek z repozytorium, z którego pochodzi, więc przejście to jednorazowa reinstalacja:

| # | Krok |
|---|---|
| 1 | Dodaj to repozytorium (jak wyżej) |
| 2 | Odinstaluj stary **Home Labs Sync** / **Home Labs Backup** (pliki w `/config`, dashboardy, integracja kopii i same kopie zostają) |
| 3 | Zainstaluj je z sekcji **Home Labs Add-ons**, wklej token, uruchom |
| 4 | Usuń stare repozytoria z listy **⋮ → Repozytoria** |

Home Labs Sync po reinstalacji pobierze bieżące wydanie jeszcze raz (pliki są te same, zmian nie
będzie). Home Labs Backup od razu przekaże token istniejącej integracji; restart HA nie jest potrzebny.

## Model zaufania

| Dodatek | Co zapisuje |
|---|---|
| Home Labs Sync | wyłącznie wskazane katalogi (`dashboards/`, `www/`, `themes/`, `home_labs/`, plik pakietu, `/media/wallpanel/`) i oznaczony blok na końcu `configuration.yaml` (po zrobieniu kopii); nigdy `secrets.yaml` ani `.storage/`; każdy plik weryfikowany SHA-256, nic pobranego nie jest wykonywane |
| Home Labs Backup | wyłącznie `custom_components/home_labs_backup`; kopie wysyła Home Assistant, zaszyfrowane kluczem, którego serwer Home Labs nie zna; klucze do magazynu ma tylko serwer |

Token daje dostęp tylko do jednego domu i można go w każdej chwili unieważnić w panelu Home Labs.

## Dla utrzymujących

| | |
|---|---|
| Układ | jeden katalog = jeden dodatek (`home_labs_sync/`, `home_labs_backup/`): kod, testy, `config.yaml`, ikony, `DOCS.md`, `CHANGELOG.md`; w katalogu głównym tylko wspólne narzędzia |
| Lokalnie | `pip install aiohttp pyyaml pytest pytest-aiohttp pytest-asyncio ruff`, potem `ruff check . && ruff format --check .` oraz testy **osobno dla każdego dodatku**: `python -m pytest home_labs_sync/tests` i `python -m pytest home_labs_backup/tests` |
| Backup w HA | `pip install pytest-homeassistant-custom-component`, `python -m pytest home_labs_backup/tests_ha -o testpaths=` (osobny job w CI) |
| Wersje | każdy dodatek ma własną: `version` w swoim `config.yaml` (+ `hl_sync/__init__.py`, a w Backup także `hl_backup/__init__.py`, `manifest.json` i `const.py` integracji — testy pilnują zgodności) |
| Wydanie | podnieś wersję jednego dodatku, uzupełnij jego `CHANGELOG.md`, merge do `main`. `build.yml` buduje obrazy `aarch64`/`amd64` tylko dla wersji, której obrazu jeszcze nie ma — drugiego dodatku nie rusza. HA pokaże aktualizację tylko tego dodatku |
| Kolejność | HA widzi nową wersję od razu po merge'u, a obraz powstaje kilka minut później — aktualizacja uruchomiona w tym oknie kończy się „image not found”; wystarczy ponowić |
| Ikony | `python3 tools/make_icons.py` renderuje PNG-i sklepu obu dodatków i obrazy marki integracji Backup (`custom_components/home_labs_backup/brand/`, ikona lokalizacji kopii w HA 2026.3+) |
