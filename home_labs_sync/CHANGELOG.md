# Changelog

## 0.3.0

- Nowy zakres `packages`: pakiety HA Home Labs (`clients/<slug>/packages/*.yaml` w repo — np. encja `climate_template` na pilocie IR, skrypty, pomocnicy, grupa `notify`) są doklejane do pakietu `home_labs/package.yaml`. `configuration.yaml` bez zmian.
- Zmiana albo usunięcie pakietów = wymagany restart Home Assistant (polityka `restart_policy` jak przy dashboardach).
- Manifest: nowe pole `release.package.packages` (tekst YAML). Dozwolony tylko tag `!secret`; odrzucane integracje `homeassistant`, `lovelace`, `frontend`, `http`, `shell_command`, `command_line`, `python_script`, `pyscript` oraz klucze `automation home_labs` / `scene home_labs`.
- Istniejące instalacje zachowują zapisaną listę `scopes` (bez `packages`) — dopisz zakres ręcznie; do tego czasu dodatek ostrzega w panelu i raporcie, że pakiety z wydania pominął.

## 0.2.2

- Domyślna polityka restartu to teraz `auto` (restart w oknie `restart_window`, tylko gdy „Sprawdź konfigurację” przeszło). Istniejące instalacje zachowują zapisaną wartość — zmień ją ręcznie w konfiguracji dodatku.
- Panel: poprawiony układ — karty Status i Akcje wypełniają szerokość strony, długie wartości zawijają się zamiast wychodzić poza kartę.

## 0.2.1

- Samonaprawa: cykl z odpowiedzią „bez zmian” (304) uzgadnia pliki i pakiet `home_labs` z ostatnim znanym wydaniem — po usunięciu ręcznie wklejonego bloku `lovelace:` z `configuration.yaml` dashboardy wracają do pakietu przy najbliższym cyklu, bez czekania na nową publikację; skasowany plik z wydania jest odtwarzany.
- **Synchronizuj teraz** pobiera pełny manifest (bez ETag), więc zawsze robi pełny przebieg.
- Ostatnie wydanie jest zapamiętywane w stanie dodatku (`/data/state.json`), także po restarcie dodatku.

## 0.2.0

- Nowy zakres `media`: zdjęcia wygaszacza WallPanel z repo (`clients/<slug>/media/wallpanel/`) trafiają do `/media/wallpanel` na Home Assistant (nowe montowanie `media` w dodatku). Zdjęcie usunięte z repo jest usuwane z Home Assistant — tylko takie, które dodatek sam zapisał i którego nikt lokalnie nie zmienił; własne pliki klienta zostają. Zakres można wyłączyć, gdy klient sam zarządza zdjęciami.
- Manifest: każdy plik ma pole `root` (`config` albo `media`); brak pola oznacza `config` (zgodność ze starszym serwerem). Dla `media` dozwolone są wyłącznie ścieżki `wallpanel/**`.
- Wyższe limity rozmiaru: 10 MB na plik, 150 MB na wydanie.
- Stan dodatku: klucze `applied` zapisują katalog docelowy (`config:…` / `media:…`); wpisy z 0.1.x są traktowane jako `config`, więc aktualizacja niczego nie pobiera ponownie ani nie usuwa.
- Brak katalogu `/media` (starszy Supervisor) nie przerywa synchronizacji: zakres `media` jest pomijany z czytelnym komunikatem, pozostałe zakresy działają (status „częściowo”).
- W raportach i panelu pliki mediów są oznaczone jako `media:wallpanel/…`; zdjęcia nie wymagają przeładowania ani restartu Home Assistant.

## 0.1.0

Pierwsze wydanie.

- Pobieranie manifestu i plików wydania z serwera Home Labs (dashboardy, `www/`, motyw, automatyzacje i sceny Home Labs), weryfikacja sum SHA-256, zapis „wszystko albo nic”.
- Pakiet `home_labs` składany lokalnie (`lovelace`, `automation home_labs`, `scene home_labs`), automatyczne dopisanie bloku `homeassistant: packages:` do `configuration.yaml` albo instrukcja do ręcznego wklejenia.
- Przejęcie automatyzacji i scen Home Labs z `automations.yaml` / `scenes.yaml` z kopią zapasową.
- Wykrywanie kolizji z ręcznie wklejonym blokiem `lovelace:`.
- Przeładowanie automatyzacji, scen i motywów bez restartu; polityki restartu `never` / `notify` / `auto` z oknem czasowym.
- Sensor `sensor.home_labs_sync`, powiadomienia trwałe, raporty do serwera Home Labs.
- Eksport encji (CSV) na przycisk, według harmonogramu albo na prośbę Home Labs.
- Panel w menu bocznym (ingress) ze statusem, dziennikiem i przyciskami.
- Tryb próbny `dry_run`.
- Obrazy tylko dla `aarch64` i `amd64` — Home Assistant wycofało wsparcie 32-bitowego ARM (`armv7`), a `home-assistant/builder` nie zna już tej architektury.
