# Implementierungsplan: „Vorspann überspringen" (Intro-Skip) im GTK4-Client

**Zielversion:** 0.1.65
**Betroffene Dateien (vollständige Liste — nichts anderes anfassen):**

| # | Datei | Art der Änderung |
|---|---|---|
| 1 | `goldfish_linux/api.py` | neue Modulfunktion `intro_markers()`, Docstring an `item()` |
| 2 | `goldfish_linux/windows/player_window.py` | CSS-Klasse, Zustandsfelder, Overlay-Knopf, Abfrage, Sichtbarkeitslogik, Sprung, Zurücksetzen |
| 3 | `goldfish_linux/__init__.py` | `__version__ = "0.1.65"` |
| 4 | `pyproject.toml` | `version = "0.1.65"` |
| 5 | `debian/changelog` | neuer Eintrag `0.1.65-1` |
| 6 | `README.md` | ein Stichpunkt im Abschnitt „### Wiedergabe" |
| 7 | `.claude/skills/goldfishlinux-overview/SKILL.md` | eine Zeile in der Chronik-Tabelle |

**Sprache:** Die App ist einsprachig deutsch, es gibt **kein** gettext/i18n und
keine Locale-Dateien (geprüft: kein `gettext`, kein `_("…")` im ganzen
Paket). Alle Beschriftungen stehen als deutsche Literale direkt im Code — also
auch hier. Knopftext exakt: `Vorspann überspringen`.

---

## 0. Was der Server liefert (Grundlage, nicht ändern)

* `GET /api/items/{id}` (Server-Handler `GetItemFor`) liefert im Item-JSON
  zusätzlich `introStartSec` und `introEndSec` (float, Sekunden, **absolute**
  Position im Video).
* Beide sind `null` bzw. fehlen, wenn keine Erkennung existiert oder kein
  Treffer gefunden wurde.
* **Nur dieser Endpunkt trägt die Felder.** Die Listen-Endpunkte (`/api/items`,
  Startseite, Sammlungen, Playlists, Staffeln) tragen sie **nicht** — und genau
  von dort kommen die Item-Dicts, die `open_player()` heute bekommt
  (`browse_page.py`, `home_page.py`, `seasons_page.py`, `playlists_page.py`,
  `detail_page.py`). Der Player muss die Marken deshalb **selbst** über
  `client.item(item_id)` nachladen.
* Die Marken sind absolut. Bei serverseitiger Umwandlung zählt `Gtk.MediaFile`
  ab dem Anfang der LAUFENDEN Umwandlung — der Client hat dafür bereits
  `self._virtual_offset` und rechnet in `_tick()` (Zeile 727) die absolute
  Position aus. **Diese bereits korrigierte Position ist der Wert, der mit den
  Marken verglichen wird.** Es ist kein neues Offset-Konzept nötig; das
  Äquivalent des Browser-`virtualOffset` existiert schon.

---

## 1. `goldfish_linux/api.py` — Marken auslesen

### 1a. Neue Modulfunktion `intro_markers()`

**Wo:** direkt **nach** der bestehenden Modulfunktion `parse_trickplay_vtt()`
(endet um Zeile 168) und **vor** `class GoldfishClient` (ab Zeile 171). Die
Funktion gehört auf Modulebene, genau wie `parse_trickplay_vtt` — nicht in die
Klasse.

**Exakt einzufügender Code:**

```python
def intro_markers(item: dict) -> tuple[float | None, float | None]:
    """Anfang und Ende des erkannten Vorspanns aus einem Item-JSON.

    `introStartSec`/`introEndSec` liefert AUSSCHLIESSLICH
    `GET /api/items/{id}` (Server-Handler `GetItemFor`) — die
    Listen-Endpunkte (`/api/items`, Startseite, Sammlungen, Playlists,
    Staffeln) tragen sie nicht. Fehlt die Erkennung oder gab es keinen
    Treffer, kommen sie als `null`.

    Beide Werte sind ABSOLUTE Sekunden im Video, nicht relativ zu einer
    laufenden Umwandlung.

    Rückgabe `(None, None)`, wenn etwas fehlt oder unbrauchbar ist.
    ⚠ `0.0` ist ein GÜLTIGER Anfang (Vorspann ab der ersten Sekunde) und darf
    NICHT als „fehlt" durchgehen — deshalb wird überall auf `is None` geprüft
    und nie auf den Wahrheitswert.
    """
    start, end = item.get("introStartSec"), item.get("introEndSec")
    if start is None or end is None:
        return None, None
    try:
        start, end = float(start), float(end)
    except (TypeError, ValueError):
        return None, None
    if start < 0 or end <= start:
        return None, None
    return start, end
```

### 1b. Docstring an `item()`

**Wo:** `GoldfishClient.item()`, aktuell Zeile 409–410, bisher ohne Docstring.

**Vorher:**

```python
    def item(self, item_id: int) -> dict:
        return self.get(f"/api/items/{item_id}") or {}
```

**Nachher:**

```python
    def item(self, item_id: int) -> dict:
        """Ein einzelnes Item — der Player-Datenpfad (`GetItemFor`).

        Nur diese Antwort trägt `introStartSec`/`introEndSec` (Vorspann-
        Erkennung, siehe `intro_markers`); die Listen-Endpunkte kennen die
        Felder nicht. Ein Aufruf je geöffnetem Titel ist in Ordnung — NICHT in
        Schleifen über viele Items aufrufen."""
        return self.get(f"/api/items/{item_id}") or {}
```

**Sonst nichts an `api.py` ändern.** Insbesondere keinen neuen Endpunkt, keine
Dataclass und keine Änderung an `playback_info()`.

---

## 2. `goldfish_linux/windows/player_window.py` — die eigentliche Arbeit

Alle Zeilennummern beziehen sich auf den Stand von v0.1.64 (Commit `a95b815`).

### 2a. Import ergänzen

**Wo:** Zeile 48.

**Vorher:**

```python
from ..api import GoldfishAPIError, GoldfishClient, TrickplayCue  # noqa: E402
```

**Nachher:**

```python
from ..api import GoldfishAPIError, GoldfishClient, TrickplayCue, intro_markers  # noqa: E402
```

### 2b. CSS für den Knopf

**Wo:** im `_CSS`-Block (Zeile 90–117), **hinter** dem bestehenden Block
`.gf-autoplay label { … }` und **vor** dem schließenden `"""`.

**Einzufügen:**

```
.gf-intro-skip {
  background-color: alpha(#000000, 0.72);
  color: #ffffff;
  font-size: 1.05rem;
  padding: 10px 20px;
  border-radius: 999px;
}
.gf-intro-skip label { color: #ffffff; }
```

Begründung der Werte: gleiche Deckkraft und Textfarbe wie beim bestehenden
`.gf-autoplay`-Hinweis, damit beide Overlays wie ein Bausatz wirken; großer
Radius = Pille, wie im Browser.

### 2c. Zustandsfelder im Konstruktor

**Wo:** in `__init__`, **nach** dem Autoplay-Zustandsblock (endet Zeile 256 mit
`self._autoplay_token = 0`) und **vor** `self.set_default_size(1100, 680)`
(Zeile 258). Die Felder müssen vor `_build_ui()` und vor dem ersten `_tick`
existieren.

**Einzufügen:**

```python
        # Zustand für „Vorspann überspringen" (siehe _build_intro_overlay).
        # Die Marken kommen NUR über GET /api/items/{id} und sind None,
        # solange keine Erkennung vorliegt. `_intro_visible` spiegelt, was
        # gerade angezeigt wird, damit `_tick` nicht viermal je Sekunde
        # dasselbe `set_visible()` aufruft. `_intro_token` steigt bei JEDEM
        # Titelwechsel und beim Schließen: eine im Hintergrund laufende
        # Abfrage erkennt daran, dass ihr Ergebnis nicht mehr gilt (gleiche
        # Technik wie `_autoplay_token`).
        self._intro_start: float | None = None
        self._intro_end: float | None = None
        self._intro_visible = False
        self._intro_token = 0
```

### 2d. Den Knopf bauen und ins Overlay hängen

**Wo:** `_build_ui()`, Zeile 322–325. Nach
`self.overlay.add_overlay(self._build_autoplay_overlay())` eine Zeile ergänzen:

```python
        self.overlay.add_overlay(self._build_intro_overlay())
```

⚠ **Nur hier, genau einmal parenten.** Nicht anders herum, nicht später
umhängen — GTK verweigert ein zweites `set_child()`/Umparenten lautlos-kritisch
(siehe AGENTS.md, Ursache von v0.1.4).
⚠ **Kein `self.overlay.set_measure_overlay(...)`** aufrufen: Overlay-Kinder
werden in GTK4 standardmäßig NICHT in die Größenmessung einbezogen, und genau
das ist hier gewünscht (der Knopf darf die Mindestgröße des Fensters nicht
beeinflussen).

**Neue Methode** — direkt **nach** `_build_autoplay_overlay()` (endet Zeile 371)
und **vor** `_build_bar()` (Zeile 373) einfügen:

```python
    def _build_intro_overlay(self) -> Gtk.Widget:
        """Knopf „Vorspann überspringen" — unsichtbar, bis die Wiedergabe im
        erkannten Vorspann steht.

        Liegt IM selben Gtk.Overlay wie Untertitel, Ladekreisel und
        Nächste-Folge-Hinweis, also über dem Bild und unter der Steuerleiste —
        wie im Browser ein auffälliger Knopf IM Videobild.

        ⚠ **Er darf NICHT in die Steuerleiste (`self.bar`).** Dort verändert
        jedes Ein- und Ausblenden die Breite der Zeitleiste daneben (die hat
        `hexpand`), und die Leiste springt — dreimal als Fehler gemeldet, siehe
        die Notizen an `_update_step_buttons` und an `self.res_label`. Als
        Overlay-Kind über dem Bild kostet das Umschalten das Layout nichts.

        Unten RECHTS, damit er dem mittig-unten liegenden
        Nächste-Folge-Hinweis (`.gf-autoplay`) nicht in die Quere kommt."""
        button = Gtk.Button(
            label="Vorspann überspringen",
            halign=Gtk.Align.END,
            valign=Gtk.Align.END,
            margin_end=24,
            margin_bottom=24,
            visible=False,
            tooltip_text="Zum Ende des erkannten Vorspanns springen",
        )
        button.add_css_class("gf-intro-skip")
        button.connect("clicked", lambda *_: self._skip_intro())
        self.intro_button = button
        return button
```

### 2e. Marken holen

**Wo:** `_start_playback()`, Zeile 544–551. Ganz am Anfang der Methode eine
Zeile ergänzen, damit sie bei jedem Start UND bei jedem `_switch_to` (das ruft
`_start_playback` auf, Zeile 1356) greift.

**Nachher:**

```python
    def _start_playback(self) -> None:
        # Vorspann-Marken gehören zum laufenden Titel und kommen nur über
        # GET /api/items/{id} (siehe _request_intro_markers).
        self._request_intro_markers()
        if self.local_path:
            self._play_uri(Gio.File.new_for_path(self.local_path).get_uri())
            return
        if self.direct_url:
            self._play_uri(self.direct_url)
            return
        threading.Thread(target=self._resolve_stream, daemon=True).start()
```

**Drei neue Methoden** — einfügen **nach** `_load_side_data()` (endet Zeile 588)
und **vor** `_fetch_subtitle()` (Zeile 590):

```python
    def _request_intro_markers(self) -> None:
        """Vorspann-Marken des laufenden Titels beschaffen.

        Zwei Stufen: erst das mitgegebene Item auswerten (kostet nichts — wer
        es schon über `GET /api/items/{id}` geladen hat, trägt die Felder
        bereits), dann eine eigene Abfrage im Hintergrund, weil die
        Listen-Endpunkte sie NICHT liefern.

        Nicht gefragt wird bei Trailern (`direct_url` — ein Trailer hat keine
        Marken) und bei Titeln eigener Datenträger (`local: True`, Kennung
        negativ, siehe `local_library.py`): deren Kennung ist dem Server
        unbekannt, eine Abfrage träfe ein fremdes Item. Ein heruntergeladenes
        Server-Video behält seine echte Kennung und wird gefragt; ohne Netz
        scheitert die Abfrage still und es gibt schlicht keinen Knopf.

        **Die Abfrage läuft absichtlich in einem EIGENEN Faden** und nicht in
        `_load_side_data`: dort kann das Holen einer Untertitelspur bis zu 120 s
        blockieren (der Server extrahiert sie beim ersten Abruf per ffmpeg) —
        der Knopf soll aber schon am Anfang des Films bereitstehen."""
        self._intro_token += 1
        token = self._intro_token
        start, end = intro_markers(self.item)
        self._apply_intro_markers(start, end, token)
        if self.direct_url is not None:
            return
        if self.item.get("local") or self.item_id <= 0:
            return
        item_id = self.item_id

        def worker() -> None:
            # Bewusst KEINE sichtbare Fehlermeldung (anders als bei der
            # Wiedergabe selbst): die Marken sind Beigabe, genau wie in
            # `_refresh_autoplay_pref`. Ohne sie fehlt nur der Knopf.
            try:
                data = self.client.item(item_id)
            except Exception:  # noqa: BLE001 — siehe Kommentar
                return
            fetched_start, fetched_end = intro_markers(data)
            GLib.idle_add(self._apply_intro_markers, fetched_start, fetched_end, token)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_intro_markers(self, start: float | None, end: float | None, token: int) -> bool:
        """Marken übernehmen. Läuft im GTK-Hauptablauf (direkt oder per
        `idle_add`). Ein veralteter Token heißt: inzwischen läuft ein anderer
        Titel — Ergebnis verwerfen."""
        if token != self._intro_token:
            return False
        self._intro_start = start
        self._intro_end = end
        if start is None or end is None:
            self._set_intro_visible(False)
        return False

    def _update_intro_skip(self, position: float) -> None:
        """Sichtbarkeit allein aus der Position — genau wie im Browser
        (`maybeToggleIntroSkip`): zwischen Anfang und Ende des erkannten
        Vorspanns sichtbar, sonst nicht.

        `position` ist die ABSOLUTE Stelle im Film; `_tick` hat den
        `_virtual_offset` der laufenden Umwandlung dort schon aufgerechnet
        (siehe Dateikopf). Die Marken des Servers sind ebenfalls absolut, es
        wird also Gleiches mit Gleichem verglichen — bei direkter Wiedergabe
        ist der Offset 0 und es passt ohne Umrechnung."""
        start, end = self._intro_start, self._intro_end
        show = start is not None and end is not None and start <= position < end
        self._set_intro_visible(show)

    def _set_intro_visible(self, visible: bool) -> None:
        # Nur bei echter Änderung schalten — `_tick` läuft viermal je Sekunde.
        if visible == self._intro_visible:
            return
        self._intro_visible = visible
        self.intro_button.set_visible(visible)

    def _skip_intro(self) -> None:
        """Klick auf den Knopf: an das Ende des Vorspanns springen.

        `seek_to` erledigt den Rest — bei direkter Wiedergabe ein Sprung im
        Abspieler, bei laufender Umwandlung eine neue Umwandlung ab der
        Zielsekunde. Der Knopf verschwindet SOFORT und nicht erst beim nächsten
        Takt: bei einer Umwandlung tauscht `seek_to` das Medium aus, und bis
        das neue eine Position meldet, stünde der Knopf sonst noch sichtbar
        über dem schon verlassenen Vorspann."""
        end = self._intro_end
        self._set_intro_visible(False)
        if end is None:
            return
        self.seek_to(end)
```

### 2f. In den Takt einhängen

**Wo:** `_tick()`, Zeile 719–762. Zwischen den Untertitel-Block (endet Zeile 758
mit `self.subtitle_label.set_visible(bool(text))`) und
`self._maybe_report_position(position, duration)` (Zeile 760) eine Zeile
ergänzen:

```python
        self._update_intro_skip(position)
```

Ergebnis (Reihenfolge einhalten):

```python
        if self._track is not None:
            text = self._track.text_at(position)
            self.subtitle_label.set_label(text)
            self.subtitle_label.set_visible(bool(text))

        self._update_intro_skip(position)
        self._maybe_report_position(position, duration)
        self._update_stats(position, duration)
        return True
```

Das ist der einzige Ort, an dem die Sichtbarkeit berechnet wird — analog zum
`timeupdate`-Handler im Browser. **Keine weiteren Aufrufe** von
`_update_intro_skip` einbauen (nicht in `seek_to`, nicht in `toggle_play`): der
Takt läuft ohnehin alle 250 ms weiter.

### 2g. Beim Titelwechsel zurücksetzen

**Wo:** `_switch_to()`, Zeile 1307–1356, im Zurücksetz-Block. Direkt **nach**
`self._virtual_offset = 0.0` (Zeile 1333) einfügen:

```python
        # Vorspann-Marken gehören zum ALTEN Titel: verwerfen, Knopf verstecken,
        # laufende Abfrage entwerten. `_start_playback()` am Ende dieser Methode
        # holt die Marken des neuen Titels.
        self._intro_token += 1
        self._intro_start = None
        self._intro_end = None
        self._set_intro_visible(False)
```

### 2h. Beim Schließen entwerten

**Wo:** `_on_close_request()`, Zeile 1410–1423. Direkt **nach**
`self._autoplay_token += 1` (Zeile 1413) einfügen:

```python
        self._intro_token += 1
```

So kann eine noch laufende Marken-Abfrage nach dem Schließen nichts mehr
setzen.

---

## 3. Randfälle — so muss sich die Umsetzung verhalten

| Fall | Erwartetes Verhalten | Wodurch abgedeckt |
|---|---|---|
| `introStartSec`/`introEndSec` fehlen oder sind `null` | kein Knopf, nie | `intro_markers()` → `(None, None)`, `_update_intro_skip` prüft `is not None` |
| `introStartSec == 0` (Vorspann ab Sekunde 0) | Knopf erscheint sofort | Prüfung auf `is None`, **nicht** auf Wahrheitswert |
| unbrauchbare Werte (`end <= start`, negativ, Text) | kein Knopf | `intro_markers()` verwirft sie |
| Nutzer **pausiert**, während der Knopf sichtbar ist | Knopf bleibt stehen (Position ändert sich nicht) | `_update_intro_skip` ist eine reine Funktion der Position |
| Nutzer **spult** aus dem Vorspann heraus | Knopf verschwindet beim nächsten Takt (≤ 250 ms) | `_tick` |
| Nutzer spult **zurück** in den Vorspann | Knopf erscheint erneut — **ausdrücklich gewollt**, genau wie im Browser (dort ist die Sichtbarkeit ebenfalls nur eine Funktion der Position). Kein „einmal übersprungen, nie wieder" einbauen. | `_tick` |
| Klick auf den Knopf | Sprung auf `introEndSec`, Knopf sofort weg | `_skip_intro` |
| serverseitige Umwandlung (HLS) | Vergleich mit der um `_virtual_offset` korrigierten Position; der Sprung startet eine neue Umwandlung ab `introEndSec` | bestehende `_tick`/`seek_to`-Logik, unverändert |
| Wechsel auf das nächste Video (Warteschlange, Zufall, nächste Folge) | Knopf weg, Marken neu geholt | `_switch_to` (2g) + `_start_playback` (2e) |
| Fenster wird geschlossen, Antwort kommt später | nichts passiert | `_intro_token` (2h) |
| Trailer (`direct_url`) | nie ein Knopf, keine Abfrage | `_request_intro_markers` |
| eigener Datenträger (`local: True`, negative Kennung) | keine Abfrage (die Kennung kennt der Server nicht); ein im Item mitgegebener Wert würde aber greifen | `_request_intro_markers` |
| heruntergeladenes Video, Server nicht erreichbar | Abfrage scheitert still, kein Knopf, keine Fehlermeldung | `except Exception: return` im Worker |
| Vollbild, Steuerleiste ausgeblendet | Knopf bleibt sichtbar und anklickbar | er liegt im Overlay, nicht in `self.bar` |

---

## 4. Wie das in die bestehende Architektur passt

Das Vorbild ist **„Nächste Folge automatisch starten"** (v0.1.50) — dieselbe
Art von Feature, derselbe Bauplan, bewusst nachgeahmt:

* **Overlay statt Dialog und statt zweitem Fenster.** Ein Dialog aus dem Player
  legt sich hinter das Wiedergabefenster und lässt die App hängen (in v0.1.21
  genau so passiert). Es gibt nur EIN Wiedergabefenster.
* **Overlay statt Steuerleisten-Kind.** In `self.bar` darf kein Kind seine
  Sichtbarkeit ändern (v0.1.53).
* **Zustand im `PlayerWindow`, keine neue Klasse und kein neues Modul.** Der
  Player hält seinen Zustand als einfache Felder; es gibt in diesem Projekt
  kein Reactive-Store-Muster. Genau wie `_autoplay_item`/`_autoplay_token`.
* **Token-Muster gegen veraltete Hintergrundantworten** (`_autoplay_token` →
  `_intro_token`).
* **Netzzugriff nur im Hintergrundfaden**, Ergebnis per `GLib.idle_add` zurück
  in den Hauptablauf — wie `_lookup_next_episode`, `_refresh_autoplay_pref`,
  `_load_side_data`.
* **Fehler schlucken ist hier erlaubt und beabsichtigt**, weil die Marken
  Beigabe sind (Vorbild `_refresh_autoplay_pref`, Zeile 1065–1081). Die harte
  Regel „sichtbare Fehlermeldung in Hintergrundfäden" gilt für Abläufe, deren
  Ausbleiben wie „nichts passiert" wirkt — hier merkt niemand etwas, weil das
  Feature nur ein Zusatzknopf ist. Trotzdem: `except Exception`, nicht nur ein
  Spezialtyp.
* **Kein Einstellungsschalter.** Der Server hat für Intro-Skip keinen
  Pro-Konto-Schalter (anders als `autoplayNext`); der Knopf erscheint einfach,
  wenn Marken vorliegen — wie im Browser. Also **nichts** in `settings_page.py`,
  `config.py` (`ViewPrefs`) oder `api.py`-Preferences anfassen.

---

## 5. Version und Dokumentation

### 5a. Drei Versionsorte — im SELBEN Commit, synchron

1. `goldfish_linux/__init__.py`: `__version__ = "0.1.65"`
2. `pyproject.toml` (Zeile 7): `version = "0.1.65"`
3. `debian/changelog`: neuer Eintrag **oben** anfügen.

Es gibt keinen automatischen Versions-Inject beim Build; die Nummer ist auf der
Login-Seite und in der Seitenleiste sichtbar.

### 5b. `debian/changelog` — neuer Eintrag ganz oben

Stil aus dem Bestand übernehmen: **keine Umlaute** (die vorhandenen Einträge
schreiben `ae/oe/ue`), und der **erste Satz** eines Stichpunkts trägt die
Aussage — nur er landet im Update-Dialog (`_highlights`, höchstens 5 kurze
Stichpunkte).

```
goldfish-linux (0.1.65-1) unstable; urgency=medium

  * Neu: Knopf "Vorspann ueberspringen" im Videobild, sobald der Server einen
    Vorspann erkannt hat - ein Klick springt an dessen Ende. Der Knopf zeigt
    sich nur waehrend des Vorspanns und verschwindet danach von selbst.

 -- Christian <christian@byboernie.de>  Fri, 25 Sep 2026 12:00:00 +0200
```

Die Absender- und Datumszeile beginnt mit einem Leerzeichen, dann `--`, dann
zwei Leerzeichen vor dem Datum — genau wie in den bestehenden Einträgen.

### 5c. `README.md`

Im Abschnitt `### Wiedergabe` (Zeile 67–84) **einen** Stichpunkt ergänzen,
sinnvoll hinter „Vorschaubilder beim Spulen":

```markdown
- **„Vorspann überspringen"** — erkennt der Server einen Vorspann, erscheint
  während dessen Laufzeit ein Knopf im Videobild, der direkt an sein Ende
  springt.
```

Sonst nichts am README ändern.

### 5d. Themenskill

In `.claude/skills/goldfishlinux-overview/SKILL.md` an das **Ende** der
Chronik-Tabelle eine Zeile anhängen (Format der bestehenden Zeilen):

```markdown
| 0.1.65 | **„Vorspann überspringen"**: `GET /api/items/{id}` (`GetItemFor`) liefert `introStartSec`/`introEndSec` — **nur dort**, die Listen-Endpunkte tragen die Felder nicht, deshalb holt `PlayerWindow` sie beim Start selbst (`_request_intro_markers`, eigener Faden, weil das Untertitel-Nachladen in `_load_side_data` bis zu 120 s blockieren kann). Der Knopf liegt im `Gtk.Overlay` über dem Bild (unten rechts, `.gf-intro-skip`), NICHT in der Steuerleiste — dort würde sein Ein-/Ausblenden die Zeitleiste springen lassen (v0.1.53). Sichtbarkeit ist eine reine Funktion der Position, berechnet in `_tick` aus der um `_virtual_offset` korrigierten absoluten Sekunde (Browser-Vorbild `maybeToggleIntroSkip`); zurückspulen zeigt den Knopf bewusst wieder. `0.0` ist ein gültiger Vorspann-Anfang — überall auf `is None` prüfen, nie auf den Wahrheitswert. Trailer und Titel eigener Datenträger (negative Kennung) werden nicht gefragt. |
```

**`CLAUDE.md` und `AGENTS.md` nicht anfassen.** `CLAUDE.md` ist nur der
Import-Shim und wird von einem Hook zurückgesetzt.

---

## 6. Was NICHT geändert werden soll (Scope-Grenzen)

* **Keine neue Datei, kein neues Modul, keine neue Klasse.** Alles passt in die
  sieben Dateien der Tabelle oben.
* **`seek_to`, `_play_uri`, `_stream_uri`, `_release_media`, `_virtual_offset`,
  `_fresh_token`, die Fehler-Wiederholung und die Trickplay-Vorschau bleiben
  unberührt.** Der Intro-Knopf benutzt `seek_to` nur, er ändert es nicht.
* **Nichts in `self.bar` einfügen oder umbauen** — kein Knopf, kein Label, keine
  Sichtbarkeitsänderung an irgendeinem Kind der Steuerleiste.
* **Keine neue Tastenkombination** in `_on_key` (Zeile 878–891).
* **Kein Einstellungsschalter**, kein neues Feld in `ViewPrefs`
  (`goldfish_linux/config.py`), keine Änderung an
  `playback_preferences`/`set_playback_preferences`.
* **`detail_page.py`, `browse_page.py`, `home_page.py`, `seasons_page.py`,
  `playlists_page.py`, `downloads_page.py`, `local_page.py`, `widgets/*` bleiben
  unverändert.** Insbesondere NICHT versuchen, die Marken schon in den Listen
  oder in der Detailseite zu besorgen — die Listen-Endpunkte haben sie nicht.
* **`self.item` NICHT durch das frisch geholte Item ersetzen.** Nur
  `introStartSec`/`introEndSec` daraus lesen. `self.item` wird an mehreren
  Stellen weiterverwendet (`_source_description`, `_is_episode`,
  `_series_folder`), ein Austausch wäre eine unnötige Verhaltensänderung.
* **`/api/playback/{id}` nicht zusätzlich aufrufen** (ffprobe serverseitig), und
  `client.item()` **nie in einer Schleife über viele Items** — genau ein Aufruf
  je geöffnetem Titel.
* **`Adw.Spinner` nicht verwenden** (braucht libadwaita ≥ 1.6); hier wird
  ohnehin kein neuer Ladeanzeiger gebraucht.
* **Kein `set_transient_for`**, kein zweites Fenster, kein Dialog.
* **Nicht committen und nicht pushen**, solange das nicht ausdrücklich
  beauftragt ist — das Repo ist öffentlich. Testskripte (siehe unten) nicht ins
  Repo legen, sondern unter `/tmp/` anlegen.
* Keine Umformatierung, kein Aufräumen, keine Umbenennung an bestehendem Code.

---

## 7. Akzeptanzkriterien (Prüfliste für den Reviewer)

**Code**

1. `goldfish_linux/api.py` enthält die Modulfunktion `intro_markers()` — auf
   Modulebene (nicht in der Klasse), mit `is None`-Prüfungen, und sie gibt
   `(None, None)` zurück bei fehlenden, unbrauchbaren oder nicht-numerischen
   Werten, aber `(0.0, x)` bei `introStartSec = 0`.
2. `GoldfishClient.item()` hat einen Docstring, der festhält, dass nur dieser
   Endpunkt die Felder trägt.
3. `player_window.py` importiert `intro_markers` aus `..api`.
4. Der Knopf ist ein `Gtk.Button` mit **exakt** dem Label
   `Vorspann überspringen`, trägt die CSS-Klasse `gf-intro-skip`, ist
   `halign=END`/`valign=END` und startet mit `visible=False`.
5. Der Knopf wird **genau einmal** geparentet, und zwar per
   `self.overlay.add_overlay(...)` in `_build_ui`. Er ist **kein** Kind von
   `self.bar`. `grep -n "intro" goldfish_linux/windows/player_window.py` zeigt
   keinen `bar.append(...)`-Aufruf für ihn.
6. Die Sichtbarkeit wird ausschließlich in `_tick` über `_update_intro_skip`
   berechnet, und zwar mit der Variablen `position` (der bereits um
   `_virtual_offset` korrigierten absoluten Sekunde) — nicht mit
   `media.get_timestamp()` direkt.
7. Die Bedingung lautet `start is not None and end is not None and
   start <= position < end`. Kein `if start and end`, kein `<=` am oberen Ende.
8. `_request_intro_markers` wird in `_start_playback` aufgerufen (und damit auch
   über `_switch_to`); der Netzaufruf läuft in einem `threading.Thread(...,
   daemon=True)`, das Ergebnis kommt per `GLib.idle_add` zurück, und der Worker
   fängt `Exception` (nicht nur `GoldfishAPIError`) ohne UI-Meldung.
9. Trailer (`direct_url`) und Items mit `local`/nicht-positiver Kennung lösen
   **keine** Server-Abfrage aus.
10. `_switch_to` setzt `_intro_start`, `_intro_end` zurück, erhöht
    `_intro_token` und versteckt den Knopf; `_on_close_request` erhöht
    `_intro_token`.
11. `_skip_intro` versteckt den Knopf, bevor es `seek_to(end)` aufruft, und tut
    bei `end is None` nichts.
12. Keine Datei außerhalb der Tabelle in Abschnitt 0 ist geändert
    (`git status`/`git diff --stat` prüfen).

**Version und Doku**

13. `0.1.65` steht in `goldfish_linux/__init__.py`, `pyproject.toml` und
    `debian/changelog` — identisch.
14. Der Changelog-Eintrag ist umlautfrei, und der erste Satz des Stichpunkts
    trägt die Aussage allein.
15. README-Stichpunkt und Skill-Zeile sind vorhanden; `CLAUDE.md` ist
    unverändert.

---

## 8. Test- und Verifikationsschritte

Alles aus dem Repo-Wurzelverzeichnis ausführen.

**8a. Syntax und ungenutzte Namen (Pflicht, immer)**

```bash
python3 -m py_compile goldfish_linux/api.py goldfish_linux/windows/player_window.py
python3 -m pyflakes goldfish_linux/api.py goldfish_linux/windows/player_window.py
```

Beide müssen ohne Ausgabe/Fehler durchlaufen. Ist `pyflakes` nicht vorhanden:
`python3 -m pip install --user pyflakes` oder `apt install pyflakes3`.

**8b. Reine Logikprüfung von `intro_markers` (ohne GTK)**

```bash
python3 - <<'PY'
from goldfish_linux.api import intro_markers
cases = [
    ({}, (None, None)),
    ({"introStartSec": None, "introEndSec": 60}, (None, None)),
    ({"introStartSec": 0, "introEndSec": 45}, (0.0, 45.0)),
    ({"introStartSec": 12.5, "introEndSec": 60.25}, (12.5, 60.25)),
    ({"introStartSec": 60, "introEndSec": 60}, (None, None)),
    ({"introStartSec": 90, "introEndSec": 30}, (None, None)),
    ({"introStartSec": -5, "introEndSec": 30}, (None, None)),
    ({"introStartSec": "abc", "introEndSec": 30}, (None, None)),
]
for item, expected in cases:
    got = intro_markers(item)
    assert got == expected, (item, got, expected)
print("intro_markers: alle Faelle ok")
PY
```

Der Import von `goldfish_linux.api` braucht `requests`, aber **kein** GTK.

**8c. Startet die App überhaupt noch (echter Linux-Rechner mit GTK4)**

```bash
timeout 20 python3 -m goldfish_linux; echo "Exit: $?"
```

**Exit 124 = gut** (das Fenster lief, das Zeitlimit hat es beendet).
**Exit 0 = Fehler** (die App hat sich sofort beendet). Fenster suchen mit
`wmctrl -lp` (Titel genau `Goldfish`).

**8d. Sichtprüfung des Knopfes mit einem Treiberskript (ohne Server)**

Der Knopf funktioniert auch ohne Server, weil `_request_intro_markers` die
Marken zuerst aus dem übergebenen Item liest. Skript unter `/tmp/` anlegen —
**nicht** ins Repo:

```bash
cat > /tmp/intro_skip_driver.py <<'PY'
import sys
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib

from goldfish_linux.api import GoldfishClient
from goldfish_linux.windows.player_window import PlayerWindow

VIDEO = sys.argv[1]          # Pfad zu einer beliebigen lokalen Videodatei

item = {
    "id": -1,                # negativ + local: kein Server-Item
    "local": True,
    "path": VIDEO,
    "title": "Intro-Skip-Test",
    "durationSec": 600,
    "introStartSec": 5,      # Knopf soll ab Sekunde 5 erscheinen
    "introEndSec": 20,       # und ab Sekunde 20 wieder weg sein
}


class App(Adw.Application):
    def do_activate(self):
        PlayerWindow(self, GoldfishClient(), item, local_path=VIDEO).present()


App(application_id="io.github.boernie77.GoldfishLinux.introtest").run([])
PY
timeout 60 python3 /tmp/intro_skip_driver.py /pfad/zu/video.mkv
```

Zu prüfen (Screenshot mit `gnome-screenshot -w -f /tmp/intro.png`, vorher
`wmctrl -i -a <id>` — `import`/`xdotool` sind nicht installiert):

1. Sekunde 0–5: **kein** Knopf.
2. Sekunde 5–20: Knopf unten rechts im Videobild, Text `Vorspann überspringen`.
3. Klick darauf: Wiedergabe steht bei ~20 s, Knopf sofort weg.
4. Mit dem Zurück-Knopf (15 s) wieder in den Bereich spulen: Knopf erscheint
   erneut (gewollt).
5. Die Zeitleiste ändert beim Ein- und Ausblenden des Knopfes **ihre Länge
   nicht** — beide Screenshots (Knopf sichtbar / unsichtbar) übereinanderlegen:
   Griffposition und Balkenbreite müssen identisch sein. Das ist der
   wichtigste Regressionstest (v0.1.53).
6. `introStartSec` im Skript auf `0` setzen und erneut starten: der Knopf muss
   von der ersten Sekunde an da sein (Beweis, dass `0` nicht als „fehlt" gilt).
7. Beide Marken im Skript auf `None` setzen: nie ein Knopf, keine Fehlermeldung.

**8e. Gegen den echten Server (wenn erreichbar)**

Eine Serienfolge mit erkanntem Vorspann öffnen — sowohl in **direkter
Wiedergabe** (Qualität „Original") als auch mit einer **kleineren
Qualitätsstufe** (erzwingt die serverseitige Umwandlung). In beiden Fällen muss
der Knopf an derselben Stelle des Films erscheinen und der Sprung an derselben
Stelle landen. Der Umwandlungsfall ist der interessante: dort zählt das Medium
ab dem Anfang der laufenden Umwandlung, und nur die Korrektur um
`_virtual_offset` macht den Vergleich richtig. Zusätzlich nach einem Sprung
mitten in den Film prüfen, dass der Knopf **nicht** fälschlich wieder auftaucht.

**8f. Abschlussbericht**

Im Abschlussbericht ausdrücklich festhalten, was geprüft wurde und was nicht —
insbesondere, falls 8c–8e nicht laufen konnten. `py_compile`/`pyflakes` allein
sind **kein** Ersatz für einen Test am echten Linux-Rechner; solche Änderungen
gelten als „ungeprüft auf echtem GTK" und müssen so benannt werden (im
Abschlussbericht und in der Skill-Zeile aus 5d).
