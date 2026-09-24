# Home Labs Sync

Dodatek do Home Assistant dla klientów Home Labs (home-labs.pl). Pobiera konfigurację opublikowaną dla **tego domu** przez Home Labs i zapisuje ją w katalogu konfiguracji Home Assistant (a zdjęcia wygaszacza — w katalogu mediów), a potem wysyła krótki raport zwrotny. Na życzenie eksportuje też listę encji domu (CSV), której Home Labs potrzebuje do przygotowania dashboardów i automatyzacji.

## Co robi dodatek

| Zakres (`scopes`) | Pliki | Efekt |
|---|---|---|
| `dashboards` | `/config/dashboards/**`, `/config/www/**`, wpis `lovelace:` w pakiecie | dashboardy Home Labs w menu bocznym |
| `themes` | `/config/themes/**` | motyw „Home Labs Noc” |
| `automations` | `/config/home_labs/automations.yaml` | automatyzacje Home Labs (osobny plik, nie `automations.yaml`) |
| `scenes` | `/config/home_labs/scenes.yaml` | sceny Home Labs |
| `media` | `/media/wallpanel/**` | zdjęcia wygaszacza WallPanel na tabletach (katalog mediów HA, nie konfiguracji) |
| `packages` | treść w pakiecie `home_labs/package.yaml` | pakiety HA Home Labs: encje szablonowe (np. klimatyzacja na IR), skrypty, pomocnicy, grupy powiadomień — szczegóły w „Pakiety” niżej |

Do tego dodatek:

- składa lokalnie pakiet `home_labs/package.yaml` i dołącza go w `configuration.yaml` (szczegóły niżej),
- przejmuje automatyzacje i sceny Home Labs, które wcześniej były wklejane ręcznie w edytorze UI (kopia zapasowa w danych dodatku),
- przeładowuje automatyzacje, sceny i motywy bez restartu; restart jest potrzebny **tylko** przy dodaniu/zmianie dashboardu albo pakietów; zdjęcia wygaszacza nie wymagają niczego — WallPanel sam odczytuje katalog,
- wystawia sensor `sensor.home_labs_sync` i powiadomienia trwałe w Home Assistant,
- pokazuje panel „Home Labs Sync” w menu bocznym.

Dodatek **nie** rusza własnych automatyzacji, scen, skryptów ani innych plików klienta.

## Instalacja krok po kroku

1. **Ustawienia → Dodatki → Sklep z dodatkami**.
2. Prawy górny róg **⋮ → Repozytoria** → wklej `https://github.com/Miho-Labs/home-labs-home-assistant-addons` → **Dodaj** → zamknij.
3. Odśwież sklep, znajdź **Home Labs Sync** → **Zainstaluj**.
4. Zakładka **Konfiguracja**: wklej **Token dostępu** otrzymany z panelu Home Labs (zaczyna się od `hls_`). Na pierwszy start zalecamy włączyć **Tryb próbny (dry run)** — zobaczysz, co dodatek zmieni, bez zapisu.
5. Zakładka **Informacje** → **Uruchom**. Włącz też „Uruchamiaj przy starcie” i „Watchdog”.
6. W menu bocznym pojawi się **Home Labs Sync** — otwórz panel i sprawdź status.
7. Jeśli wszystko wygląda dobrze, wyłącz tryb próbny, zapisz konfigurację i kliknij **Synchronizuj teraz**.
8. Gdy panel pokaże „wymagany restart Home Assistant” — uruchom ponownie Home Assistant (Ustawienia → System → ⋮ → Uruchom ponownie). Po restarcie dashboardy Home Labs są w menu bocznym.

## Opcje

| Opcja | Domyślnie | Opis |
|---|---|---|
| `api_url` | `https://public.home-labs.pl` | Adres serwera Home Labs. Zmieniaj tylko na prośbę Home Labs. |
| `api_token` | — | Token z panelu Home Labs. Daje dostęp tylko do konfiguracji tego domu. |
| `interval_minutes` | `30` | Co ile minut sprawdzać, czy jest nowa konfiguracja (5–1440). Serwer odpowiada „bez zmian” bardzo tanio, więc 30 min jest w porządku. |
| `scopes` | wszystkie sześć | Które zakresy synchronizować. Wyłączony zakres = pliki zostają, dodatek przestaje nimi zarządzać. `media` wyłącz, jeśli klient sam zarządza zdjęciami wygaszacza. **Instalacje sprzed 0.3.0 mają zapisaną starą listę bez `packages`** — dopisz go ręcznie, gdy Home Labs przygotuje pakiety (dodatek ostrzega w panelu, gdy wydanie je zawiera, a zakres jest wyłączony). |
| `restart_policy` | `auto` | `never` — tylko raport do Home Labs; `notify` — powiadomienie w HA, gdy restart jest potrzebny; `auto` — restart automatyczny w oknie `restart_window`, tylko gdy „Sprawdź konfigurację” przeszło i tylko raz na wydanie. |
| `restart_window` | `02:00-05:00` | Okno restartu automatycznego (czas lokalny), może przechodzić przez północ. |
| `entities_export_schedule` | `off` | Eksport encji według harmonogramu: `daily` (codziennie) lub `weekly` (poniedziałki). |
| `entities_export_hour` | `4` | Godzina eksportu według harmonogramu. |
| `dry_run` | `false` | Tryb próbny: pełny plan w panelu i raporcie, zero zapisu. |
| `log_level` | `info` | `debug` przydaje się przy zgłaszaniu problemów. |

## Jak działa bootstrap `configuration.yaml`

Dodatek nigdy nie edytuje istniejących linii `configuration.yaml`. Może jedynie **dopisać na końcu** oznaczony blok, po wcześniejszym zrobieniu kopii. Przy każdej synchronizacji sprawdza plik i wybiera jeden z wariantów:

| Wariant | Kiedy | Co robi dodatek |
|---|---|---|
| a) brak klucza `homeassistant:` (typowa świeża instalacja) | plik nie ma sekcji `homeassistant:` | dopisuje blok poniżej; pakiet = `home_labs/package.yaml` |
| b) `packages: !include_dir_named <katalog>` (lub `!include_dir_merge_named`) | klient trzyma pakiety w katalogu | zapisuje pakiet jako `<katalog>/home_labs.yaml`; `configuration.yaml` bez zmian |
| c) `homeassistant:` istnieje, ale bez `packages:` (albo `packages:` bez wpisu `home_labs`) | np. klient ma `homeassistant: customize:` | **nic nie zmienia**; w panelu i w powiadomieniu „Home Labs Sync: dokończ konfigurację” podaje dokładne linie do wklejenia |
| d) `packages: home_labs: !include home_labs/package.yaml` już jest | po wariancie a) lub po ręcznym wklejeniu | nic do zrobienia |

Blok dopisywany w wariancie a):

```yaml
# >>> home-labs-sync (zarządzane przez dodatek Home Labs Sync — nie edytuj tego bloku)
homeassistant:
  packages:
    home_labs: !include home_labs/package.yaml
# <<< home-labs-sync
```

W wariancie c) wklej pod istniejącym `homeassistant:`:

```yaml
homeassistant:
  # ...to, co już masz...
  packages:
    home_labs: !include home_labs/package.yaml
```

a jeśli `packages:` już istnieje jako lista wpisów — dopisz tylko wiersz `home_labs: !include home_labs/package.yaml` na tym samym poziomie co inne pakiety. Po wklejeniu uruchom ponownie Home Assistant. Powiadomienie znika samo przy następnej synchronizacji.

Dodatkowo, gdy zakres `themes` jest włączony, a w `configuration.yaml` nie ma `frontend: themes:`, dodatek prosi o dodanie:

```yaml
frontend:
  themes: !include_dir_merge_named themes
```

Jeśli `configuration.yaml` nie da się sparsować (błąd składni), dodatek nic w nim nie zmienia i zgłasza to w panelu.

### Kolizje z wcześniej wklejonym blokiem `lovelace:` (migracja obecnych klientów)

Do tej pory dashboardy Home Labs rejestrowano ręcznie blokiem `lovelace: dashboards:` w `configuration.yaml`. Home Assistant nie pozwala zdefiniować tego samego dashboardu dwa razy, więc:

1. Przy synchronizacji dodatek sprawdza, które klucze `lovelace.dashboards.*` już są w `configuration.yaml`, i **pomija je** w swoim pakiecie. Dashboardy nadal działają (po staremu).
2. W panelu widać „Kolizje”, a w Home Assistant pojawia się powiadomienie „Home Labs Sync: kolizja w configuration.yaml”.
3. Po pierwszej udanej synchronizacji **usuń stary blok `lovelace:`** z `configuration.yaml` (blok `# >>> home-labs-sync … # <<< home-labs-sync` zostaw).
4. Kliknij **Synchronizuj teraz** w panelu dodatku (albo poczekaj na kolejny cykl). Dodatek zauważy, że kolizji już nie ma, dopisze dashboardy do pakietu `home_labs/package.yaml` i zgłosi „wymagany restart”.
5. Dopiero teraz uruchom ponownie Home Assistant. Restart **przed** krokiem 4 zostawia HA bez dashboardów Home Labs (stary blok już usunięty, pakiet jeszcze bez nich) — wtedy po prostu wykonaj krok 4 i zrestartuj jeszcze raz.

### Samonaprawa

Każdy cykl (także gdy serwer odpowiada „bez zmian”) porównuje pliki na Home Assistant i pakiet `home_labs` z ostatnim znanym wydaniem: skasowany albo zmieniony przez kogoś plik z wydania wraca, pakiet jest składany na nowo, a `configuration.yaml` sprawdzany pod kątem brakujących linii i kolizji. Własne pliki klienta (spoza wydania) nie są ruszane. **Synchronizuj teraz** zawsze pobiera pełny manifest z serwera, nawet gdy nic się tam nie zmieniło.

## Co widzi klient

- Dashboardy Home Labs są w menu bocznym jak dotychczas; zmiany w plikach dashboardów widać po odświeżeniu przeglądarki, bez restartu.
- Automatyzacje Home Labs są w **Ustawienia → Automatyzacje** — można je włączać/wyłączać, uruchamiać i oglądać ślady, ale są **tylko do odczytu** w edytorze (ładowane z pakietu). Poprawki robi Home Labs i publikuje nowe wydanie.
- Automatyzacje i sceny klienta (własne, tworzone w UI) pozostają w `automations.yaml` / `scenes.yaml` i **nie są ruszane**. Dodatek usuwa z tych plików wyłącznie wpisy o identyfikatorach wskazanych przez Home Labs (te, które wcześniej były wklejane ręcznie), a przed usunięciem robi kopię w danych dodatku.
- Zdjęcia wygaszacza WallPanel na tabletach zmieniają się same po publikacji nowego zestawu — bez restartu i bez klikania.
- Sensor `sensor.home_labs_sync` (stan `ok` / `noop` / `error` / `paused` / `dry_run`) z atrybutami: identyfikator wydania, commit, data publikacji, czas ostatniej synchronizacji, czy wymagany restart.

## Pakiety (zakres `packages`)

Część konfiguracji Home Assistant nie jest ani dashboardem, ani automatyzacją: encja `climate` sterująca klimatyzatorem przez IR (platforma `climate_template` z HACS), skrypty, pomocnicy (`input_boolean`, `input_number` …), grupa powiadomień `notify`. Home Labs trzyma je w repozytorium w `clients/<slug>/packages/*.yaml` (każdy plik to zwykły pakiet HA), a serwer łączy je w jeden fragment YAML. Dodatek dokleja ten fragment do pakietu `home_labs/package.yaml` — **`configuration.yaml` się nie zmienia**, niezależnie od wariantu dołączenia pakietu.

| Sytuacja | Co robi dodatek |
|---|---|
| wydanie zawiera pakiety, zakres `packages` włączony | dokleja je do pakietu `home_labs`; zmiana treści pakietów = **wymagany restart** HA (nowe integracje ładują się tylko przy starcie) |
| pakiet usunięty z repo | znika z pakietu `home_labs` przy najbliższym wydaniu; też wymaga restartu |
| zakres `packages` wyłączony | pakiety pominięte; ostrzeżenie w panelu i raporcie |
| integracja wymaga HACS (np. `climate_template`) | dodatek jej nie instaluje — bez niej „Sprawdź konfigurację” zgłosi błąd, a automatyczny restart się nie wykona |

Zasady (sprawdza je serwer Home Labs, a dodatek jeszcze raz przy każdym manifeście — naruszenie = odrzucony manifest):

- dozwolony tylko tag `!secret`; `!include`, `!include_dir_*`, `!env_var` są odrzucane,
- zakazane integracje: `homeassistant`, `lovelace`, `frontend`, `http`, `shell_command`, `command_line`, `python_script`, `pyscript` (także z etykietą, np. `command_line nasz:`),
- zarezerwowane klucze `automation home_labs` i `scene home_labs` (dodaje je sam dodatek).

Pakiety wymagają dodatku w wersji **0.3.0 lub nowszej** — starszy odrzuci manifest z pakietami w całości.

## Zdjęcia wygaszacza (zakres `media`)

Zdjęcia, które WallPanel pokazuje na tabletach jako wygaszacz, leżą w repozytorium Home Labs w `clients/<slug>/media/wallpanel/`. Po publikacji dodatek zapisuje je w `/media/wallpanel/` na Home Assistant — to katalog **mediów** HA (montowanie `media` dodatku), nie katalog konfiguracji. WallPanel czyta je stamtąd (`media-source://media_source/local/wallpanel`), więc nowy zestaw pojawia się na tabletach sam, bez przeładowania i bez restartu.

| Sytuacja | Co robi dodatek |
|---|---|
| brak katalogu `/media/wallpanel/` | tworzy go |
| zdjęcie dodane lub zmienione w repo | zapisuje (weryfikacja SHA-256, zapis atomowy, jak dla plików konfiguracji) |
| zdjęcie usunięte z repo | usuwa z `/media/wallpanel/` — **tylko** plik, który sam wcześniej zapisał i którego nikt lokalnie nie zmienił (kopia w danych dodatku) |
| własne zdjęcia klienta w `/media/wallpanel/` | nie rusza ich; zdjęcie Home Labs podmienione ręcznie przez klienta też zostaje (dodatek przestaje nim zarządzać) |
| inne katalogi w `/media` | nigdy ich nie czyta ani nie zapisuje |
| zakres `media` wyłączony w `scopes` | zdjęcia zostają na miejscu, dodatek przestaje nimi zarządzać — wariant dla klienta, który sam dba o zdjęcia |
| `/media` nie jest zamontowany (starszy Supervisor) | zgłasza błąd w panelu i raporcie (status „częściowo”), pozostałe zakresy synchronizuje normalnie; rozwiązanie: aktualizacja Home Assistant albo wyłączenie zakresu `media` |

W raporcie i panelu pliki mediów są oznaczone jako `media:wallpanel/…`, żeby odróżnić je od plików konfiguracji. Limity: 10 MB na zdjęcie, 150 MB na całe wydanie.

## Restart Home Assistant

Restart jest potrzebny tylko przy dodaniu lub zmianie rejestracji dashboardu (sekcja `lovelace` pakietu) albo po dopisaniu bloku bootstrap. Automatyzacje, sceny i motywy dodatek przeładowuje sam.

| `restart_policy` | Zachowanie |
|---|---|
| `never` | informacja tylko w raporcie do Home Labs i w panelu |
| `notify` | powiadomienie trwałe „Home Labs Sync: wymagany restart Home Assistant”; znika po restarcie |
| `auto` (domyślnie) | restart w oknie `restart_window`, jeśli **Sprawdź konfigurację** zwróciło OK; najwyżej raz na wydanie; poza oknem — powiadomienie jak w `notify` |

## Eksport encji

Home Labs potrzebuje listy encji domu (identyfikatory, nazwy, obszary, piętra, klasy urządzeń, jednostki, aktualne stany), żeby przygotować dashboardy i automatyzacje. Eksport można uruchomić:

- przyciskiem **Eksportuj encje teraz** w panelu,
- według harmonogramu (`entities_export_schedule` + `entities_export_hour`),
- na prośbę Home Labs zgłoszoną przez serwer (dodatek wykona ją raz przy najbliższej synchronizacji).

Szablon jest renderowany przez Home Assistant, wynik (CSV z nagłówkiem `entity_id;name;area;floor;device_class;unit;state`) trafia bezpośrednio na serwer Home Labs. **Nic nie jest zapisywane lokalnie.** CSV nie zawiera haseł, tokenów ani historii — tylko bieżące stany encji. Wynik ostatniego eksportu widać w panelu.

Wskazówka: przed eksportem przypisz pomieszczeniom piętra (Ustawienia → Obszary), wtedy kolumna `floor` będzie wypełniona.

## Pierwszy start z `dry_run: true`

W trybie próbnym dodatek pobiera manifest, liczy plan (które pliki zapisze, które usunie, czy dopisze blok do `configuration.yaml`, czy potrzebny będzie restart) i pokazuje go w panelu oraz wysyła w raporcie z flagą `dry_run`. Nie pobiera plików, nie zapisuje nic w `/config`, nie przeładowuje niczego w Home Assistant. Gdy plan wygląda dobrze — wyłącz `dry_run` i kliknij **Synchronizuj teraz**.

## Model zaufania

- Dodatek zapisuje **wyłącznie** w: `dashboards/`, `www/`, `themes/`, `home_labs/`, pliku pakietu (`home_labs/package.yaml` albo `<katalog pakietów>/home_labs.yaml`), w `/media/wallpanel/` (zakres `media`) oraz — tylko przez usunięcie wpisów Home Labs — w `automations.yaml` i `scenes.yaml`. Do `configuration.yaml` może wyłącznie dopisać oznaczony blok (po kopii).
- Nigdy nie czyta ani nie zapisuje `secrets.yaml`, `.storage/`, plików poza katalogiem konfiguracji ani niczego w `/media` poza `wallpanel/`. Każda ścieżka z serwera jest sprawdzana osobno dla katalogu docelowego (brak `..`, brak ścieżek bezwzględnych, tylko dozwolone katalogi — dla mediów wyłącznie `wallpanel/`); manifest z niedozwoloną ścieżką jest odrzucany w całości.
- Pliki są pobierane do katalogu tymczasowego, weryfikowane sumą SHA-256 i dopiero wtedy — wszystkie albo żadne — przenoszone do `/config` lub `/media/wallpanel`. Limity: 10 MB na plik, 150 MB na wydanie.
- Nic pobranego z serwera nie jest wykonywane przez dodatek. Pakiet składa dodatek lokalnie i przyjmuje z serwera tylko sekcję `lovelace` (bez tagów YAML) oraz — w zakresie `packages` — konfigurację HA bez integracji uruchamiających polecenia (`shell_command`, `command_line`, `python_script`, `pyscript`) i bez zmian w `homeassistant:` / `http:` (tylko tag `!secret`).
- Token daje dostęp tylko do konfiguracji tego jednego domu i można go unieważnić w panelu Home Labs. Raport zwrotny zawiera: wersję dodatku i Home Assistant, identyfikator wydania, status, listę zapisanych/usuniętych plików, błędy, wynik „Sprawdź konfigurację” — bez danych osobowych.
- Kopie zapasowe (nadpisane pliki klienta, `configuration.yaml` przed dopisaniem bloku, `automations.yaml` / `scenes.yaml` przed przejęciem, usunięte zdjęcia wygaszacza w podkatalogu `media/`) leżą w danych dodatku (`/data/backup/<wydanie>/`), przechowywane są 2 ostatnie wydania.

## Rozwiązywanie problemów

| Objaw | Co sprawdzić |
|---|---|
| Panel: „Brak tokenu API” / „Serwer odrzucił token” | Skopiuj token ponownie z panelu Home Labs (Konfiguracja → `api_token`), zapisz, zrestartuj dodatek. |
| Panel: „serwer niedostępny” | Chwilowy brak internetu lub przerwa u Home Labs — dodatek spróbuje za `interval_minutes`. Błąd pokazuje się dopiero po 3 nieudanych próbach z rzędu. |
| „wstrzymane w panelu Home Labs” | Home Labs celowo wstrzymał synchronizację tego domu. |
| Bootstrap: „do uzupełnienia ręcznie” | Wklej podane linie do `configuration.yaml`, uruchom ponownie HA. |
| Sprawdzenie konfiguracji: błąd | Treść błędu jest w panelu i w raporcie; zwykle problem leży w innym fragmencie konfiguracji — Home Labs to widzi w raporcie. |
| Dashboard nie pojawia się w menu | Sprawdź, czy panel nie pokazuje „wymagany restart Home Assistant”. |
| „Katalog mediów /media nie istnieje” (status „częściowo”) | Supervisor nie zamontował `/media` — zaktualizuj Home Assistant; jeśli zdjęcia wygaszacza nie są potrzebne, wyłącz zakres `media` w `scopes`. Pozostałe zakresy działają. |
| Zdjęcia wygaszacza się nie zmieniają | Sprawdź, czy zakres `media` jest włączony i czy w panelu na liście „Zapisane pliki” są wpisy `media:wallpanel/…`. WallPanel musi wskazywać `media-source://media_source/local/wallpanel`. |
| Pakiety się nie pojawiają / ostrzeżenie „zakres 'packages' jest wyłączony” | Konfiguracja dodatku → `scopes` → dopisz `packages`, zapisz, **Synchronizuj teraz**, potem restart HA. |
| Po pakietach „Sprawdź konfigurację”: błąd „Integration … not found” | Pakiet używa integracji z HACS, której nie ma (np. Template Climate) — zainstaluj ją w HACS i zrestartuj HA. |
| Automatyzacja Home Labs „nie do edycji” | To normalne — jest ładowana z pakietu. Można ją włączać/wyłączać. |

Dziennik: zakładka **Dziennik** dodatku (albo ostatnie 20 wpisów w panelu). Przy zgłoszeniu do Home Labs ustaw `log_level: debug` i podaj identyfikator wydania z panelu. Stan dodatku widać też w `sensor.home_labs_sync`.
