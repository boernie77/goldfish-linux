---
name: goldfishlinux-performance-und-rendering
description: "Use when GoldfishLinux is slow, leaks threads/memory, or looks blurry: flat movie libraries, widget recycling, poster decode width, card measurement, music lists."
metadata:
  project: GoldfishLinux (boernie77/goldfish-linux)
  source: "CLAUDE.md-Aufteilung 2026-09-20"
---

# goldfishlinux-performance-und-rendering

Aus der frueheren Sammel-CLAUDE.md des GoldfishLinux-Repos ausgelagerter Themenbereich (Zeichen: 13321, Sektionen: 2). Volltext des Originals: Skill `goldfishlinux-full-archive`.

## Harte Regeln (zuerst lesen)

- Film-Bibliotheken sind IMMER flach - Ordnerkacheln dort erzeugen Tausende Kacheln ohne Poster.
- Jede Liste, die tausende Eintraege haben KANN, muss wiederverwenden (`Gtk.GridView`, `Gtk.ListView` ueber `Gio.ListStore`, `Gtk.ColumnView`); `Gtk.ListBox` nur bei naturgemaess kleiner Laenge.
- Bilder erst laden, wenn die Kachel in Sicht ist (GridView legt ~385 Kacheln an), und im Hintergrund dekodieren.
- Die Dekodierbreite muss zur Kartenhoehe passen (`_thumb_decode_width`), sonst skaliert `ContentFit.COVER` zu klein dekodierte Bilder hoch - das war die Unscharfe der `/api/thumb/`-Fallbacks.
- NIE die Elternkette einer Kachel ablaufen (`get_parent()` in Schleife) - das endet reproduzierbar im Speicherauszug.

---

## Fallstricke aus 0.1.18 (Tempo und GTK-Lebensdauern)

- **Film-Bibliotheken sind IMMER flach.** Ein Film liegt in seinem eigenen
  Release-Ordner; die Filme-Bibliothek dieses Servers bringt 2808 solcher
  Ordner mit, jeder mit einer Datei und ohne eigene TMDB-Zuordnung. Wer dort
  Ordnerkacheln zeigt, zeigt 2808 Kacheln mit Release-Dateinamen und ohne
  Poster — genau der Fehler, den der Benutzer als "keine Filmcover, Titel wie
  der Dateiname" gemeldet hat. Der Browser macht es ebenso (`grid.js`:
  `flatView = state.flatView || lib.kind === "movies" || isShuffle`). Serien
  behalten ihre Ordnerkacheln.
- **`Gtk.GridView` legt rund 385 Kacheln an, egal wie groß das Fenster ist.**
  Nachgemessen bei 626 px Höhe und 301 px hohen Zeilen (zwölf sichtbare
  Kacheln) — und zwar auch mit einem nackten GridView ohne unseren Code, also
  keine Eigenheit dieser App. Wer beim Belegen einer Kachel etwas anstößt
  (Bild laden!), tut das also 385-mal pro Seitenaufruf. Poster werden deshalb
  erst geladen, wenn die Kachel wirklich in Sicht ist: 12 statt 385 Anfragen.
- **⚠ NIE die Elternkette einer Kachel ablaufen** (`picture.get_parent()` in
  einer Schleife). Oberhalb einer Rasterkachel liegen die internen Widgets des
  GridView; schon sie aus Python anzufassen erzeugt Hüllobjekte, die GTKs
  eigenem Aufräumen in die Quere kommen. Folge beim Scrollen: erst
  `gtk_widget_insert_after: assertion 'GTK_IS_WIDGET (widget)' failed`, dann
  `Gtk:ERROR ... gtk_list_factory_widget_teardown_factory: assertion failed:
  (priv->object == NULL)` und ein Speicherauszug. Reproduzierbar in drei von
  drei Läufen; mit derselben Prüfung ohne Elternlauf still. Deshalb gibt das
  Raster seine Bildlaufleiste ausdrücklich an die Kachel weiter
  (`CardWidget(scroller=...)`), und gerechnet wird nur mit
  `compute_bounds(scroller)`.
- **Ein Modellwechsel in einem SICHTBAREN Raster, das dabei stark schrumpft,
  ist nicht still.** 3170 Kacheln auf einen Suchtreffer zu bringen, während
  das Raster im Fenster hängt, erzeugt acht Meldungen der Art
  `gtk_widget_measure: assertion 'GTK_IS_WIDGET (widget)' failed` (auch mit
  komplett neuem Modell statt `splice`, auch ohne jedes Poster-Laden). Hängt
  das Raster während des Wechsels nicht im Fenster, bleibt es still — deshalb
  tritt bei JEDEM Laden weiter der Ladekreis an seine Stelle, obwohl das
  stehende Raster ruhiger aussähe.
- **Dekodieren gehört in den Hintergrund.** Bis 0.1.17 wurde ein Bild aus dem
  Dateizwischenspeicher im Hauptablauf dekodiert ("der Thread-Umweg ist teurer"
  — stimmt pro Bild, aber nicht bei 385 davon). Jetzt: Texturen im Speicher
  halten (begrenzt), dekodieren im Faden, im Hauptablauf nur noch die fertige
  Textur setzen.
- **Die Reihenzeilen von `/api/nav/preferences` und `/api/home/preferences`
  heißen `libraryId`, nicht `id`.** Beide Endpunkte liefern eine eigene
  Zeilenform, nicht das übliche Bibliotheks-Objekt. Mit `lib["id"]` stirbt der
  Signal-Handler an einem KeyError, GTK schreibt das nur auf die Konsole — für
  den Benutzer wirkt der Schalter wirkungslos ("ein An- und Abwählen bewirkt
  nichts"). Außerdem MUSS die Seitenleiste diese Einstellung selbst lesen und
  nach einer Änderung neu aufgebaut werden.
- **⚠ `MainWindow.ctx.library_kinds` ist beim allerersten Öffnen der
  Startseite oft noch leer (Bug, gefixt 0.1.32, User-Report: "bei Linux
  fehlt auf der Startseite der Serienname und der Kanalname bei YouTube").**
  `MainWindow.__init__` pusht `HomePage` SOFORT, das Befüllen von
  `ctx.library_kinds` (eigener Hintergrund-Ladevorgang über
  `_load_libraries()`) läuft daneben her und braucht zwei sequentielle
  Netzwerk-Anfragen (`/api/libraries` + `/api/nav/preferences`) — die
  Home-Antwort selbst (nur EIN Aufruf) kommt praktisch immer zuerst zurück.
  `home_page.py`s `_strip()` fragte für die beiden bibliotheksübergreifenden
  Streifen ("Fortsetzen"/"Als nächstes", die einzigen zwei ohne explizit
  mitgegebenes `kind`) genau dieses noch leere `ctx.library_kinds` ab — die
  Bibliotheksart fiel dadurch praktisch immer auf den Fallback "movies"
  zurück, `CardWidget.bind()`s Serien-/Kanalname-Zeile (nur bei
  `kind in ("tv", "private")`) blieb unsichtbar. Fix: `_strip()` nutzt
  jetzt `self._library_by_id`, das `_apply()` bereits synchron aus der
  EIGENEN Home-Antwort baut (`section["library"]` je Sektion) — dieselbe
  Datenquelle, kein zweiter, race-anfälliger Ladevorgang.
- **Vollständigkeit einer Sammlung** ist `movieCount >= partCount -
  hiddenCount - unreleasedCount` (beide Abzüge schickt der Server nur, wenn
  sie nicht 0 sind). Ohne die Abzüge gilt jede Reihe mit angekündigter
  Fortsetzung dauerhaft als unvollständig.
- **⚠ `Gtk.Picture` meldet bei vorgegebener Höhe eine seitenverhältnis-
  abhängige NATURBREITE.** Ein 16:9-Bild in einem 168×252 angeforderten
  Picture verlangt bei `for_size=301` eine Breite von 536 Pixeln
  (nachgemessen; bei `for_size=-1` immerhin die Texturbreite). Ist im Fenster
  Platz übrig, verteilt die umgebende Box diesen Wunsch — die Kachel zeichnet
  weiter 168 und daneben klafft eine Lücke. Genau das war als "in allen
  Bibliotheken zieht sich der Abstand auf, wenn ich das Fenster größer mache"
  gemeldet. **Weder `set_size_request` noch `halign`, `hexpand=False`,
  `Adw.Clamp` (216) oder `Gtk.AspectFrame` (536) helfen dagegen.** Was hilft:
  die Sollgröße gibt eine leere Box als HAUPTKIND eines `Gtk.Overlay` vor, das
  Bild liegt als Overlay-Kind darüber und ist von der Messung ausgenommen
  (`set_measure_overlay(picture, False)`) — Naturbreite dann 168 bei jeder
  Höhe. Siehe `_image_frame` in `widgets/card.py`; JEDE neue Bildkachel muss
  darüber gebaut werden.
- **`Gtk.GridView` teilt seine Breite auf genau so viele Spalten, wie er
  anlegt.** Mit `max_columns=12` sind das in einem 2600 Pixel breiten Fenster
  216 Pixel je Spalte, also 48 Pixel Luft je 168er-Kachel. Deshalb steht die
  Grenze auf 36: dann bestimmt die Kachelbreite die Spaltenzahl.
- **Die Startseite hat zwei übergreifende Streifen, keine pro Bibliothek.**
  "Fortsetzen" und "Als nächstes" führen die Titel ALLER Bibliotheken in je
  einer Zeile (sortiert nach letztem Abspielen bzw. Hinzufügen, je 24), erst
  darunter kommt "Zuletzt hinzugefügt" pro Bibliothek. Der Server liefert
  beides pro Bibliothek getrennt; das Zusammenführen ist Client-Aufgabe
  (`views.js renderHomeView` macht es genauso). In den übergreifenden
  Streifen bestimmt jede Kachel ihre Art selbst über `libraryId` — dort liegen
  Filme, Folgen und Privatvideos nebeneinander.

- **Musiklisten sind ein `Gtk.ColumnView`, kein Stapel aus Zeilen-Widgets**
  (`widgets/column_list.py`, seit 0.1.33). Entscheidend sind drei Dinge, die
  eine selbstgebaute Zeilenliste nicht mitbringt: Recycling (7317 Titel als
  einzelne Zeilen-Widgets blockieren den Hauptablauf sekundenlang, dieselbe
  Messung wie beim Album-Raster), ziehbare Spaltenbreiten und per Kopf
  verschiebbare Spalten. Was der ColumnView NICHT mitbringt und diese Klasse
  ergänzt, ist das Merken: Reihenfolge, Breiten und Auswahl liegen unter
  einem Kontextnamen ("albums"/"allTracks"/"albumTracks") in den
  Ansichtseinstellungen, genau wie `MUSIC_LIST_CONTEXTS` im Browser.
  Fallstricke dabei:
  - **Beim Aufbau darf nicht gespeichert werden.** Das Anhängen der Spalten
    löst dasselbe `items-changed` aus wie ein Verschieben durch den Benutzer
    und würde die gerade gelesene Reihenfolge sofort überschreiben
    (`_building`-Schalter).
  - **`notify::fixed-width` feuert beim Ziehen für jedes Pixel** — Speichern
    erst nach 400 ms Ruhe, sonst schreibt die App die Einstellungsdatei
    hunderte Male je Zug.
  - **Einer dehnbaren Spalte keine feste Breite vorgeben.** `expand=True` plus
    `set_fixed_width` nimmt ihr das Dehnen, und rechts bleibt im breiten
    Fenster eine leere Fläche. Eine feste Breite bekommt sie erst, wenn der
    Benutzer sie selbst gezogen hat.
  - **Die Aktionsspalte muss nach jedem Einblenden wieder ans Ende geholt
    werden** (`_move_fixed_columns_last`) — angehängt wird immer hinten, also
    sonst hinter den Knöpfen.
  - **Keine Spalte ist dehnbar** (`expand`). Eine dehnbare Spalte saugt den
    Restplatz auf und rechnet sich bei JEDER Breitenänderung neu — zieht man
    irgendeine Spalte breiter, wandern dadurch auch die Nachbarn (nachgemessen:
    Spalte 0 auf 250 gezogen, die dehnbare Spalte 2 schrumpfte von 337 auf
    237). Genau das war als "wenn ich Spalten verschiebe, bewegen sich alle
    anderen mit" gemeldet. Mit festen Breiten ändert sich nur die angefasste
    Spalte; rechts bleibt im breiten Fenster Platz frei.
  - **Die Leiste darüber trägt NICHT die Klasse `toolbar`.** libadwaita macht
    Knöpfe darin rahmenlos — ein einzelner Knopf neben einer verbundenen
    Gruppe sieht dann aus, als hinge er lose in der Gegend.
  - **Kein Bildlauf um die Tabelle herum.** Sie bringt einen eigenen mit;
    zwei ineinander sind mit dem Rad kaum zu treffen, und die Spaltenköpfe
    wären beim Blättern weg. Auf der Albumseite steht der Kopf deshalb fest
    darüber statt mitzuscrollen.
  - **Zeilen werden wiederverwendet.** Ein Favoriten-Umschalter in der Zeile
    bekommt beim Binden seinen Zustand gesetzt und meldet das als `toggled` —
    ohne Vergleich mit dem gemerkten Wert löst allein das Scrollen
    Server-Aufrufe aus.

- **Jede Liste, die tausende Einträge haben KANN, muss wiederverwenden.**
  Das Fenster der Warteschlange baute eine `Adw.ActionRow` je Titel — bei
  einer gemischten Bibliothek (4438 Titel) öffnete es dadurch gar nicht mehr.
  Mit `Gtk.ListView` über einem `Gio.ListStore` sind es 0,07 Sekunden
  (nachgemessen, seit 0.1.37). Dasselbe gilt für die Kachelraster
  (`CardGrid`/`AlbumGrid`) und die Musiktabellen (`ColumnList`) — die
  `Gtk.ListBox`-Listen im Rest der App sind nur dort in Ordnung, wo die
  Länge von Natur aus klein ist (Titel eines Albums, Suchtreffer).
- **Die Zufallswiedergabe zieht 200 Titel, nicht die ganze Bibliothek**
  (`_SHUFFLE_LIMIT`): eine Warteschlange mit tausenden Einträgen ist nicht
  mehr überschaubar — der Zähler an der Abspielleiste sah aus wie ein Fehler.


## Unscharfe Vorschaubilder ohne eigenes Cover — echter Fix in 0.1.38

User-Report 2026-09-13: "Ich hatte auf der Linux App gestern bemängelt, dass
die Vorschaubilder in den Kacheln, wenn kein Cover geladen ist, also bei den
privaten Bibliotheken, aber auch bei Serienfolgen, ziemlich unscharf ist.
Angeblich wurde das gefixt, aber die sind immer noch unscharf." Betrifft
jedes Item OHNE TMDB/Custom-Metadata-Poster (`poster_path_for_item` fällt
dann auf `/api/thumb/{id}` zurück — server-generiert, fix 480×270, 16:9,
siehe Server-CLAUDE.md „Scanner & Metadaten").

**Warum der 0.1.24-Fix nie griff:** er machte `CardWidget`/`FolderCardWidget`
selbst zwar breiter für `kind == "private"` (`CARD_WIDTH_WIDE`), aber die
tatsächlich benutzte Bibliotheksansicht (`widgets/grid.py: CardGrid`) erzwingt
für PRIVATE Bibliotheken absichtlich dieselbe Kartenform wie Filme/Serien
(`aspect = "movies" if self.kind == "private" else self.kind` — User-Wunsch
aus einer früheren Session: "auch in der Bibliothek YouTube sollen die
Kacheln die gleiche Größe haben") und übergibt diese überschriebene Form an
beide Widgets. Der Breiten-Umschalter aus 0.1.24 wird dadurch nie erreicht —
weder bei privaten Bibliotheken noch (da nie dafür gebaut) bei Serienfolgen
ohne Poster, die ohnehin schon immer in der normalen Filme/Serien-Kartenform
(2:3) laufen.

**Der tatsächliche Bug saß einen Schritt früher, beim Dekodieren:**
`load_poster_async(decode_width=…)` bekam bisher immer die KARTENBREITE
(z. B. 168 px) — bei einem 16:9-Bild ergibt das nur ≈94 px Höhe. Erst danach
skaliert `Gtk.Picture`s `ContentFit.COVER` dieses bereits stark verkleinerte
Bild auf die volle, deutlich größere Kartenhöhe (z. B. 252 px bei einer
2:3-Karte) wieder hoch — ein Faktor von ≈2,7×. Genau dieses nachträgliche
Hochskalieren eines zu klein dekodierten Bilds war die Unschärfe, nicht der
Zuschnitt selbst (der reine COVER-Crop eines 16:9-Bilds in eine 2:3-Karte
skaliert eigentlich eher leicht HERUNTER + croppt seitlich, kein
Qualitätsverlust).

**Fix (`widgets/card.py`):** neue Funktion `_thumb_decode_width(frame_width,
frame_height)` — liefert `max(frame_width, frame_height * 16/9)`, also genug
Dekodierbreite, damit die anschließende COVER-Höhenskalierung nicht mehr
über die native Auflösung hinaus hochskalieren muss (deckt sich ungefähr mit
der nativen Server-Auflösung 480×270, kein künstlicher Zusatzverlust vorher).
`CardWidget`/`FolderCardWidget` merken sich jetzt zusätzlich `_frame_height`
und nutzen die Funktion NUR, wenn der gewählte Pfad tatsächlich ein
`/api/thumb/`-Fallback ist (echte TMDB/Custom-Poster behalten `_frame_width`
als Dekodierbreite — deren Seitenverhältnis passt schon zur Kartenform, da
gibt es das COVER-Mismatch-Problem gar nicht).

**Kein Server-seitiger Rescan/Neu-Einlesen nötig** — reines Client-
Rendering-Problem, keine gespeicherten Daten waren betroffen. Nach dem
Update auf 0.1.38 sind die Kacheln beim nächsten Öffnen der Bibliothek
automatisch scharf (der Bild-Cache in `widgets/poster.py` schlüsselt ohnehin
über `(server_path, decode_width)` — ein geänderter `decode_width` erzeugt
automatisch einen neuen Cache-Eintrag statt eine alte, zu klein dekodierte
Version wiederzuverwenden).

