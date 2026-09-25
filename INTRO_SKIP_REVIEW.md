# Review: „Vorspann überspringen" (Intro-Skip), v0.1.65

**Reviewer:** Opus 5 · **Datum:** 2026-09-25 · **Grundlage:** `INTRO_SKIP_PLAN.md`,
`git diff HEAD` im Worktree `feature/intro-skip-clients` (Basis `a95b815`, v0.1.64)

## Status: **OK — keine Korrektur nötig**

Der Plan wurde vollständig und wortgetreu umgesetzt. Ich habe **keine Änderung am
Code vorgenommen** — es gab nichts zu beheben. Die einzige Abweichung vom
Plan-Wortlaut ist eine **Verbesserung** (siehe Punkt 1) und bleibt so stehen.

⚠ **Ungeprüft auf echtem GTK.** In dieser Sandbox gibt es weder eine GTK4-Laufzeit
noch die Erlaubnis, `python3` auszuführen — `py_compile`, `pyflakes` und die
Logikprüfung `intro_markers` aus Plan 8b konnten **nicht** laufen. Die Prüfung war
eine sorgfältige manuelle Code-Lektüre. Details unter „Offene Punkte".

---

## 1. Abweichung vom Plan — bewusst und richtig

**Trailer-Behandlung in `_request_intro_markers` (player_window.py:664–676).**

Der Plan (§2e) liest erst die Marken aus `self.item` und prüft **danach** auf
`direct_url`:

```python
start, end = intro_markers(self.item)
self._apply_intro_markers(start, end, token)
if self.direct_url is not None:
    return
```

Damit widerspricht der Plan-Code seiner eigenen Randfall-Tabelle (§3: „Trailer
(`direct_url`) → nie ein Knopf"). Nachgeprüft: `detail_page._trailer_ready`
(detail_page.py:612–621) übergibt `self.item` — also das **Film**-Item — an
`open_player(..., direct_url=...)`. Trägt dieses Item bereits
`introStartSec`/`introEndSec`, wäre der Knopf **während des Trailers** erschienen
und hätte an eine Sekunde des Films gesprungen. Die Umsetzung zieht die
`direct_url`-Prüfung vor den `intro_markers`-Aufruf und setzt für Trailer explizit
`(None, None)`. Das ist die vom Plan **gemeinte** Semantik — korrekt so, mit
treffendem Kommentar dokumentiert.

Zweite, harmlose Abweichung: Die Skill-Zeile in
`.claude/skills/goldfishlinux-overview/SKILL.md` endet zusätzlich mit
„**Ungeprüft auf echtem GTK**" — genau, was Plan §8f verlangt. Gut.

---

## 2. Akzeptanzkriterien (Plan §7) — Einzelprüfung

| # | Kriterium | Ergebnis |
|---|---|---|
| 1 | `intro_markers()` auf **Modulebene**, `is None`-Prüfungen, `(0.0, x)` bleibt gültig | ✅ api.py:165–190, direkt zwischen `parse_trickplay_vtt` und `class GoldfishClient` |
| 2 | Docstring an `GoldfishClient.item()` | ✅ api.py:439–444 |
| 3 | Import von `intro_markers` | ✅ player_window.py:48 |
| 4 | `Gtk.Button`, Label exakt `Vorspann überspringen`, `.gf-intro-skip`, END/END, `visible=False` | ✅ player_window.py:410–423 |
| 5 | **Genau einmal** geparentet, per `add_overlay` in `_build_ui`, kein Kind von `self.bar` | ✅ player_window.py:347 — `grep` über alle `intro`-Vorkommen zeigt keinen `bar.append` |
| 6 | Sichtbarkeit nur in `_tick`, mit der korrigierten `position` | ✅ player_window.py:912, `position` stammt aus Zeile 879 (`_virtual_offset + timestamp`) |
| 7 | Bedingung `start is not None and end is not None and start <= position < end` | ✅ player_window.py:717, wortgleich |
| 8 | Aufruf in `_start_playback`, Netzaufruf im Daemon-Thread, Rückweg per `idle_add`, `except Exception` ohne UI-Meldung | ✅ 599, 681–692 |
| 9 | Trailer / `local` / nicht-positive Kennung lösen keine Abfrage aus | ✅ 666–678 (Trailer sogar strenger als geplant, s. o.) |
| 10 | `_switch_to` setzt zurück + Token; `_on_close_request` erhöht Token | ✅ 1490–1493, 1574 |
| 11 | `_skip_intro` versteckt zuerst, `end is None` → nichts | ✅ 736–740 |
| 12 | Keine Datei außerhalb der Plan-Tabelle geändert | ✅ genau die 7 Dateien; einzige untracked-Datei ist `INTRO_SKIP_PLAN.md` selbst |
| 13 | `0.1.65` in `__init__.py`, `pyproject.toml`, `debian/changelog` | ✅ alle drei, im selben Diff |
| 14 | Changelog umlautfrei, erster Satz trägt die Aussage | ✅ „ueberspringen", „waehrend"; erster Satz allein aussagekräftig. Datum geprüft: **2026-09-25 ist tatsächlich ein Freitag**, Signaturzeile formatgleich zu den Bestandseinträgen |
| 15 | README-Stichpunkt + Skill-Zeile vorhanden, `CLAUDE.md` unverändert | ✅ |

## 3. Zusätzlich geprüft (über die Plan-Liste hinaus)

* **Reihenfolge im Konstruktor:** `_build_ui()` (Zeile 280) läuft vor
  `_start_playback()` (296) — `self.intro_button` existiert also garantiert, bevor
  `_request_intro_markers` → `_apply_intro_markers` → `_set_intro_visible` erstmals
  darauf zugreift. Kein `AttributeError` beim Start.
* **Genau ein `client.item()`-Aufruf je Titel:** `_start_playback()` wird nur an
  zwei Stellen gerufen (Konstruktor, `_switch_to`). Der Fehler-Wiederholungspfad
  (`_retry_playback` → `_play_uri`) löst **keine** weitere Abfrage aus. Die
  AGENTS.md-Regel „nicht in Schleifen über viele Items" ist eingehalten; `/api/playback/`
  wird nicht zusätzlich angefasst.
* **Umwandlungsfall nachgerechnet:** `seek_to(end)` setzt bei Transcode über
  `_play_uri(uri, target)` den `_virtual_offset` auf `end`. Der nächste Takt rechnet
  `position = end + 0` → `start <= end < end` ist **False**. Der Knopf taucht nach
  dem Sprung also nicht wieder auf. Gleiches gilt bei direkter Wiedergabe.
* **Fehlerseite:** `_show_load_error` ersetzt den kompletten Fensterinhalt
  (`set_content`), das Overlay samt Knopf ist damit weg — kein „schwebender Knopf über
  der Fehlermeldung", auch wenn der Fehler mitten im Vorspann auftritt.
* **Kein Dialog, kein zweites Fenster, kein `set_transient_for`, kein `Adw.Spinner`,
  keine neue Tastenkombination, kein Einstellungsschalter, keine neue Datei.**
  Alle harten Verbote aus AGENTS.md eingehalten.
* **Zeitleisten-Regression (v0.1.53):** Der Knopf ist Overlay-Kind, kein Kind von
  `self.bar`; `set_measure_overlay` wird korrekt **nicht** gerufen. Der Layout-Pfad der
  Steuerleiste ist unberührt. (Optische Endkontrolle trotzdem am Gerät, s. u.)
* **CSS-Priorität:** `_ensure_css` lädt mit `STYLE_PROVIDER_PRIORITY_APPLICATION`
  (600) — `background-color`, `border-radius` und `font-size` der neuen Regel setzen
  sich gegen das Theme durch.
* **Thread-Sicherheit:** Der Worker liest nur das lokale `token`/`item_id`, schreibt
  nichts direkt in den Zustand, Rückweg ausschließlich über `GLib.idle_add`. Muster
  identisch zu `_refresh_autoplay_pref`/`_lookup_next_episode`.
* **Repo-Sichtbarkeit:** Keine echten Namen, IPs oder Zugangsdaten im Diff. Die
  Changelog-Signatur ist die bestehende, unveränderte Zeile des Projekts.

## 4. Gefundene Kleinigkeiten — bewusst **nicht** geändert

Nichts davon ist ein Fehler; ich liste es, damit es nicht als übersehen gilt.

1. **Theme-Rahmen am Knopf (rein optisch).** `.gf-intro-skip` überschreibt
   `background-color` und `border-radius`, nicht aber `box-shadow`/`border` des
   libadwaita-Knopf-Stils. Je nach Theme kann die Pille eine feine Umrandung
   behalten. Ob das stört, lässt sich nur am Bildschirm entscheiden — deshalb keine
   Blindkorrektur, sondern Sichtprüfung (Plan 8d). Fix bei Bedarf: `box-shadow: none;
   border: none;` in die CSS-Regel.
2. **`float("nan")`** würde `intro_markers` passieren (`nan < 0` und `nan <= nan` sind
   beide False) und `(nan, nan)` liefern. Folge: der Vergleich in `_update_intro_skip`
   ist immer False, es gibt schlicht nie einen Knopf. Harmlos, JSON kann `NaN`
   regulär gar nicht transportieren — kein Handlungsbedarf.
3. **Nicht spulbares direktes Medium:** Klickt man den Knopf und liefert
   `media.is_seekable()` False, versteckt sich der Knopf und erscheint beim nächsten
   Takt wieder, weil die Position stehen blieb. Vorbestehendes Verhalten von
   `seek_to`, laut Plan §6 nicht anzufassen.
4. **Lücke in der Chronik-Tabelle:** Die Skill-Tabelle springt jetzt von 0.1.56 auf
   0.1.65 — die Versionen 0.1.57–0.1.64 fehlen dort schon vorher. Vorbestehend, nicht
   Teil dieses Features.

## 5. Offene Punkte für die menschliche Abnahme

**Nicht ausgeführte Prüfschritte** (Sandbox ohne GTK4 und ohne Erlaubnis,
`python3` zu starten — beide Aufrufe wurden abgelehnt):

- [ ] **Plan 8a:** `python3 -m py_compile goldfish_linux/api.py goldfish_linux/windows/player_window.py`
      und dasselbe mit `pyflakes`. **Nicht gelaufen.** Ersatzweise vollständige
      manuelle Lektüre beider Hunks: Einrückung, Klammern, Dekoratoren, Typannotationen
      (`float | None` — im Projekt bereits üblich) und alle verwendeten Namen sind
      stimmig; der neue Import ist tatsächlich benutzt (pyflakes-sauber zu erwarten).
- [ ] **Plan 8b:** Logikprüfung von `intro_markers` mit den acht Fällen. **Nicht
      gelaufen**, aber Fall für Fall von Hand durchgerechnet — alle acht erwarteten
      Ergebnisse stimmen, insbesondere `{"introStartSec": 0, "introEndSec": 45}` →
      `(0.0, 45.0)`.
- [ ] **Plan 8c:** `timeout 20 python3 -m goldfish_linux` (Exit **124** = gut).
- [ ] **Plan 8d:** Treiberskript unter `/tmp/`, alle sieben Sichtprüfungen —
      besonders **Nr. 5, der Zeitleisten-Regressionstest** (Griffposition und
      Balkenbreite müssen bei sichtbarem und unsichtbarem Knopf identisch sein) und
      **Nr. 6** (`introStartSec = 0` muss den Knopf ab Sekunde 0 zeigen).
- [ ] **Plan 8e:** Gegen den echten Server, eine Serienfolge mit erkanntem Vorspann,
      einmal Qualität „Original" (direkt) und einmal kleinere Stufe (Umwandlung). Der
      Umwandlungsfall ist der eigentlich interessante — nur dort greift die
      `_virtual_offset`-Korrektur.

**Fachlich zu bestätigen:**

- [ ] **Feldnamen `introStartSec` / `introEndSec`** konnten nicht gegen den Server
      gegengeprüft werden — das Repo `goldfish` liegt in dieser Sandbox nicht vor. Vor
      dem Release einmal `GetItemFor` im Server-Repo ansehen und bestätigen, dass die
      JSON-Tags genau so heißen (AGENTS.md: „Bei jeder Server-API-Änderung `api.py`
      gegenprüfen").
- [ ] **Optik des Knopfes** (Punkt 4.1) und die Position unten rechts im Vollbild —
      kollidiert sie mit nichts, wenn gleichzeitig Untertitel laufen?
- [ ] Ob der Knopf bei einer heruntergeladenen Datei ohne Netz wirklich still
      ausbleibt (Abfrage scheitert im Worker, keine Fehlermeldung).

**Nicht committet, nicht gepusht** — wie beauftragt.
