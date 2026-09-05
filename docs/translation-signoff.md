# Alert translation sign-off register

Alert text is safety-critical. A mistranslated evacuation instruction is worse
than no instruction at all, because it will be acted on.

No language below has been reviewed by a native speaker. `REVIEWED` in
`backend/app/services/i18n.py` lists only English, which is the language the
bulletins are authored in and therefore needs no translation review.

**Do not add a language to `REVIEWED` until the row below is complete and
signed.** `GET /api/v1/alerts/languages` reports this state, and the Alerts page
shows it to every operator, so an unreviewed translation is visible rather than
silently trusted.

| Code | Language | States served | Reviewer | Organisation | Date | Status |
| --- | --- | --- | --- | --- | --- | --- |
| `en` | English | all | - | source language | - | Not required |
| `hi` | Hindi | all | | | | **Pending** |
| `as` | Assamese | Assam | | | | **Pending** |
| `bn` | Bengali | Tripura, Barak valley | | | | **Pending** |
| `mni` | Meiteilon (Manipuri) | Manipur | | | | **Pending** |
| `kha` | Khasi | Meghalaya | | | | **Pending** |
| `lus` | Mizo (Duhlian) | Mizoram | | | | **Pending** |
| `ne` | Nepali | Sikkim | | | | **Pending** |

## What review must cover

1. **The four risk-level words** (`LEVEL_WORDS`) — these appear in the SMS
   subject position and carry the urgency.
2. **The four advisory actions per level** (`ACTION_TEMPLATES`) — the
   `critical` action instructs evacuation and must be unambiguous.
3. **The SMS body template** (`SMS_TEMPLATES`) — check that the placeholder
   order still reads naturally once `{location}` and `{district}` are English
   proper nouns embedded in a non-English sentence.
4. **Register and dialect** — Mizo `lus` is Duhlian; Khasi has significant
   dialectal variation across the Khasi, Jaintia and War areas; Meiteilon is
   given here in Bengali script and may need Meitei Mayek for some audiences.
5. **Length** — any non-Latin character forces UCS-2 encoding, cutting SMS
   payload from 160 to 70 characters per part. Check
   `i18n.estimate_sms_parts()` output for each rendered bulletin.

## Additional languages to consider

Bodo, Karbi, Dimasa, Nyishi, Adi, Ao and Angami all have substantial speaker
populations in monitored districts and are not yet represented.
