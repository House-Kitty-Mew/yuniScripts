# Datagram Project
## A Forward/Backward Compatible Data Archival System

### Overview
Datagram is a structured data archival format designed for extreme longevity and compatibility across decades or even centuries. The core concept is that a datagram file (or directory structure) can be opened by any compatible client, regardless of version differences, by embedding function definitions within the datagram itself or using client-registered local functions.

### Key Features
- **Version Agnostic**: Old clients (e.g., version 1.0.0) can open newer datagrams by importing embedded function definitions.
- **Self‑Contained**: Datagrams can carry their own viewer, loader, and UI components.
- **Extensible**: Supports arbitrary data types (images, databases, documents) through a plugin‑like function system.
- **Integrity & Security**: Built‑in hashing (SHAKE256) and optional public‑key encryption.
- **GUI‑Ready**: Includes a declarative GUI definition for immediate visualization.

### Directory Structure
```
Datagram/
├── Meta/                    # Metadata and versioning
│   ├── Base.ini            # Core identity, hash, encryption
│   ├── DatagramMeta.ini    # Extended metadata (custom fields)
│   └── FunctionsReqVersions.ini # Required versions of loader, viewer, buttons
├── Databases/              # Structured data (e.g., SQLite, key‑value)
│   └── Default/            # Default database namespace
│       └── Data/           # Actual database files (optional)
├── LargeAssets/            # Bulk binary assets (images, audio, video)
└── PreLoad/                # Components loaded before GUI
    ├── Gui/                # GUI definition
    │   └── Default_Gui.ini # Screen, buttons, image mappings
    └── Intil/              # Initialization scripts (optional)
```

### Forward/Backward Compatibility Mechanism
1. **Function Versioning**: Each datagram declares the minimum required version of its loader, viewer, and button handlers.
2. **Embedded Functions**: A datagram can optionally include the actual function code (scripts, binaries) needed to interpret its data.
3. **Client Fallback**: If the client lacks a required function, it can either:
   - Use a locally registered version (if compatible),
   - Download the function from a trusted source (URL specified in metadata),
   - Parse the embedded function definition and load it dynamically.
4. **GUI Adaptation**: The GUI definition is versioned separately; clients can render a simplified interface if advanced features are missing.

### Configuration Files Explained

#### `Meta/Base.ini`
- `[Datagram Version]` – Format version (semantic versioning).
- `[Datagram NAME ID]` – Human‑readable title.
- `[Datagram Author]` – Creator identification.
- `[Datagram Hashing Algo]` – Hash algorithm identifier (1 = SHAKE256‑1024).
- `[Datagram Hash UQID]` – Unique hash of the datagram’s content (prevents tampering).
- `[Encryption]` – 0 = none, 1 = public‑key encryption.
- `[Encryption Public Key]` – Public key or placeholder.
- `[Encryption Server URL]` – Optional endpoint to obtain the decryption key.

#### `Meta/FunctionsReqVersions.ini`
- `[Datagram Loader Version]` – Minimum loader version required.
- `[Image Viewer Version]` – Minimum image‑viewer version.
- `[Buttons Version]` – Minimum button‑handler version.

#### `PreLoad/Gui/Default_Gui.ini`
- Defines screens, buttons, and image mappings using a simple declarative syntax.
- Screens are linked to functions (e.g., `Func:ImageViewer`, `Func:Buttons`).
- Buttons can trigger actions (e.g., `ExitAppSafely`).
- Images are referenced from the database (`DB:Default, IMGMETA:{…}`).

### Usage Example
1. **Create a Datagram**: Populate the directories with your data, update the `.ini` files accordingly.
2. **Package**: Optionally compress the whole folder into a single archive (e.g., `.datagram`).
3. **Distribute**: Share the datagram file; recipients with any compatible client can open it.
4. **Open**: The client reads the metadata, verifies the hash, loads the required functions (either locally or from the datagram), and renders the GUI.

### Development Notes
- This folder is a **template/specification**. Implementations of the loader, viewer, and button handlers are not included here.
- The empty directories (`Databases/Default/Data`, `LargeAssets`, `PreLoad/Intil`) are placeholders for your actual content.
- To make a functional datagram, you need to write the actual function scripts (PowerShell, Python, C#, etc.) and place them in the appropriate locations (e.g., `PreLoad/Intil/` for initialization scripts).
- The forward/backward compatibility relies on a well‑defined function‑API; design your functions with versioning in mind.

### Future Work
- Define a standard API for loader, viewer, and button functions.
- Create reference implementations in multiple languages.
- Develop a command‑line tool for creating, validating, and extracting datagrams.
- Specify a binary container format for single‑file distribution.

---
*Last updated: January 11, 2026*  
*Project maintained by TrueGearsWorks*