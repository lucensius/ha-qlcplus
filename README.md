# QLC+ für Home Assistant

Custom Integration, die [QLC+](https://www.qlcplus.org/) (Q Light Controller Plus) über dessen
Weboberfläche steuert. Du lädst beim Einrichten dein QLC+-Projekt (`.qxw`) hoch, optional mit
eigenen Fixture-Definitionen (`.qxf`). Die Integration liest daraus Geräte, Adressen, Kanäle,
Szenen, Chaser und Virtual-Console-Widgets aus und legt die passenden Entitäten automatisch an.
Gesteuert wird live über die WebSocket-Schnittstelle von QLC+.

Nichts ist auf bestimmte Geräte zugeschnitten: Alle Entitäten werden aus den Kanal-Groups,
Presets und Capabilities der Fixture-Definitionen abgeleitet.

- Getestet mit QLC+ **4.14.4** (Desktop-Version im Webmodus) und Home Assistant **2026.2**.
- QLC+ 4.13 wird laut Quellcode unterstützt, ist aber nicht live getestet. QLC+ 5 ist ungetestet.

## Installation

### Über HACS

1. HACS → Integrationen → Menü oben rechts → *Benutzerdefinierte Repositories*.
2. `https://github.com/lucensius/ha-qlcplus` als Kategorie *Integration* hinzufügen.
3. „QLC+“ installieren und Home Assistant neu starten.

### Manuell

Kopiere den Ordner `custom_components/qlcplus` nach `<config>/custom_components/qlcplus` und starte
Home Assistant neu.

## QLC+ im Webmodus starten

QLC+ muss mit Weboberfläche laufen (Standard-Port 9999):

```bash
qlcplus -w -o /pfad/zu/Lichtsteuerung.qxw -p
```

| Option | Bedeutung |
|---|---|
| `-w` | Weboberfläche und WebSocket aktivieren |
| `-wp 9999` | anderer Port |
| `-o <datei>` | Projekt beim Start öffnen |
| `-p` | im Operate-Modus starten (sonst reagieren VC-Buttons nicht) |
| `-wa` | Web-Authentifizierung (Benutzer/Passwort dann in HA eintragen) |

Teste im Browser: `http://<ip>:9999` muss die Virtual Console zeigen.

### Raspberry Pi (ohne Bildschirm, optional mit Docker)

QLC+ 4 ist eine Qt-Desktop-Anwendung. Ohne Bildschirm startest du es mit einem virtuellen
X-Server:

```bash
sudo apt install qlcplus xvfb
xvfb-run -a qlcplus -w -o /home/pi/Lichtsteuerung.qxw -p
```

Ein mögliches Docker-Setup (Beispiel, nicht von mir auf einem Pi getestet; `qlcplus` kommt aus
den Debian-Paketquellen):

```dockerfile
# Dockerfile
FROM debian:bookworm-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends qlcplus xvfb xauth \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /data
EXPOSE 9999
CMD ["xvfb-run", "-a", "qlcplus", "-w", "-o", "/data/project.qxw", "-p"]
```

```yaml
# docker-compose.yml
services:
  qlcplus:
    build: .
    restart: unless-stopped
    network_mode: host          # Art-Net/sACN und Port 9999
    volumes:
      - ./data:/data            # project.qxw und eigene Fixtures
      - ./qlcplus-config:/root/.qlcplus
    devices:
      - /dev/ttyUSB0:/dev/ttyUSB0   # USB-DMX-Interface, falls vorhanden
```

Eigene Fixture-Definitionen legst du für QLC+ nach `~/.qlcplus/fixtures/` (im Container
`./qlcplus-config/fixtures/`).

## Einrichtung

1. *Einstellungen → Geräte & Dienste → Integration hinzufügen → QLC+*.
2. Host und Port eingeben. Die Integration testet die Verbindung und zeigt die QLC+-Version an.
3. Das Projekt (`.qxw`) hochladen, das in QLC+ geladen ist, optional eine eigene `.qxf`.
4. Fehlen Fixture-Definitionen, listet der nächste Schritt sie mit Hersteller, Modell und Modus
   auf, und du kannst weitere `.qxf` hochladen. Definitionen aus der QLC+-Bibliothek werden
   automatisch von GitHub geladen (nur die benötigten) und lokal unter `<config>/qlcplus/`
   zwischengespeichert.
5. Die Integration vergleicht die Funktions- und Widget-IDs mit der laufenden QLC+-Instanz. Passt
   das Projekt nicht, siehst du eine Warnung mit den Abweichungen.

Über **Konfigurieren** (Optionen) kannst du später:

- ein neues Projekt oder weitere Fixtures hochladen (die Entitäten werden neu aufgebaut),
- Entitätsgruppen wählen: Funktionen, Geräte, VC-Widgets,
- das Poll-Intervall (Standard 5 s) und die Dauer für Flash-Buttons (Standard 1 s) einstellen.

## Erzeugte Entitäten

**Geräte:** ein Hub für die QLC+-Instanz, ein Gerät pro Fixture und bei Fixtures mit mehreren
Heads ein Gerät pro Head (per `via_device` unter dem Fixture).

| Quelle | Entität |
|---|---|
| Head bzw. Fixture mit Dimmer/Intensity oder RGB(W)-Kanälen | `light` mit Helligkeit. RGB(W)-Kanäle werden zu RGB(W)-Farbe. Hat das Gerät nur ein Farbrad, wählt die Integration bei einer HA-Farbe die nächstgelegene Farbradfarbe (aus `Res1` der Capabilities). Gobos und Effekte erscheinen als `effect_list`. |
| Colour-, Gobo-, Effect-, Prism- und Maintenance-Kanäle mit mehreren Capabilities | `select` (Maintenance ist standardmäßig deaktiviert) |
| Pan/Tilt (inkl. 16-Bit-Fine) | `number` in Grad, wenn der Modus `PanMax`/`TiltMax` kennt, sonst in % |
| Speed, Shutter/Strobe, Zoom und andere stufenlose Kanäle | `number` (DMX 0–255, Intensity in %) |
| Nebelmaschinen (Typ Smoke/Hazer) | `number` in % statt `light` |
| Szenen, Chaser, Sequenzen, EFX, Shows, … | `switch` mit Status aus QLC+ (Push) |
| SingleShot-Chaser/-Sequenzen | `button` |
| VC-Flash-Buttons | `button` (wird für die eingestellte Dauer gehalten) |
| VC-Toggle-Buttons, VC-Fader | `switch` bzw. `number`, standardmäßig deaktiviert |
| Global | `switch` Blackout, `switch` Laser-Freigabe (nur bei Lasern), `binary_sensor` Verbindung |

Die Namen setzen sich aus Fixture-, Head- und Kanalnamen zusammen (z. B. „TMH Bar S120 Kopf 1
Farbrad“). Die `unique_id`s bleiben stabil, auch wenn du das Projekt neu hochlädst.

### Wie HA in die Lichtsteuerung eingreift

Licht-, Select- und Number-Entitäten schreiben über den **Simple Desk** von QLC+. Diese Werte
haben Vorrang vor allen laufenden Funktionen, bis du den Kanal wieder freigibst
(`qlcplus.release_channel`). Funktionen schaltest du über die Switches oder
`qlcplus.run_function` und `qlcplus.stop_function`.

### Laser-Sicherheit

Fixtures vom Typ `Laser` bekommen das Attribut `laser: true`. Solange der Schalter
**Laser-Freigabe** aus ist (Standard, auch nach jedem Neustart), blockiert die Integration:

- jeden Wert über 0 auf einem Laser-Kanal (Entitäten und `qlcplus.set_channel`),
- Funktionen, die einen Laser einschalten, auch indirekt über Chaser, Collections, Shows oder
  Skripte,
- VC-Buttons und -Fader, die solche Funktionen oder Laser-Kanäle steuern.

Schaltest du die Freigabe wieder aus, nimmt die Integration zurück, was HA am Laser gesetzt bzw.
gestartet hat. Fixtures ohne auflösbare Definition werden vorsichtshalber wie Laser behandelt.

> Die Sperre wirkt nur auf Aktionen aus Home Assistant. Die Virtual Console von QLC+, andere
> Clients oder externe Eingänge bleiben unberührt. Sie ersetzt keine Hardware-Sicherheit
> (Not-Aus, Interlock).

### Services

| Service | Beschreibung |
|---|---|
| `qlcplus.run_function` / `qlcplus.stop_function` | Funktion per ID oder exaktem Namen starten bzw. stoppen |
| `qlcplus.set_channel` | DMX-Kanal setzen (`address` absolut ab 1, oder mit `universe`) |
| `qlcplus.release_channel` | Kanal (oder Liste) freigeben; ohne Adresse alle Kanäle |
| `qlcplus.blackout` | Blackout ein/aus |
| `qlcplus.reload_project` | gespeichertes Projekt neu laden |

`config_entry_id` brauchst du nur bei mehreren QLC+-Instanzen.

## Beispiel-Automationen

**Partymodus** – Szene starten, Laser nur auf ausdrücklichen Wunsch:

```yaml
automation:
  - alias: Partymodus
    triggers:
      - trigger: state
        entity_id: input_boolean.party
        to: "on"
    actions:
      - action: qlcplus.run_function
        data:
          function: "Bar Rainbow + Gobos"
      - action: qlcplus.run_function
        data:
          function: "Bar Auto-Makro 1"
  - alias: Partymodus aus
    triggers:
      - trigger: state
        entity_id: input_boolean.party
        to: "off"
    actions:
      - action: qlcplus.stop_function
        data:
          function: "Bar Rainbow + Gobos"
      - action: qlcplus.stop_function
        data:
          function: "Bar Auto-Makro 1"
      - action: qlcplus.run_function
        data:
          function: "Bar Aus"
```

**Blackout bei Abwesenheit:**

```yaml
automation:
  - alias: Licht aus, wenn niemand zu Hause ist
    triggers:
      - trigger: state
        entity_id: zone.home
        to: "0"
        for: "00:05:00"
    actions:
      - action: qlcplus.blackout
        data:
          state: true
  - alias: Blackout aufheben bei Ankunft
    triggers:
      - trigger: numeric_state
        entity_id: zone.home
        above: 0
    actions:
      - action: qlcplus.blackout
        data:
          state: false
```

**Nebelstoß** – der SingleShot-Chaser „Nebelstoß 5 s“ läuft einmal durch und stoppt selbst:

```yaml
automation:
  - alias: Nebelstoß beim Szenenstart
    triggers:
      - trigger: state
        entity_id: switch.qlc_lichtsteuerung_bar_rainbow_gobos
        to: "on"
    actions:
      - action: button.press
        target:
          entity_id: button.qlc_lichtsteuerung_nebelstoss_5_s
```

Die Entity-IDs hängen vom Titel des Eintrags und von den Namen in deinem Projekt ab; schau im
Gerät „QLC+ …“ nach.

## Bekannte Grenzen

- **Zustände per Polling:** QLC+ meldet von sich aus nur Funktionsstarts und -stopps. Kanalwerte
  und VC-Widget-Zustände werden im Poll-Intervall gelesen, Änderungen aus der VC erscheinen daher
  mit bis zu einem Intervall Verzögerung. Die VC-Seite (`/`) ruft die Integration absichtlich
  nicht auf: QLC+ würde sonst bei jedem Aufruf zusätzliche Push-Verbindungen anlegen.
- **Blackout:** Über WebSocket gibt es keinen Blackout-Befehl. Hat das Projekt einen VC-Button
  mit Aktion *Blackout*, wird dieser verwendet. Sonst setzt die Integration den Grand Master auf
  0, und das wirkt nur auf die Kanäle, die der Grand Master steuert (standardmäßig nur
  Intensity-Kanäle). Den vorherigen Grand-Master-Wert kann HA nicht lesen; beim Aufheben wird er
  auf 100 % gesetzt.
- **Freigeben setzt auf den Default-Wert:** Nach `release_channel` steht ein Kanal, den keine
  Funktion schreibt, auf dem Default-Wert der Fixture-Definition, nicht auf dem Wert davor.
- **Projekt muss passen:** QLC+ kann das geladene Projekt nicht herausgeben. Lädst du in QLC+ ein
  anderes Projekt, musst du es in HA neu hochladen (Optionen).
- **Nicht abgebildet:** XY-Pads (Pan/Tilt gibt es als Numbers), Cue-Lists, Uhren,
  Animation-Widgets, Audio-Trigger.
- Funktions- oder Widget-Namen mit `|` werden von QLC+ nicht sauber übertragen.
- Fixture-Definitionen aus der Bibliothek stammen aus dem Stand von QLC+ 4.14.4.
- Mit `-wa` (Authentifizierung) braucht HA einen Benutzer mit mindestens „Simple Desk und VC“.

## Entwicklung

```bash
python3.13 -m venv .venv && .venv/bin/pip install -r requirements_test.txt
.venv/bin/pytest            # Unit- und Integrationstests (Fake-QLC+-Server)
QLC_LIVE_HOST=192.168.8.76 .venv/bin/pytest tests/integration/test_live.py -s   # Live-Test
python tools/qlc_probe.py <host> --details   # Schnittstelle erkunden (nur lesend)
```

- `docs/qlcplus-protocol.md` – das aus dem QLC+-Quellcode abgeleitete und live geprüfte Protokoll
- `docs/decisions.md` – getroffene Design-Entscheidungen
- `custom_components/qlcplus/qlc/` – Parser, Client und Planung ohne Home-Assistant-Abhängigkeit

Lizenz: siehe [LICENSE](LICENSE).
