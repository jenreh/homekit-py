# Apple HomeKit – Lokale Python Library – Konzept & Implementierungsplan

> **Projektname:** `homekit-local-py`
> **Paketname:** `homekit_local`
> **Stand:** Mai 2026
> **Ziel:** Lokale Steuerung von Apple HomeKit-Geräten via Python (HAP-Protokoll) – nutzbar als Library, CLI und MCP-Server

---

## 1. Protokoll-Analyse und Implementierungsentscheidung

### 1.1 Das HAP-Protokoll – Fundamentaler Unterschied zu Harmony und Hue

| Aspekt | Harmony Hub | Hue Bridge | HomeKit (HAP) |
|--------|------------|-----------|--------------|
| Transport | WebSocket (custom JSON) | HTTPS REST + SSE | **Custom-TCP mit End-to-End-Verschlüsselung** |
| Standard | Undokumentiert, inoffiziell | Offiziell dokumentiert | Offiziell spezifiziert (HAP Spec), komplex |
| Authentifizierung | Remote-ID im URL | `hue-application-key` Header | **SRP-Pairing + Ed25519-Schlüsselaustausch** |
| Push-Events | WebSocket-Events | SSE | **Persistente TCP-Verbindung mit Event-Notifications** |
| TLS | Kein TLS | HTTPS (selbst-signiert) | **Kein TLS – eigene ChaCha20-Poly1305-Verschlüsselung** |
| HTTP-Libraries nutzbar? | Ja (websockets) | Ja (httpx) | **Nein – Verschlüsselung auf TCP-Ebene** |
| Protokollkomplexität | Mittel | Niedrig | **Sehr hoch** |

Die Pairing-Authentifizierung nutzt das Secure Remote Password (SRP, 3072-bit) Protokoll mit einem achtstelligen PIN. Kommunikation wird mit HKDF-SHA-512-abgeleiteten Schlüsseln auf Basis von per-Session Curve25519-Schlüsseln verschlüsselt – sowohl für IP- als auch BLE-Geräte.

**Warum das kritisch ist:** Man kann `httpx` oder `aiohttp` **nicht direkt** für HAP verwenden. Standard-HTTP-Libraries sind keine Option, weil es sehr schwierig ist, HAP-Session-Security ohne Monkey-Patches einzubinden, und weil sie keine Responses ohne vorangegangene Requests erwarten (d.h. Push-Events).

### 1.2 HAP Kommunikationsschichten

```
Applikation (GET /accessories, PUT /characteristics, ...)
       ↓
HTTP/1.1 Framing (HAP-JSON Content-Type)
       ↓
ChaCha20-Poly1305 Verschlüsselung (Session-Key)
       ↓
TCP-Stream (Port variabel, per mDNS angekündigt)
```

### 1.3 Pairing-Ablauf (einmalig, pro Gerät)

```
Controller (wir)                         Accessory (Gerät)
     │                                        │
     │── POST /pair-setup (M1: SRP Start) ──►│
     │◄─ SRP Salt + Public Key (M2) ─────────│
     │                                        │
     │  [PIN-Eingabe, SRP-Berechnung lokal]   │
     │                                        │
     │── SRP Public Key + Proof (M3) ────────►│
     │◄─ SRP Proof + verschlüsselte Ed25519   │
     │   Key Exchange (M4/M5/M6) ────────────│
     │                                        │
     │  [Ed25519-Schlüsselpaar wird gespeichert – PERSISTENT!]
     │                                        │
     ╔════════════════════════════════════════╗
     ║   Dauerhaft gepairt. PIN nie wieder    ║
     ║   nötig (solange Keys nicht verloren). ║
     ╚════════════════════════════════════════╝
```

### 1.4 Session-Aufbau (bei jedem Verbindungsstart)

```
POST /pair-verify (M1: Curve25519 Public Key)
     ↓
Accessory antwortet mit eigenem Curve25519 Key + verschlüsseltem Ed25519-Beweis
     ↓
Controller verifiziert, antwortet mit eigenem verschlüsselten Beweis
     ↓
Beide Seiten leiten Session-Key ab → ChaCha20-Poly1305 ab sofort aktiv
```

### 1.5 Relevante HAP-Endpunkte (nach Pair-Verify)

| Endpunkt | Methode | Zweck |
|----------|---------|-------|
| `/accessories` | GET | Alle Accessories mit Services und Characteristics |
| `/characteristics?id=AID.IID,...` | GET | Charakteristik-Werte lesen |
| `/characteristics` | PUT | Charakteristik-Werte schreiben (Steuern) |
| `/characteristics?id=AID.IID&ev=1` | GET | Event-Subscription (Push) |
| `/pair-setup` | POST | Erstes Pairing (TLV8-kodiert) |
| `/pair-verify` | POST | Session aufbauen (TLV8-kodiert) |
| `/pairings` | POST/GET/DELETE | Pairings verwalten |
| `/identify` | POST | Gerät identifizieren (blinken etc.) |

### 1.6 HAP Datenmodell

Ein Accessory ist die Repräsentation des physischen Geräts. Ein Accessory besteht aus mehreren Services. Ein Service ist eine Gruppierung von Funktionalität eines bestimmten Gerätetyps – bekannte Services sind Switch, Lightbulb oder Outlet. Ein Service besteht aus mehreren Characteristics, die die tatsächlichen Steuerpunkte sind.

```
Accessory (physisches Gerät, AID)
  └── Service (logische Funktion, IID)
        └── Characteristic (Wert/Aktion, IID)
              ├── value (aktueller Wert)
              ├── format (bool, uint8, float, string, ...)
              ├── perms (pr=read, pw=write, ev=events, ...)
              ├── minValue, maxValue, minStep
              └── unit (celsius, percentage, lux, ...)
```

**Wichtige Service-Typen:**

| Service | UUID | Wichtige Characteristics |
|---------|------|--------------------------|
| `AccessoryInformation` | `0000003E` | Name, Manufacturer, Model, SerialNumber, FirmwareRevision |
| `Lightbulb` | `00000043` | On (bool), Brightness (0–100), Hue (0–360), Saturation (0–100), ColorTemperature (mirek) |
| `Switch` | `00000049` | On (bool) |
| `Outlet` | `00000047` | On (bool), OutletInUse (bool) |
| `Thermostat` | `0000004A` | CurrentTemperature, TargetTemperature, CurrentHeatingCoolingState, TargetHeatingCoolingState |
| `LockMechanism` | `00000045` | LockCurrentState (0–3), LockTargetState (0–1) |
| `GarageDoorOpener` | `00000041` | CurrentDoorState (0–4), TargetDoorState (0–1), ObstructionDetected |
| `WindowCovering` | `0000008C` | CurrentPosition (0–100), TargetPosition (0–100), PositionState |
| `Fan` | `00000040` | On (bool), RotationSpeed (0–100), RotationDirection |
| `MotionSensor` | `00000085` | MotionDetected (bool) |
| `TemperatureSensor` | `0000008A` | CurrentTemperature (float, °C) |
| `HumiditySensor` | `00000082` | CurrentRelativeHumidity (0–100) |
| `ContactSensor` | `00000080` | ContactSensorState (0=closed, 1=open) |
| `LeakSensor` | `00000083` | LeakDetected (0/1) |
| `SmokeSensor` | `00000087` | SmokeDetected (0/1) |
| `AirQualitySensor` | `0000008D` | AirQuality (0–5), PM2_5Density, PM10Density |

### 1.7 Discovery via mDNS

HomeKit-Geräte kündigen sich via mDNS als `_hap._tcp.local` an. Der TXT-Record enthält: `c#` (Config-Nummer), `ff` (Feature Flags), `id` (Device-ID), `md` (Model Name), `pv` (Protocol Version), `s#` (State Number), `sf` (Status Flags, 0=paired), `ci` (Category Identifier).

```
Service: _hap._tcp.local
TXT: id=AA:BB:CC:DD:EE:FF  ← Device-ID (eindeutig, für Pairing-Keys)
     md=Eve Energy          ← Modellname
     ci=7                   ← Category: Outlet
     sf=0                   ← Status: bereits gepairt
     sf=1                   ← Status: bereit zum Pairen
```

---

## 2. Library-Analyse

### 2.1 Implementierungsstrategien im Vergleich

| Strategie | Eignung | Wann nutzen | Hauptnachteile |
|-----------|---------|-------------|----------------|
| **Python Controller mit `aiohomekit`** | ✅ Beste Option | Direkte lokale Kontrolle, kein HA-Dependency | Keine API-Garantien; Geräte-Quirks erfordern defensive Adapter |
| Home Assistant als Backend | Robust, breite Gerätekompatibilität | HA bereits betrieben; schnelle Kompatibilität | App steuert HA-Entities, nicht HomeKit direkt |
| macOS/iOS Swift-Sidecar | Bester Zugriff auf Apple-Home-DB | Rooms, Scenes, Shared-Homes, Apple-Home-Pairings | Kein reines Python; benötigt Apple-Plattform + HomeKit-Entitlement |
| `parker-aiohomekit`-Fork | Evaluierungskandidat | Modernisierte Alternative testen | Explizit inkompatibel zu `aiohomekit`, keine Garantien |
| `HAP-python` | Nur für Tests | Fake-Accessories für Integrationstests | Server-seitig; nicht zum Steuern bestehender Accessories |
| `homekit_python` / `pyhomekit` | Nur Inspiration | Protokoll-Ideen lesen | Basiert auf HAP-Release von 2017; `pyhomekit` pre-alpha |
| **HAP selbst implementieren** | ❌ Vermeiden | Nur als Protokollprojekt | Pairing, Encryption, Event-Sessions, mDNS, Vendor-Quirks – extrem hohes Risiko |

**Hinweis Apple HomeKit ADK:** Apple's HomeKit ADK wurde 2025 archiviert und ist read-only. Das ADK ist für Hersteller/Prototype-Accessory-Implementierungen, nicht für das Steuern bestehender Accessories. Nur als Protokoll-Hintergrund nutzen.

### 2.2 Aktiv gewartete Libraries

| Library | Sprache | Letzte Version | Status | Verwendung |
|---------|---------|---------------|--------|------------|
| **`aiohomekit`** | Python (async) | 3.2.20 (Mai 2025) | ✅ Aktiv | Home Assistant (Produktiv) |
| `parker-aiohomekit` | Python (async) | Fork von aiohomekit | ⚠️ Evaluierungskandidat | Explizit inkompatibel, keine Garantien |
| `homekit_python` (`homekit`) | Python (sync) | 2.0.0+ | ⚠️ Wenig aktiv | Referenz/Inspiration |
| `HAP-python` | Python | 4.x | ✅ Aktiv | Server-seitig (Accessory, nicht Controller) |
| `brutella/hap` | Go | v2 | ✅ Aktiv | Protokoll-Referenz |

### 2.3 Empfehlung: `aiohomekit` als direkte Dependency

**Abweichung vom Harmony-/Hue-Ansatz:** Anders als bei Harmony (undokumentiertes Protokoll, einfach zu reimplementieren) und Hue (standard HTTPS REST) ist HAP kryptografisch so komplex, dass eine Eigenimplementierung **nicht empfohlen wird**.

Gründe für `aiohomekit` als Dependency:
- Aktiv gewartete Library mit 235 Releases, zuletzt Juni 2025 (3.2.15). Ursprung in `homekit_python`, einer synchronen Referenzimplementierung beider Seiten.
- Produktionserprobt in Home Assistant mit Millionen von Installationen. Home Assistant pinnt `aiohomekit==3.2.20`.
- Korrekte Implementierung von SRP, HKDF, Curve25519, ChaCha20-Poly1305, Ed25519, TLV8 – alles korrekt zu reimplementieren würde Monate kosten und ist fehleranfällig.
- Viele Geräte interpretieren die HAP-Spezifikation locker – aiohomekit hat bekannte Geräte-Quirks (JSON-Whitespace, Boolean-Encoding, HTTP-Header-Reihenfolge) bereits abgefangen.

**Was wir selbst bauen:** Einen sauberen Adapter-Layer mit eigener `HomeKitBackend`-Schnittstelle (nur ein Ort im Code importiert aiohomekit), plus High-Level Entity-Model, CLI und MCP-Server.

**`homekit_python`** dient nur als Protokoll-Referenz, **nicht** als Dependency.

### 2.3 Bekannte Pitfalls

| Pitfall | Ursache | Mitigation |
|---------|---------|------------|
| **Pairingdaten-Verlust** | Ed25519-Keys verloren → Gerät muss zurückgesetzt werden | Keys sicher persistieren, Backup empfehlen |
| **Maximale Controller-Anzahl** | Die meisten Accessories unterstützen max. 16 gepairte Controller | Warnung wenn limit nahe; `homekit unpair` implementieren |
| **Keine Standard-HTTP-Library** | ChaCha20 auf TCP-Ebene, nicht TLS | `aiohomekit` löst das – nicht umgehen |
| **BLE-only Geräte** | Manche Geräte sprechen nur Bluetooth | Klare Fehlermeldung: IP-only Scope |
| **Bridges vs. direkte Geräte** | HomeKit-Hubs (HomePod, Apple TV) bridgen Geräte – dann ist der Hub der HAP-Endpunkt, nicht das Gerät | Discovery muss Bridges erkennen und transparent behandeln |
| **Geräte-Quirks** | Falsches JSON-Spacing, falsche Boolean-Typen, undokumentierte Felder | aiohomekit fängt bekannte Quirks ab |
| **`sf=0` bedeutet gepairt** | Gerät mit `sf=0` nimmt keine neuen Pairings an | Vor Pairing auf `sf=1` prüfen |
| **Verbindungslimit** | Viele Accessories erlauben nur 8–16 gleichzeitige Verbindungen | Verbindungen sparsam halten, on-demand bevorzugen |
| **Event-Verbindung** | Events nur über persistente Verbindung | Dedizierter Event-Loop, kein on-demand |
| **Charakteristik-Berechtigungen** | Nicht jede Characteristic ist schreibbar | `perms`-Feld vor Write prüfen |

---

## 3. Architektur

### 3.1 Paketstruktur

```
homekit-local-py/
│
├── homekit_local/
│   ├── __init__.py
│   ├── core/
│   │   ├── backend.py             # HomeKitBackend – stabile interne Schnittstelle
│   │   ├── models.py              # Frozen Dataclasses: Accessory, Entity, Characteristic, ...
│   │   ├── registry.py            # Entity-Registry: Aliases, Rooms, Domain-Mapping
│   │   ├── storage.py             # Pairing-Persistenz (keyring + verschlüsselte Datei)
│   │   ├── events.py              # State-Cache, Subscriptions, Polling-Fallback, Reconnect
│   │   └── policy.py              # Safety-Regeln für destruktive Operationen
│   ├── backends/
│   │   ├── __init__.py
│   │   └── aiohomekit_backend.py  # EINZIGER Ort der aiohomekit importiert
│   ├── config.py                  # pydantic-settings: Config, Env-Overrides, Pfade
│   ├── exceptions.py              # PairingError, AccessoryNotFoundError, NotPairedError, ...
│   ├── aliases.py                 # Semantische Shortcuts: turn_on, set_brightness, etc.
│   ├── discovery.py               # mDNS-Discovery (_hap._tcp.local) via zeroconf
│   ├── cache.py                   # Accessory-Config-Cache (~/.cache/homekit-local/)
│   ├── cli/
│   │   └── main.py                # Typer/Rich CLI
│   ├── mcp_server/
│   │   └── server.py              # MCP Tools und Ressourcen (FastMCP)
│   └── diagnostics/
│       ├── mdns.py                # mDNS-Sichtbarkeit und Service-Erkennung
│       ├── network.py             # IP/IPv6, VLANs, Interface-Check
│       └── pairability.py         # Pairing-Status, c#-Änderungen, Credential-Store
│
├── tests/
│   ├── unit/
│   │   ├── test_models.py
│   │   ├── test_aliases.py
│   │   ├── test_registry.py
│   │   └── test_config.py
│   ├── integration/
│   │   └── test_real_accessories.py   # opt-in via HOMEKIT_ACCESSORY_ID env
│   └── conftest.py
│
├── docs/
│   ├── pairing.md             # Pairing-Anleitung und Key-Backup
│   ├── protocol.md            # HAP-Grundlagen, AID/IID-Konzept
│   ├── entity-model.md        # Domain-Mapping, Entity-IDs, Registry
│   └── troubleshooting.md
│
└── pyproject.toml
```

### 3.2 Konfigurationspfade

| Zweck | Pfad |
|-------|------|
| Benutzerkonfiguration | `~/.config/homekit-local/config.toml` (Linux/macOS) |
| | `%APPDATA%\homekit-local\config.toml` (Windows) |
| **Pairingdaten (kritisch!)** | `~/.config/homekit-local/pairings/<device-id>.json` |
| Accessory-Config-Cache | `~/.cache/homekit-local/<device-id>/accessories.json` |

⚠️ **Pairingdaten sind nicht ersetzbar** ohne den physischen Reset-Knopf am Gerät. Das Verzeichnis sollte in Backups eingeschlossen werden.

### 3.3 Konfigurationsdatei (TOML)

```toml
[controller]
name = "homekit-local"          # Anzeigename dieses Controllers
id = ""                         # Wird automatisch generiert (Ed25519-Key-ID)

[discovery]
mdns_timeout_s = 5
ip_only = true                  # Nur IP-Accessories, keine BLE

[connection]
mode = "ondemand"               # ondemand | persistent
request_timeout_s = 10
event_reconnect_delay_s = 2
event_poll_fallback_s = 30      # Polling wenn Push nicht verfügbar

[cache]
ttl_seconds = 3600              # 1 Stunde Accessory-Config-Cache

[storage]
backend = "keyring"             # keyring (OS-Keychain) | file
# file-Modus: verschlüsselt, Permissions 0600
pairing_dir = ""                # leer = Standard-Pfad

[mcp]
default_mode = "read_only"      # read_only | read_write
allow_write_tools = true        # Schreib-Tools aktivieren (nur wenn read_write)
allow_raw_characteristic_writes = false   # Direkter AID/IID-Zugriff deaktiviert
bind_host = "127.0.0.1"
audit_log = true                # Alle Write-Tool-Aufrufe protokollieren

[dangerous_operations]
# Optionen: "allow" | "confirmation_required" | "disabled"
"lock.unlock" = "confirmation_required"
"garage.open" = "disabled"
"security_system.disarm" = "disabled"
"cover.open" = "allow"
```

**Konfigurationspriorität:** CLI-Argument > Umgebungsvariable > Config-Datei > Default

```bash
HOMEKIT_CONFIG_DIR=/custom/path    # Überschreibt Standard-Konfigurationspfad
HOMEKIT_PAIRING_DIR=/custom/path   # Überschreibt Pairing-Verzeichnis
```

---

## 4. Datenmodelle

```python
@dataclass(frozen=True)
class DiscoveredAccessory:
    """Durch mDNS entdecktes Gerät, noch nicht zwingend gepairt."""
    device_id: str              # AA:BB:CC:DD:EE:FF (eindeutiger Identifier)
    name: str
    model: str | None
    host: str
    port: int
    category: int               # HAP Category ID
    category_name: str          # "Lightbulb", "Outlet", "Thermostat", ...
    is_paired: bool             # sf=0 → gepairt, sf=1 → pairable
    config_number: int          # c# – ändert sich bei Konfigurationsänderung
    is_bridge: bool             # ci=2 (Bridge)

@dataclass(frozen=True)
class Characteristic:
    aid: int                    # Accessory Instance ID
    iid: int                    # Instance ID dieser Characteristic
    type_uuid: str              # z.B. "00000025" (On), "00000008" (Brightness)
    type_name: str | None       # "On", "Brightness", "CurrentTemperature", ...
    value: bool | int | float | str | None
    format: str                 # "bool", "uint8", "float", "string", ...
    perms: list[str]            # ["pr", "pw", "ev"] etc.
    unit: str | None            # "celsius", "percentage", "lux", ...
    min_value: float | None
    max_value: float | None
    min_step: float | None

@dataclass(frozen=True)
class Service:
    aid: int
    iid: int
    type_uuid: str
    type_name: str | None       # "Lightbulb", "Switch", "Thermostat", ...
    characteristics: tuple[Characteristic, ...]
    is_primary: bool

@dataclass(frozen=True)
class Accessory:
    aid: int
    device_id: str
    name: str
    services: tuple[Service, ...]

    def get_service(self, type_name: str) -> Service | None: ...
    def get_characteristic(self, type_name: str) -> Characteristic | None: ...

@dataclass(frozen=True)
class AccessoryPairing:
    device_id: str
    host: str
    port: int
    name: str
    paired_at: str              # ISO-8601 Zeitstempel
    # Die eigentlichen Keys sind in aiohomekit's Pairing-Storage

@dataclass(frozen=True)
class CharacteristicWriteResult:
    aid: int
    iid: int
    success: bool
    status: int | None          # HAP-Statuscode bei Fehler
    error: str | None

@dataclass(frozen=True)
class HapEvent:
    device_id: str
    aid: int
    iid: int
    characteristic_type: str | None
    value: bool | int | float | str | None
    timestamp: str

# ─── Entity-Modell (High-Level, automation-freundlich) ────────────────────────

@dataclass(frozen=True)
class EntityCapability:
    """Deklariert was eine Entity lesen/schreiben kann und wie sicher das ist."""
    domain: str                            # "light", "switch", "climate", "lock", ...
    readable: frozenset[str]               # Characteristic-Typen, die gelesen werden
    writable: frozenset[str]               # Characteristic-Typen, die geschrieben werden
    units: dict[str, str]                  # z.B. {"CurrentTemperature": "celsius"}
    enum_values: dict[str, dict[int, str]] # z.B. {"LockCurrentState": {0: "unsecured", 1: "secured"}}
    safety_class: Literal["safe", "caution", "dangerous"]

@dataclass(frozen=True)
class Entity:
    """Automation-freundliche Abstraktion über Raw-HAP-Modell."""
    entity_id: str             # z.B. "light.kitchen_ceiling", "lock.front_door"
    domain: str                # "light", "switch", "sensor", "climate", "lock", "cover", "fan"
    name: str                  # Anzeigename
    device_id: str             # Pairing-Device-ID (AA:BB:CC:DD:EE:FF)
    aid: int
    service_iid: int
    room: str | None           # Lokal gepflegter Raum-Name
    aliases: list[str]         # Alternative Bezeichnungen
    capability: EntityCapability

@dataclass(frozen=True)
class EntityState:
    """Aktueller Zustand einer Entity – mit Freshness-Metadaten."""
    entity_id: str
    state: str                 # "on" | "off" | "locked" | numerischer Wert als String
    attributes: dict           # z.B. {"brightness": 70, "color_temperature": 300}
    last_seen: str             # ISO-8601
    source: str                # "event" | "poll" | "cache"
    fresh: bool                # False wenn last_seen > poll_interval
```

---

## 5. Backend-Interface und Core Client API

### 5.1 Formale Backend-Schnittstelle (`core/backend.py`)

**Einziger Punkt** im Code, der weiß, dass aiohomekit existiert, ist `backends/aiohomekit_backend.py`. Alle anderen Module sprechen nur gegen dieses Interface:

```python
class HomeKitBackend(Protocol):
    """Stabile interne Schnittstelle – aiohomekit-unabhängig."""

    async def discover(self) -> list[DiscoveredAccessory]: ...
    async def pair(self, device_id: str, pin: str, alias: str) -> AccessoryPairing: ...
    async def unpair(self, device_id: str) -> None: ...

    async def list_accessories(self, device_id: str) -> list[Accessory]: ...
    async def list_entities(self) -> list[Entity]: ...

    async def read_characteristic(
        self, device_id: str, aid: int, iid: int
    ) -> Characteristic: ...
    async def write_characteristic(
        self, device_id: str, aid: int, iid: int, value: Any
    ) -> CharacteristicWriteResult: ...

    async def get_state(self, entity_id: str) -> EntityState: ...
    async def subscribe(
        self, entity_ids: list[str]
    ) -> AsyncIterator[HapEvent]: ...
```

Wenn aiohomekit eine Breaking Change einführt, muss nur `aiohomekit_backend.py` angepasst werden.

### 5.2 High-Level Client API (`HomeKitClient`)

```python
class HomeKitClient:
    """High-Level Controller für HomeKit Accessories über HAP."""

    async def connect(self, device_id: str) -> None: ...
    async def disconnect(self, device_id: str | None = None) -> None: ...

    # Discovery
    async def discover(self, timeout_s: float = 5.0) -> list[DiscoveredAccessory]: ...

    # Pairing
    async def pair(self, device_id: str, pin: str) -> AccessoryPairing: ...
    async def unpair(self, device_id: str) -> None: ...
    async def list_pairings(self) -> list[AccessoryPairing]: ...

    # Accessory-Konfiguration (gecacht)
    async def get_accessories(self, device_id: str, refresh: bool = False) -> list[Accessory]: ...

    # Entity-Modell (High-Level)
    async def list_entities(self) -> list[Entity]: ...
    async def get_entity(self, entity_id: str) -> Entity: ...
    async def get_state(self, entity_id: str) -> EntityState: ...

    # Characteristics direkt (Raw-Zugriff, hinter Config-Flag)
    async def get_characteristic(self, device_id: str, aid: int, iid: int) -> Characteristic: ...
    async def put_characteristic(self, device_id: str, aid: int, iid: int, value: Any) -> CharacteristicWriteResult: ...

    # Semantische Shortcuts (aliases.py)
    async def turn_on(self, entity_id: str) -> CharacteristicWriteResult: ...
    async def turn_off(self, entity_id: str) -> CharacteristicWriteResult: ...
    async def set_brightness(self, entity_id: str, value: float) -> CharacteristicWriteResult: ...
    async def set_color_temperature(self, entity_id: str, kelvin: int) -> CharacteristicWriteResult: ...
    async def set_hue_saturation(self, entity_id: str, hue: float, saturation: float) -> CharacteristicWriteResult: ...
    async def set_target_temperature(self, entity_id: str, celsius: float) -> CharacteristicWriteResult: ...
    async def set_lock(self, entity_id: str, locked: bool) -> CharacteristicWriteResult: ...
    async def set_position(self, entity_id: str, percent: int) -> CharacteristicWriteResult: ...
    async def identify(self, device_id: str) -> None: ...

    # Event-Stream
    async def subscribe(self, entity_ids: list[str]) -> None: ...
    async def listen(self, entity_id: str | None = None) -> AsyncIterator[HapEvent]: ...
```

Im Gegensatz zu Hue (zustandsloses REST) ist bei HAP jede Verbindung eine verschlüsselte Session mit Pair-Verify-Handshake. Das macht On-Demand teurer:

```
ON-DEMAND (pro Befehl):
  connect → Pair-Verify-Handshake (~100ms) → Befehl → disconnect
  Vorteil: Keine persistente Verbindung nötig
  Nachteil: Latenz bei jedem Befehl

PERSISTENT (empfohlen für Events):
  connect → Pair-Verify → Session offen halten → Befehle & Events
  Vorteil: Niedrige Latenz, Event-Push
  Nachteil: Zählt gegen Connection-Limit des Accessories
```

---

## 6. Aliases und Entity-Domain-Mapping

### 6.1 Service → Domain Mapping (`registry.py`)

HomeKit-Services werden auf automation-freundliche Domains gemappt:

| HAP Service | UUID | Domain | Entity-ID-Beispiel |
|-------------|------|--------|--------------------|
| `Lightbulb` | `00000043` | `light` | `light.kitchen_ceiling` |
| `Switch` | `00000049` | `switch` | `switch.coffee_machine` |
| `Outlet` | `00000047` | `switch` | `switch.power_strip` |
| `Thermostat` | `0000004A` | `climate` | `climate.hallway` |
| `TemperatureSensor` | `0000008A` | `sensor` | `sensor.living_room_temp` |
| `HumiditySensor` | `00000082` | `sensor` | `sensor.bedroom_humidity` |
| `MotionSensor` | `00000085` | `sensor` | `sensor.entrance_motion` |
| `ContactSensor` | `00000080` | `sensor` | `sensor.front_door_contact` |
| `LockMechanism` | `00000045` | `lock` | `lock.front_door` |
| `GarageDoorOpener` | `00000041` | `cover` | `cover.garage` |
| `WindowCovering` | `0000008C` | `cover` | `cover.bedroom_blind` |
| `Fan` / `FanV2` | `00000040` | `fan` | `fan.living_room` |
| `AirQualitySensor` | `0000008D` | `sensor` | `sensor.air_quality` |
| `LeakSensor` | `00000083` | `sensor` | `sensor.kitchen_leak` |

Unbekannte Services bleiben sichtbar (in `diagnostics`), werden aber nicht als Entity gemappt.

### 6.2 Semantische Shortcuts (`aliases.py`)

HAP-Characteristics haben gerätespezifische IIDs – die Library findet sie anhand des Service-Typs und Characteristic-Typs automatisch:

```python
# aliases.py – Mapping logischer Aktionen auf HAP-Characteristic-Types

TURN_ON_OFF = {
    "service_types": ["Lightbulb", "Switch", "Outlet", "Fan"],
    "characteristic": "On",       # UUID 00000025
}

SET_BRIGHTNESS = {
    "service_types": ["Lightbulb"],
    "characteristic": "Brightness",   # UUID 00000008, uint8, 0–100
}

SET_COLOR_TEMPERATURE = {
    "service_types": ["Lightbulb"],
    "characteristic": "ColorTemperature",  # UUID 000000CE, mirek
}

SET_TARGET_TEMPERATURE = {
    "service_types": ["Thermostat"],
    "characteristic": "TargetTemperature",  # UUID 00000035, float, °C
}

SET_LOCK = {
    "service_types": ["LockMechanism"],
    "characteristic": "LockTargetState",   # UUID 0000001E, 0=unsecured, 1=secured
}
```

**Kelvin → Mirek Konvertierung** (für Farbtemperatur):
```python
def kelvin_to_mirek(k: int) -> int:
    return round(1_000_000 / k)   # z.B. 2700K → 370 mirek, 6500K → 154 mirek
```

---

## 7. CLI-Interface

Technologie: **Typer** + **Rich**

```bash
# Discovery
homekit discover [--timeout 5]
homekit discover --json

# Pairing (einmalig pro Gerät)
homekit pair <device-id> --pin 123-45-678 --alias "Wohnzimmer Hue"
homekit unpair <device-id>
homekit pairings list
homekit pairings export --out pairings-backup.json
homekit pairings import pairings-backup.json

# Geräte und Entities (High-Level)
homekit devices                        # Alle gepairteten Geräte
homekit entities                       # Alle Entities (light.*, switch.*, ...)
homekit entity light.kitchen_ceiling   # Status einer Entity

# Status lesen (Entity-basiert)
homekit get light.kitchen_ceiling
homekit get climate.hallway
homekit get lock.front_door

# Steuern (Entity-basiert, semantische Shortcuts)
homekit set light.kitchen_ceiling on
homekit set light.kitchen_ceiling brightness=75
homekit set light.kitchen_ceiling color_temp=2700
homekit set climate.hallway target_temperature=21.5
homekit set lock.front_door locked=true
homekit set cover.bedroom_blind position=50

# Kurzformen
homekit on light.kitchen_ceiling
homekit off light.kitchen_ceiling
homekit brightness light.kitchen_ceiling 75
homekit color-temp light.kitchen_ceiling 2700
homekit temperature climate.hallway 21.5
homekit lock lock.front_door
homekit unlock lock.front_door
homekit position cover.bedroom_blind 50
homekit identify <device-id>

# Events beobachten
homekit watch light.kitchen_ceiling sensor.living_room_temp
homekit watch --all

# Roher Characteristic-Zugriff (explizit, hinter Config-Flag)
homekit raw read <device-id> <aid> <iid>
homekit raw write <device-id> <aid> <iid> <json-value>
# raw write nur wenn allow_raw_characteristic_writes = true

# Accessory-Struktur (Low-Level, Debugging)
homekit accessories <device-id>        # Alle Services + Characteristics
homekit services <device-id>
homekit characteristics <device-id>

# Diagnose
homekit diagnose mdns                  # mDNS-Sichtbarkeit, _hap._tcp.local, _hap._udp.local
homekit diagnose network               # IP/IPv6-Status, VLAN/Subnet-Check, Interfaces
homekit diagnose pairability           # Pairing-State, c#-Änderungen, sf-Flags
homekit diagnose storage               # Credential-Store-Erreichbarkeit, Key-Integrität
homekit diagnose mcp-security          # MCP-Konfiguration, Bind-Host, Audit-Log
homekit diagnose [--all]               # Alle obigen Checks in einem Lauf
```

### CLI-Designregeln

- `--json` für alle relevanten Befehle.
- Entity-IDs (`light.kitchen`) als primäres Adressierungsschema; Device-IDs (`AA:BB:CC:DD:EE:FF`) für Low-Level-Befehle.
- **Alle Logs auf `stderr`** – stdout nur für Ausgaben.
- `homekit raw write` erfordert `allow_raw_characteristic_writes = true` in der Config.
- Policy-Gates: `dangerous_operations` werden vor Ausführung geprüft; `confirmation_required` fragt interaktiv nach.

### CLI Exit-Codes

| Code | Bedeutung |
|------|-----------|
| `0` | Erfolg |
| `2` | Usage-/Validierungsfehler |
| `10` | Accessory nicht erreichbar |
| `11` | Nicht gepairt (`homekit pair` ausführen) |
| `12` | Pairing fehlgeschlagen (falscher PIN / bereits gepairt) |
| `13` | Characteristic nicht gefunden / nicht schreibbar |
| `14` | Verbindungslimit überschritten |
| `15` | Pairingdaten korrupt oder fehlen |
| `16` | Operation durch Policy gesperrt (`dangerous_operations`) |

---

## 8. MCP-Server

Technologie: **FastMCP** (offizielles MCP Python SDK).
Default-Transport: **stdio**. MCP **read-only by default** – Schreib-Tools nur wenn `[mcp].allow_write_tools = true`.

### 8.1 FastMCP Lifespan-Pattern

```python
from contextlib import asynccontextmanager
from mcp.server.fastmcp import FastMCP

@asynccontextmanager
async def lifespan(app: FastMCP):
    service = await HomeKitService.create_from_config()
    await service.start()
    try:
        yield {"homekit": service}
    finally:
        await service.stop()

mcp = FastMCP("homekit-local", lifespan=lifespan)
```

Alle Tools und Resources greifen auf den im Lifespan erstellten `HomeKitService` zu.

### 8.2 MCP Resources (GET-artige Datenzugriffe)

| Resource URI | Inhalt |
|---|---|
| `homekit://devices` | Alle gepairteten Geräte |
| `homekit://devices/{device_id}` | Ein Gerät mit Services |
| `homekit://entities` | Alle Entities mit Domain und Capability |
| `homekit://entities/{entity_id}` | Eine Entity |
| `homekit://state/{entity_id}` | Aktueller Status mit Freshness-Metadaten |
| `homekit://capabilities/{entity_id}` | Was eine Entity lesen/schreiben kann |
| `homekit://events/recent` | Zuletzt empfangene Events |

**Freshness in State-Responses** (stale Werte klar ausweisen):

```json
{
  "entity_id": "light.kitchen_ceiling",
  "state": "on",
  "attributes": { "brightness": 70, "color_temperature": 300 },
  "last_seen": "2026-05-14T10:15:20Z",
  "source": "event",
  "fresh": true
}
```

Wenn `fresh = false`: explizite Metadaten statt Vortäuschung von Aktualität.

### 8.3 MCP Tools (Operationen mit Seiteneffekten)

**Typisierte, domänen-spezifische Tools statt generischem Characteristic-Writer:**

| Tool | Beschreibung | Safety |
|------|-------------|--------|
| `homekit_list_entities` | Alle Entities | safe |
| `homekit_get_state` | Status einer Entity | safe |
| `homekit_set_light` | Licht steuern (on, brightness, color_temperature) | safe |
| `homekit_set_switch` | Schalter an/aus | safe |
| `homekit_set_climate` | Thermostat-Zieltemperatur und Modus | caution |
| `homekit_set_cover` | Rolllade/Jalousie (position, action) | caution |
| `homekit_lock` | Schloss sperren | caution |
| `homekit_unlock` | Schloss entsperren (mit confirmation_token) | dangerous |
| `homekit_identify` | Gerät blinken lassen | safe |

```python
@mcp.tool()
async def homekit_set_light(
    entity_id: str,
    on: bool | None = None,
    brightness: int | None = None,        # 0–100
    color_temperature: int | None = None, # Kelvin
) -> dict: ...

@mcp.tool()
async def homekit_unlock(
    entity_id: str,
    confirmation_token: str,              # Explizite Bestätigung erforderlich
) -> dict: ...
```

**Nicht standardmäßig exponiert** (nur in Debug-Profil):
```python
# homekit_write_characteristic(device_id, aid, iid, value)
# → Zu mächtig für LLM-Nutzung; nur wenn allow_raw_characteristic_writes = true
```

### 8.4 MCP Transport

```bash
# stdio (Default, für lokale Desktop-Clients)
homekit-mcp --transport stdio

# Streamable HTTP (nur nach Auth + Logging implementiert)
homekit-mcp --transport streamable-http --host 127.0.0.1 --port 8765
```

Nicht auf `0.0.0.0` binden. Für Remote-Zugriff: Authentifizierung, Authorization, Audit-Logging.

### 8.5 Claude Desktop Integration

```json
// ~/.claude/claude_desktop_config.json
{
  "mcpServers": {
    "homekit": {
      "command": "homekit-mcp",
      "args": [],
      "env": {
        "HOMEKIT_CONFIG_DIR": "/home/user/.config/homekit-local"
      }
    }
  }
}
```

### 8.6 MCP-Sicherheitsregeln

- **Kein Logging auf stdout** – bricht STDIO-Protokoll.
- Pairingdaten (Ed25519-Keys) nie in Tool-Antworten ausgeben.
- `default_mode = "read_only"` – Schreib-Tools nur explizit aktivieren.
- `audit_log = true` – alle Write-Tool-Aufrufe protokollieren.
- `dangerous_operations`-Policy wird auch für MCP-Tool-Aufrufe durchgesetzt.
- Kein generischer Raw-Characteristic-Writer standardmäßig exponiert.

---

## 9. Implementierungsphasen

### Phase 0 – Projekt-Setup

| # | Aufgabe | Details |
|---|---------|---------|
| 0.1 | Repository + `pyproject.toml` | Python ≥ 3.11, Entry Points |
| 0.2 | Paketstruktur anlegen | Alle Module als leere Dateien mit Docstrings |
| 0.3 | Tooling konfigurieren | `ruff`, `mypy`, `pytest`, `pytest-asyncio` |
| 0.4 | Config-Pfade implementieren | XDG-konform via `platformdirs`, plattformübergreifend |
| 0.5 | Entry Points prüfen | `homekit --help`, `homekit-mcp --help` |

**Akzeptanzkriterium:** `pip install -e .` funktioniert, `pytest` läuft fehlerfrei.

### Phase 1 – Discovery

| # | Aufgabe | Details |
|---|---------|---------|
| 1.1 | `discovery.py` via `zeroconf` | `_hap._tcp.local` browsen |
| 1.2 | TXT-Record parsen | `id`, `md`, `ci`, `sf`, `c#` extrahieren |
| 1.3 | `DiscoveredAccessory` befüllen | Category-Name aus `ci` ableiten |
| 1.4 | Bridge-Erkennung | `ci=2` → Bridge, transparent behandeln |
| 1.5 | `sf=0` Warnung | "Bereits gepairt, kein Pairing möglich" |

**Akzeptanzkriterium:** `homekit discover` listet alle IP-Accessories im Netzwerk.

### Phase 2 – Pairing und Key-Verwaltung

| # | Aufgabe | Details |
|---|---------|---------|
| 2.1 | `pairing.py` – Pairing auslösen | Über `aiohomekit` Controller, PIN-Eingabe |
| 2.2 | Pairingdaten persistieren | Sicher in `~/.config/homekit-local/pairings/<id>.json` |
| 2.3 | Pairingdaten laden | Beim Start, `NotPairedError` wenn fehlt |
| 2.4 | `homekit pair` CLI-Command | PIN als Argument oder interaktiv |
| 2.5 | `homekit unpair` | Pairing löschen (lokale Keys + Unpair-Request an Gerät) |
| 2.6 | Import/Export | `pairings export/import` für Backup |
| 2.7 | Fehlerklassen | `PairingError`, `AlreadyPairedError`, `NotPairableError` |

**Akzeptanzkriterium:** `homekit pair AA:BB:CC:DD:EE:FF --pin 123-45-678` pairt Gerät. Keys persistent. `homekit pairings list` zeigt gepairtete Geräte.

### Phase 3 – HAP-Verbindung und Characteristic-Zugriff

| # | Aufgabe | Details |
|---|---------|---------|
| 3.1 | `backends/aiohomekit_backend.py` | aiohomekit Controller-API kapseln, `HomeKitBackend`-Interface implementieren |
| 3.2 | Session aufbauen | Pair-Verify über aiohomekit, Session-Key |
| 3.3 | `GET /accessories` | Alle Accessories laden und normalisieren |
| 3.4 | `GET /characteristics` | Einzelne und mehrere Werte lesen |
| 3.5 | `PUT /characteristics` | Werte schreiben, Fehler auswerten |
| 3.6 | `cache.py` | Accessory-Config cachen, invalidieren bei `c#`-Änderung |
| 3.7 | Name-Resolver | Service/Characteristic nach Typ-Name oder UUID suchen |

**Akzeptanzkriterium:** `homekit accessories <id>` zeigt alle Services. `homekit get` liest Werte korrekt.

### Phase 4 – Entity-Mapping und Registry

| # | Aufgabe | Details |
|---|---------|---------|
| 4.1 | `registry.py` – Service→Domain Mapping | Lightbulb→light, Thermostat→climate, etc. |
| 4.2 | Entity-ID-Generierung | `{domain}.{accessory_name}` normalisiert |
| 4.3 | `EntityCapability` | readable/writable/units/enum_values/safety_class |
| 4.4 | Alias-System | `alias` und `room` pro Entity in Registry konfigurierbar |
| 4.5 | Unbekannte Services | In Diagnostics sichtbar halten, kein Mapping |

**Akzeptanzkriterium:** `homekit entities` zeigt `light.kitchen_ceiling`, `lock.front_door` etc. `homekit entity light.kitchen_ceiling` zeigt Capabilities.

### Phase 5 – Semantische Steuerung und Policy

| # | Aufgabe | Details |
|---|---------|---------|
| 5.1 | `aliases.py` | Service/Characteristic-Mapping, Kelvin→Mirek |
| 5.2 | `turn_on()` / `turn_off()` | Für Lightbulb, Switch, Outlet, Fan |
| 5.3 | `set_brightness()` | uint8, 0–100, Validierung |
| 5.4 | `set_color_temperature()` | Kelvin→Mirek, Bereichsprüfung |
| 5.5 | `set_hue_saturation()` | float, Hue 0–360, Sat 0–100 |
| 5.6 | `set_target_temperature()` | float, °C, min/max aus Characteristic |
| 5.7 | `set_lock()` / `unlock()` | LockTargetState 0/1 |
| 5.8 | `set_position()` | WindowCovering, 0–100% |
| 5.9 | `policy.py` | `dangerous_operations` aus Config, Confirmation-Flow |

**Akzeptanzkriterium:** `homekit set light.kitchen on`, `homekit set climate.hallway target_temperature=21.5`. `homekit unlock lock.front_door` wird von Policy abgefangen.

### Phase 6 – Events, State-Cache und Reconnect

| # | Aufgabe | Details |
|---|---------|---------|
| 6.1 | `events.py` – Subscription Manager | `GET /characteristics?ev=1` über aiohomekit |
| 6.2 | State-Cache | Letzter bekannter Wert pro Entity, `last_seen` + `source` + `fresh` |
| 6.3 | Polling-Fallback | Wenn Push nicht stabil: konfigurierbares Intervall |
| 6.4 | mDNS-Rediscovery | Bei IP/Port-Änderung des Accessories automatisch neu verbinden |
| 6.5 | Exponential Backoff | Reconnect-Logik bei Verbindungsabbruch |
| 6.6 | `homekit watch` CLI | Events/State-Updates in Echtzeit ausgeben |

**Akzeptanzkriterium:** `homekit watch light.kitchen_ceiling` zeigt Events bei manuellem Schalten. State-Cache liefert Freshness-Metadaten.

### Phase 7 – CLI

| # | Aufgabe | Details |
|---|---------|---------|
| 7.1 | Typer-App strukturieren | Subcommands, `--json`, `--verbose` |
| 7.2 | Entity-basierte Commands | `homekit get`, `homekit set`, `homekit on/off/brightness/...` |
| 7.3 | `homekit diagnose` | 5 Subcommands: `mdns`, `network`, `pairability`, `storage`, `mcp-security` |
| 7.4 | `homekit raw` | Read/Write hinter Config-Flag `allow_raw_characteristic_writes` |
| 7.5 | Rich-Output | Tabellen für Entity-Liste, Status-Badges, Freshness-Indikator |
| 7.6 | Exit-Codes | 0 / 2 / 10–16 |
| 7.7 | Logs auf stderr | Niemals auf stdout |

**Akzeptanzkriterium:** Alle Funktionen per CLI, `--json` überall, `homekit diagnose --all` zeigt Pass/Fail.

### Phase 8 – MCP-Server

| # | Aufgabe | Details |
|---|---------|---------|
| 8.1 | `mcp_server/server.py` | FastMCP, Lifespan-Pattern, `HomeKitService`-Singleton |
| 8.2 | STDIO-Transport | Default |
| 8.3 | Resources implementieren | `homekit://entities`, `homekit://state/*`, `homekit://capabilities/*` |
| 8.4 | Domänen-spezifische Tools | `homekit_set_light`, `homekit_set_climate`, `homekit_lock`, `homekit_unlock` |
| 8.5 | Policy-Enforcement | `dangerous_operations` auch für MCP-Tool-Aufrufe |
| 8.6 | `audit_log` | Write-Tool-Aufrufe auf stderr protokollieren |
| 8.7 | Kein Raw-Writer standardmäßig | Nur in Debug-Profil |

**Akzeptanzkriterium:** MCP Inspector listet alle Tools. Claude Desktop kann Lampen schalten. Unlock erfordert Confirmation-Token.

### Phase 9 – Tests

**Ziel:** 80%+ Testabdeckung ohne echte Hardware, opt-in Integrationstests.

| # | Aufgabe | Details |
|---|---------|---------|
| 9.1 | `simulator.py` | Fake-Accessory via `HAP-python` (Server-Seite) |
| 9.2 | Simulator-Szenarien | Lightbulb, Thermostat, Lock – je mit Events und Policy-Tests |
| 9.3 | `test_models.py` | Dataclass-Validierung, EntityCapability, EntityState-Freshness |
| 9.4 | `test_aliases.py` | Kelvin→Mirek, Service-Lookup, Domain-Mapping |
| 9.5 | `test_registry.py` | Entity-ID-Generierung, Alias-System, unbekannte Services |
| 9.6 | `test_config.py` | TOML-Laden, Env-Overrides, pydantic-settings |
| 9.7 | `test_discovery.py` | mDNS mit gemocktem zeroconf |
| 9.8 | `test_cli_commands.py` | Typer-Testclient, Snapshot-Tests |
| 9.9 | `test_mcp_tools.py` | FastMCP-Testclient, Policy-Enforcement, Audit-Log |
| 9.10 | `test_real_accessories.py` | `HOMEKIT_ACCESSORY_ID=... pytest -m integration` |

**Akzeptanzkriterium:** 80%+ Coverage. Simulator läuft ohne echte Hardware. Integration opt-in.

### Phase 10 – Dokumentation

| # | Aufgabe | Details |
|---|---------|---------|
| 10.1 | `README.md` | Installation, Pairing-Anleitung, CLI-Beispiele, MCP-Setup |
| 10.2 | `docs/pairing.md` | Pairing-Flow, Apple-Home-Ablösung, Key-Backup, verlorene Keys |
| 10.3 | `docs/protocol.md` | HAP-Grundlagen, AID/IID, Characteristic-Types, TLV8 |
| 10.4 | `docs/entity-model.md` | Domain-Mapping, Entity-IDs, Registry-Konfiguration |
| 10.5 | `docs/troubleshooting.md` | mDNS/VLAN, Verbindungslimit, Geräte-Quirks, stale State |

### Phase 11 – Thread und BLE (Zukünftig, nach stabilem IP-Betrieb)

**Erst nach vollständig stabilem IP-Pfad angehen:**

| Transport | Zusätzliche Anforderungen |
|-----------|--------------------------|
| **Thread** | IPv6-Support, `_hap._udp.local`-Discovery, CoAP-Transport, Border-Router-Awareness, komplexerer Provisioning-Flow |
| **BLE** | BLE-Adapter-Auswahl, Sleeping-Accessory-Wake, längere Timeouts, langsamere State-Reads/Writes, Battery-Device-Policy |

---

## 10. Abhängigkeiten

```toml
[project]
name = "homekit-local-py"
requires-python = ">=3.12"

dependencies = [
    "aiohomekit==3.2.20",    # Exakt gepinnt – keine API-Garantien; upgrade bewusst
    "mcp>=1.27,<2",          # Offizielles MCP Python SDK (v1.x-Linie)
    "typer>=0.16",           # CLI
    "rich>=13",              # Output-Formatierung
    "pydantic-settings>=2",  # Strukturierte Config aus TOML, Env, Secrets
    "keyring>=25",           # OS-Keychain für Pairing-Material
    "zeroconf>=0.131",       # mDNS-Discovery (_hap._tcp.local)
    "platformdirs>=4.0",     # Plattformübergreifende Config-/Cache-Pfade
    # tomllib in stdlib ab Python 3.11
]

[project.optional-dependencies]
simulator = ["HAP-python>=4.0"]   # Nur für Tests: Fake-Accessory bauen

[tool.pytest.ini_options]
asyncio_mode = "auto"

[project.scripts]
homekit = "homekit_local.cli.main:app"
homekit-mcp = "homekit_local.mcp_server.server:main"
```

**Versionsstrategie:**
- `aiohomekit` exakt pinnen (`==3.2.20`) – kein `~=`, weil keine API-Garantien
- `mcp` mit `<2` pinnen – aktive v1.x-Linie, v2 in Entwicklung
- Bei aiohomekit-Upgrade: erst Backend-Tests prüfen, dann upgraden

---

## 11. Risiken und Gegenmaßnahmen

| Risiko | Auswirkung | Gegenmaßnahme |
|--------|------------|---------------|
| **Pairingdaten-Verlust** | Gerät muss physisch zurückgesetzt werden | keyring + Backup-Doku, Export-Command, klare Warnung |
| **`aiohomekit`-Breaking-Change** | Library-Update bricht API | Exakt pinnen (`==3.2.20`), nur `aiohomekit_backend.py` importiert Library |
| **Gerät bereits mit Apple Home gepairt** | Unser Controller kann nicht pairen (`sf=0`) | Gerät erst aus Apple Home entfernen; `homekit diagnose pairability` zeigt das an |
| **Gerät interpretiert HAP locker** | Unerwartete Fehler | aiohomekit fängt Quirks ab; `homekit diagnose` hilft debuggen |
| **BLE-only Gerät** | Nicht per IP erreichbar | Frühe Fehlermeldung: "IP-only scope" in Phase 1 |
| **Verbindungslimit erreicht** | Neue Verbindung abgelehnt | On-Demand bevorzugen, Limit dokumentieren |
| **mDNS über VLANs** | Accessory "unsichtbar" obwohl pingbar | `homekit diagnose network` prüft Subnet, VLAN, IGMP-Snooping |
| **`c#`-Änderung** | Accessory-Config hat sich geändert | Cache invalidieren, automatisch neu laden |
| **Stale State im MCP** | LLM sieht veraltete Werte als aktuell | `fresh`-Flag + `last_seen` in allen State-Responses |
| **Apple-Home-Metadaten fehlen** | Räume, Scenes, Shared-Users nicht sichtbar | Eigene `room`-Registry pflegen; klar dokumentieren, was nicht verfügbar ist |
| **PIN verloren** | Pairing ohne Reset unmöglich | `homekit diagnose pairability` + Vendor-Doku verlinken |
| **Policy umgangen** | Unbeabsichtigtes Lock-Unlock durch LLM | `audit_log`, Confirmation-Token für dangerous ops, MCP default read-only |
| **Event-Verbindung fällt ab** | Verpasste Events | Polling-Fallback + Auto-Reconnect + mDNS-Rediscovery bei IP-Änderung |

---

## 12. MVP-Scope

**Im MVP enthalten:**

1. mDNS-Discovery (`_hap._tcp.local`)
2. Pairing mit 8-stelligem PIN, sichere Key-Persistenz via `keyring`
3. Session-Aufbau (über aiohomekit, nur in `aiohomekit_backend.py`)
4. Formale `HomeKitBackend`-Schnittstelle (aiohomekit entkoppelt)
5. Accessories und Characteristics lesen (gecacht, `c#`-aware)
6. Entity-Registry: Domain-Mapping, Entity-IDs, `EntityCapability`
7. Semantische Shortcuts: `on`, `off`, `brightness`, `color-temp`, `temperature`, `lock`
8. `policy.py`: `dangerous_operations` aus Config, Confirmation-Flow
9. State-Cache mit `fresh`/`last_seen`/`source`-Metadaten
10. CLI für alle MVP-Funktionen inkl. `homekit diagnose` (5 Subcommands)
11. `homekit raw` hinter Config-Flag
12. MCP STDIO Server: read-only by default, domänen-spezifische Tools, Resources, audit_log
13. FastMCP Lifespan-Pattern
14. Simulator (via HAP-python) für Tests ohne Hardware

**Explizit zurückgestellt:**

- BLE-Accessories (Phase 11)
- Thread/CoAP-Transport (Phase 11)
- Apple-Home-Datenbankintegration (Rooms, Scenes, Shared Users)
- Scenes/Automations
- Remote MCP HTTP mit Authorization
- Generischer Raw-Characteristic-Writer im MCP
- Smart-Home-Bridges als primärer Anwendungsfall (erst IP-Direktzugriff stabilisieren)

---

## 13. Abgrenzung zu Harmony und Hue

| Aspekt | Harmony Hub | Hue Bridge | HomeKit (HAP) |
|--------|------------|-----------|--------------|
| Protokoll-Komplexität | Mittel (undokumentiert, aber einfach) | Niedrig (HTTPS REST) | **Sehr hoch** |
| Externe Crypto-Library nötig? | Nein | Nein | **Ja (aiohomekit)** |
| Gerätebereich | 1 Hub → alles | 1 Bridge → Hue-Geräte | **N Accessories direkt** |
| Status-Zuverlässigkeit | Niedrig (Harmony-Zustand) | Hoch (echte Gerätewerte) | **Hoch (echte Werte)** |
| Pairing-Komplexität | Einmalig (HTTP POST) | Einmalig (Link-Button) | **Einmalig + Key-Management** |
| Größtes Risiko | Firmware-Änderung | TLS-Zertifikat | **Pairingdaten-Verlust** |

---

## 14. Beispiel-Nutzung (Library)

```python
import asyncio
from homekit_local import HomeKitClient

async def main():
    async with HomeKitClient() as homekit:
        # Discovery
        devices = await homekit.discover(timeout_s=5)
        for d in devices:
            status = "gepairt" if not d.is_paired else "pairable"
            print(f"  {d.name} ({d.category_name}) – {d.device_id} – {status}")

        # Gerät ansprechen (muss bereits gepairt sein)
        device_id = "AA:BB:CC:DD:EE:FF"

        # Alle Accessories und Services anzeigen
        accessories = await homekit.get_accessories(device_id)
        for acc in accessories:
            for svc in acc.services:
                print(f"  Service: {svc.type_name}")
                for char in svc.characteristics:
                    print(f"    {char.type_name}: {char.value} {char.unit or ''}")

        # Steuern via semantische Shortcuts
        await homekit.turn_on(device_id)
        await homekit.set_brightness(device_id, 60.0)
        await homekit.set_color_temperature(device_id, 2700)  # Kelvin
        await asyncio.sleep(2)

        # Thermostat
        therm_id = "BB:CC:DD:EE:FF:00"
        await homekit.set_target_temperature(therm_id, 21.5)

        # Direkter Characteristic-Zugriff
        char = await homekit.get_characteristic(device_id, aid=1, iid=10)
        print(f"On: {char.value}")

        # Events beobachten
        await homekit.subscribe(device_id, [(1, 10), (1, 11)])
        async with asyncio.timeout(10):
            async for event in homekit.listen(device_id):
                print(f"Event: {event.characteristic_type} = {event.value}")

asyncio.run(main())
```

---

*Erstellt: Mai 2026 | Protokoll: Apple HomeKit Accessory Protocol (HAP) | Primäre Dependency: aiohomekit ≥3.2*
