# Changelog

## 0.1.1

- Ikona Home Labs przy lokalizacji kopii „Home Labs (chmura)” i przy integracji (obrazy marki w `brand/` integracji; Home Assistant 2026.3 lub nowszy). Po aktualizacji dodatku uruchom ponownie Home Assistant.
- Logo dodatku w sklepie: „Home Labs Backup” zamiast „Home Labs Sync”.

## 0.1.0

- Pierwsza wersja: dodatek instaluje integrację `home_labs_backup`, która dodaje lokalizację kopii zapasowych „Home Labs (chmura)” w Ustawienia → System → Kopie zapasowe.
- Kopie trafiają prosto do magazynu Home Labs (Cloudflare R2) przez jednorazowe, krótko ważne adresy; token daje dostęp tylko do kopii tego domu.
- Przyjmowane są wyłącznie kopie szyfrowane.
