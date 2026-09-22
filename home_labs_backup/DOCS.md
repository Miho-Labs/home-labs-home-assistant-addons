# Home Labs Backup

Kopie zapasowe Home Assistant w chmurze Home Labs — **natywnie**, jako lokalizacja kopii w
**Ustawienia → System → Kopie zapasowe**, tak jak wbudowane lokalizacje chmurowe (np. Cloudflare R2).
Harmonogram, liczbę przechowywanych kopii i odtwarzanie obsługuje sam Home Assistant.

## Jak to działa

| Element | Rola |
|---|---|
| Dodatek **Home Labs Backup** | instaluje integrację `home_labs_backup` do `/config/custom_components/` i przekazuje jej adres serwera i token (discovery Supervisora); co 6 h sprawdza, czy wszystko jest na miejscu |
| Integracja **Home Labs Backup** | rejestruje lokalizację kopii „Home Labs (chmura)”; wysyła, listuje, pobiera i usuwa kopie |
| Serwer Home Labs | sprawdza token, pilnuje limitów i wydaje jednorazowe adresy do magazynu |
| Magazyn (Cloudflare R2) | przechowuje pliki kopii; dane idą z Twojego HA prosto tutaj |

Token daje dostęp **wyłącznie** do kopii tego domu. Dodatek ani integracja nie znają żadnych kluczy
do magazynu — każda część kopii idzie pod adres ważny godzinę, wydany tylko dla tego domu.

## Instalacja

1. **Ustawienia → Dodatki → Sklep z dodatkami → ⋮ → Repozytoria** → dodaj
   `https://github.com/Miho-Labs/home-labs-home-assistant-addons`.
2. Zainstaluj **Home Labs Backup**.
3. **Konfiguracja** → `api_token`: token z panelu Home Labs (ten sam co w Home Labs Sync albo osobny,
   z zakładki *Tokeny*). Zapisz.
4. **Uruchom** dodatek. W powiadomieniach pojawi się prośba o restart.
5. **Ustawienia → System → Uruchom ponownie** Home Assistant.
6. **Ustawienia → System → Kopie zapasowe → Konfiguruj automatyczne kopie**: zaznacz lokalizację
   **Home Labs (chmura)**, ustaw harmonogram i liczbę kopii do zachowania.
7. Zostaw **szyfrowanie włączone** dla tej lokalizacji i zapisz klucz szyfrowania (Home Labs ma go
   w danych instalacji). Bez klucza kopii nie da się odtworzyć.

## Opcje

| Opcja | Domyślnie | Opis |
|---|---|---|
| `api_url` | `https://public.home-labs.pl` | adres serwera Home Labs — zmieniaj tylko na prośbę Home Labs |
| `api_token` | — | token z panelu Home Labs (`hls_…`) |
| `log_level` | `info` | `debug`, `info`, `warning` |

Zmiana tokenu: wklej nowy i uruchom dodatek ponownie — integracja przejmie go sama, bez restartu HA.

## Zasady po stronie Home Labs

| Zasada | Co zobaczysz |
|---|---|
| Tylko kopie **szyfrowane** | kopia bez szyfrowania kończy się błędem wysyłania z prośbą o włączenie szyfrowania |
| Limit na kopię i na dom (domyślnie 10 GB / 20 GB) | błąd „Brak miejsca w chmurze Home Labs” — zmniejsz liczbę przechowywanych kopii |
| Home Labs może wyłączyć kopie w chmurze dla domu | nowe kopie są odrzucane; istniejące da się dalej pobrać i odtworzyć |

## Odtwarzanie

**Ustawienia → System → Kopie zapasowe** → wybierz kopię z lokalizacji „Home Labs (chmura)” →
**Przywróć**. Przy nowej instalacji HA: najpierw zainstaluj ten dodatek (kroki 1–5), potem kopie
pojawią się na liście. Potrzebny jest klucz szyfrowania.

## Rozwiązywanie problemów

| Objaw | Co zrobić |
|---|---|
| Brak lokalizacji „Home Labs (chmura)” | sprawdź log dodatku; uruchom ponownie HA po pierwszej instalacji |
| Powiadomienie „token odrzucony” | wygeneruj nowy token w panelu Home Labs, wklej, uruchom dodatek ponownie |
| Integracja „Nie udało się skonfigurować” | brak połączenia z serwerem — HA spróbuje ponownie sam |
| Brak dodatku, a integracja potrzebna (tryb ręczny) | Ustawienia → Urządzenia i usługi → Dodaj integrację → Home Labs Backup → adres i token |

## Model zaufania

Dodatek zapisuje wyłącznie katalog `custom_components/home_labs_backup` (podmieniany w całości, tylko
gdy zmieniła się jego treść). Nie czyta i nie zapisuje niczego innego w konfiguracji. Kopie wysyła
Home Assistant przez integrację; ich zawartość jest szyfrowana kluczem, którego serwer Home Labs nie
dostaje. Po odinstalowaniu dodatku usuń też integrację (Ustawienia → Urządzenia i usługi).
