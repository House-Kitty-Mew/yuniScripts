"""snapshot_manager.py — Core Script Snapshot Manager."""
import json, os, shutil, hashlib, fnmatch, time
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from .compile_config import CompileConfig, get_default_compile_config, save_compile_config, load_compile_config
from .decompile_config import DecompileConfig, get_default_decompile_config, save_decompile_config, load_decompile_config

@dataclass
class SnapshotResult:
    success: bool; script_id: str; datagram_path: str
    file_count: int = 0; hash: str = ""; hash_algorithm: str = "SHA256"
    size_bytes: int = 0; message: str = ""; errors: List[str] = field(default_factory=list)

@dataclass
class DeployPreview:
    success: bool; script_id: str; source_datagram: str; target_path: str
    file_count: int = 0; files_to_restore: List[str] = field(default_factory=list)
    post_unpack_actions: List[Dict] = field(default_factory=list)
    compatibility_ok: bool = True; port_conflicts: List[int] = field(default_factory=list)
    would_overwrite: List[str] = field(default_factory=list); message: str = ""

@dataclass
class DeployResult:
    success: bool; script_id: str; datagram_path: str; target_path: str
    files_restored: int = 0; files_skipped: int = 0
    actions_executed: List[str] = field(default_factory=list)
    backup_path: str = ""; message: str = ""; errors: List[str] = field(default_factory=list)

def _compute_file_hash(file_path: Path, algorithm: str = "sha256") -> str:
    h = hashlib.new(algorithm)
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""): h.update(chunk)
    return h.hexdigest()

def _should_include(rp: str, inc: List[str], exc: List[str]) -> bool:
    for p in exc:
        if fnmatch.fnmatch(rp, p): return False
        if any(fnmatch.fnmatch(part, p) for part in Path(rp).parts): return False
    if not inc: return True
    for p in inc:
        if fnmatch.fnmatch(rp, p): return True
        if fnmatch.fnmatch(Path(rp).name, p): return True
    return False

def _find_project_root() -> Optional[Path]:
    f = Path(__file__).resolve()
    for level in range(15):
        if level >= len(f.parents): break
        p = f.parents[level]
        if (p / "engine" / "manager.py").exists() and (p / "SCRIPTS").exists(): return p
    return None

def _get_scripts_root() -> Optional[Path]:
    r = _find_project_root()
    return r / "SCRIPTS" if r and (r / "SCRIPTS").exists() else None

def _get_data_dir() -> Optional[Path]:
    r = _find_project_root()
    return r / "DATA" if r and (r / "DATA").exists() else None

DATAGRAM_DIRS = ["Meta","Script","Databases","Databases/Default/Data","Configs","Functions","LargeAssets","PreLoad","PreLoad/Gui","PreLoad/Intil"]

def _create_datagram_structure(root: Path) -> None:
    for d in DATAGRAM_DIRS: (root / d).mkdir(parents=True, exist_ok=True)

class ScriptSnapshotManager:
    def __init__(self, scripts_root=None, data_dir=None):
        self.scripts_root = scripts_root or _get_scripts_root()
        self.data_dir = data_dir or _get_data_dir()
        if not self.scripts_root or not self.scripts_root.exists():
            raise RuntimeError(f"SCRIPTS root not found: {self.scripts_root}. Pass scripts_root= explicitly.")

    def discover_scripts(self) -> List[Dict[str, Any]]:
        scripts = []
        if not self.scripts_root: return scripts
        for d in self.scripts_root.rglob("*"):
            if not d.is_dir(): continue
            mp = d / "main.py"
            mi = d / "meta.info"; mj = d / "meta.json"
            if not mp.exists() or not (mi.exists() or mj.exists()): continue
            try: sid = str(d.relative_to(self.scripts_root).as_posix())
            except ValueError: sid = d.name
            if mi.exists(): meta = self._parse_meta_info(mi)
            elif mj.exists(): meta = self._parse_meta_json(mj)
            else: meta = {}
            cc = load_compile_config(sid)
            dc = load_decompile_config(sid)
            scripts.append({
                "script_id": sid, "path": str(d),
                "name": meta.get("name", d.name), "version": meta.get("version", "0.0.0"),
                "description": meta.get("description", ""), "category": meta.get("category", "uncategorized"),
                "enabled": meta.get("enabled", True), "server_type": meta.get("server_type", "normal"),
                "ports": meta.get("ports", []),
                "has_compile_config": cc is not None, "has_decompile_config": dc is not None,
            })
        return sorted(scripts, key=lambda s: s["script_id"])

    def _parse_meta_info(self, path: Path) -> Dict[str, Any]:
        """Parse meta.info — supports INI and JSON formats, handles non-standard files."""
        raw = path.read_text(encoding="utf-8").strip()
        # Try JSON first
        if raw.startswith("{"):
            try: return json.loads(raw)
            except json.JSONDecodeError: pass
        # Standard INI parsing with ConfigParser
        import configparser
        config = configparser.ConfigParser()
        try:
            config.read([str(path)])
        except configparser.ParsingError:
            # File has non-standard lines - fall through to manual parse
            pass
        meta = {}
        if "script" in config:
            s = config["script"]
            for k in ("name","version","description","category","server_type","entry_point","restart_policy","requirements_file"):
                if k in s: meta[k] = s[k]
            if "enabled" in s: meta["enabled"] = s.getboolean("enabled", True)
            if "ports" in s: meta["ports"] = self._parse_ports(s["ports"])
            return meta
        # Fallback: manual line parse for badly formatted files
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith("#"): continue
            if "=" in line:
                k, v = line.split("=", 1)
                k = k.strip(); v = v.strip()
                if k == "name": meta["name"] = v
                elif k == "version": meta["version"] = v
                elif k == "description": meta["description"] = v
                elif k == "enabled": meta["enabled"] = v.lower() == "true"
                elif k == "category": meta["category"] = v
        return meta

    def _parse_meta_json(self, path: Path) -> Dict[str, Any]:
        try: return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError): return {}

    @staticmethod
    def _parse_ports(raw: str) -> List[int]:
        ports = []
        for p in raw.split(","):
            p = p.strip()
            if not p: continue
            if "-" in p:
                try: s, e = p.split("-", 1); ports.extend(range(int(s), int(e)+1))
                except ValueError: pass
            else:
                try: ports.append(int(p))
                except ValueError: pass
        return ports

    def get_or_create_compile_config(self, sid: str) -> CompileConfig:
        c = load_compile_config(sid); return c or get_default_compile_config(sid)
    def get_or_create_decompile_config(self, sid: str) -> DecompileConfig:
        c = load_decompile_config(sid); return c or get_default_decompile_config(sid)
    def set_compile_config(self, sid: str, d: Dict) -> bool: return save_compile_config(sid, CompileConfig(d))
    def set_decompile_config(self, sid: str, d: Dict) -> bool: return save_decompile_config(sid, DecompileConfig(d))

    def create_snapshot(self, script_id, output_path, name="", author="Admin", compile_config=None, include_databases=None, include_configs=None) -> SnapshotResult:
        sd = self.scripts_root / script_id
        if not sd.exists():
            return SnapshotResult(False, script_id, output_path, message=f"Not found: {sd}", errors=[str(sd)])
        cc = compile_config or self.get_or_create_compile_config(script_id)
        if include_databases is not None: cc.data["include_databases"] = include_databases
        if include_configs is not None: cc.data["include_configs"] = include_configs
        cc.data["script_id"] = script_id
        out = Path(output_path).resolve()
        if out.exists():
            return SnapshotResult(False, script_id, output_path, message=f"Exists: {out}", errors=["Path exists"])
        _create_datagram_structure(out)
        errors, copied, entries, total = [], 0, [], 0
        sfd = out / "Script"
        for item in sd.rglob("*"):
            if not item.is_file(): continue
            try: rel = str(item.relative_to(sd).as_posix())
            except ValueError: continue
            if not _should_include(rel, cc.include_patterns, cc.exclude_patterns): continue
            tgt = sfd / rel; tgt.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(str(item), str(tgt)); fh = _compute_file_hash(tgt)
                entries.append({"path":"Script/"+rel,"original_path":rel,"size":item.stat().st_size,"hash":fh,"hash_algorithm":"sha256"})
                copied += 1; total += item.stat().st_size
            except (OSError, IOError) as e: errors.append("Copy "+rel+": "+str(e))
        # Write compile/decompile instructions
        for fname, cfg_obj in [("compile_instructions.json", cc), ("decompile_instructions.json", self.get_or_create_decompile_config(script_id))]:
            fp = sfd / fname; fp.write_text(json.dumps(cfg_obj.to_dict(), indent=2), encoding="utf-8")
            entries.append({"path":"Script/"+fname,"original_path":fname,"size":fp.stat().st_size,"hash":_compute_file_hash(fp),"hash_algorithm":"sha256"}); copied += 1
        # Databases
        if cc.include_databases:
            for db in sd.rglob("*.db"):
                try:
                    tgt = out / "Databases" / "Default" / "Data" / db.name; tgt.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(str(db), str(tgt))
                    entries.append({"path":"Databases/Default/Data/"+db.name,"original_path":str(db.relative_to(sd)),"size":db.stat().st_size,"hash":_compute_file_hash(tgt),"hash_algorithm":"sha256"})
                    copied += 1; total += db.stat().st_size
                except Exception as e: errors.append("DB "+db.name+": "+str(e))
        # Configs
        if cc.include_configs and self.data_dir:
            for src in cc.config_sources:
                cf = self.data_dir / (src+".json")
                if cf.exists():
                    try:
                        tgt = out / "Configs" / cf.name; tgt.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(str(cf), str(tgt))
                        entries.append({"path":"Configs/"+cf.name,"original_path":"DATA/"+cf.name,"size":cf.stat().st_size,"hash":_compute_file_hash(tgt),"hash_algorithm":"sha256"})
                        copied += 1; total += cf.stat().st_size
                    except Exception as e: errors.append("Config "+cf.name+": "+str(e))
        # Manifest
        ct = datetime.now(timezone.utc).isoformat()
        md = {"script_id":script_id,"datagram_version":cc.packaging_config.get("datagram_version","1.0.0"),"created":ct,"name":name or ("Snapshot of "+script_id),"author":author,"file_count":copied,"total_size_bytes":total,"entries":entries}
        (out / "Manifest.json").write_text(json.dumps(md, indent=2), encoding="utf-8")
        # Meta/Base.ini + ScriptMeta.ini
        ch = hashlib.sha256(b"".join((out / e["path"]).read_bytes() for e in entries if (out/e["path"]).exists())).hexdigest()
        (out/"Meta"/"Base.ini").write_text("[Datagram Version]=1.0.0\n[Datagram NAME ID]="+(name or ("Snapshot of "+script_id))+"\n[Datagram Author]="+author+"\n[Datagram Hashing Algo]=5\n[Datagram Hash UQID]={"+ch+"}\n[Datagram Script ID]="+script_id+"\n", encoding="utf-8")
        (out/"Meta"/"ScriptMeta.ini").write_text("[Script ID]={"+script_id+"}\n[Script Name]={"+(name or script_id)+"}\n[File Count]="+str(copied)+"\n[Total Size]="+str(total)+"\n[Created]={"+ct+"}\n[Author]={"+author+"}\n", encoding="utf-8")
        save_compile_config(script_id, cc)
        return SnapshotResult(True, script_id, str(out), copied, ch, "SHA256", total, "Snapshot at "+str(out)+" ("+str(copied)+" files)", errors)

    def load_snapshot_meta(self, dg_path: str) -> Optional[Dict]:
        root = Path(dg_path).resolve()
        if not root.exists(): return None
        r = {"datagram_path":str(root),"exists":True,"is_valid":False,"script_id":"","name":"","author":"","version":"","file_count":0,"hash":"","files":[]}
        bi = root / "Meta" / "Base.ini"
        if bi.exists():
            for line in bi.read_text().splitlines():
                line = line.strip()
                if line.startswith("[") and "=" in line:
                    k, v = line.split("=",1); k = k.strip("[]").strip(); v = v.strip("{}").strip()
                    if k=="Datagram NAME ID": r["name"]=v
                    elif k=="Datagram Author": r["author"]=v
                    elif k=="Datagram Script ID": r["script_id"]=v
                    elif k=="Datagram Version": r["version"]=v
                    elif k=="Datagram Hash UQID": r["hash"]=v.strip("{}")
        mf = root / "Manifest.json"
        if mf.exists():
            try:
                d = json.loads(mf.read_text(encoding="utf-8"))
                r["file_count"]=d.get("file_count",0); r["files"]=d.get("entries",[])
                if not r["name"]: r["name"]=d.get("name","")
                if not r["author"]: r["author"]=d.get("author","")
                r["is_valid"]=True
            except (json.JSONDecodeError, OSError): pass
        sm = root / "Meta" / "ScriptMeta.ini"
        if sm.exists():
            for line in sm.read_text().splitlines():
                line = line.strip()
                if line.startswith("[") and "=" in line:
                    k, v = line.split("=",1); k = k.strip("[]").strip(); v = v.strip("{}").strip()
                    if k=="Script ID" and not r["script_id"]: r["script_id"]=v
        for fn, rk in [("compile_instructions.json","compile_instructions"),("decompile_instructions.json","decompile_instructions")]:
            fp = root / "Script" / fn
            if fp.exists():
                try: r[rk] = json.loads(fp.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError): pass
        return r

    def preview_deploy(self, dg_path, target_sid=None) -> DeployPreview:
        meta = self.load_snapshot_meta(dg_path)
        if not meta or not meta.get("is_valid"):
            return DeployPreview(False, target_sid or "", dg_path, "", message="Invalid datagram: "+dg_path)
        sid = target_sid or meta["script_id"]
        overwrite = [e["original_path"] for e in meta.get("files",[]) if (self.scripts_root / sid / e["original_path"]).exists()] if meta.get("files") else []
        dc = meta.get("decompile_instructions", {})
        return DeployPreview(True, sid, dg_path, str(self.scripts_root / sid), meta.get("file_count",0),
            [e.get("original_path","") for e in meta.get("files",[])] or [],
            dc.get("post_unpack_actions",[]), True, dc.get("compatibility",{}).get("required_ports",[]),
            overwrite, "Would restore "+str(meta.get("file_count",0))+" files")

    def deploy_snapshot(self, dg_path, target_sid=None, auto_start=False, verbose=True) -> DeployResult:
        meta = self.load_snapshot_meta(dg_path)
        if not meta or not meta.get("is_valid"):
            return DeployResult(False, target_sid or "unknown", dg_path, "", message="Invalid datagram: "+dg_path)
        sid = target_sid or meta.get("script_id","unknown"); td = self.scripts_root / sid
        actions, errors, restored, skipped, bk = [], [], 0, 0, ""
        ss = Path(dg_path).resolve() / "Script"
        if not ss.exists():
            return DeployResult(False, sid, dg_path, str(td), message="No Script/ dir", errors=["Missing Script/"])
        # Backup
        if td.exists():
            bd = self.scripts_root / (".backup_"+sid.replace("/","_")+"_"+str(int(time.time())))
            try: shutil.copytree(str(td), str(bd)); bk = str(bd); actions.append("Backup at "+str(bd))
            except Exception as e: errors.append("Backup: "+str(e))
        td.mkdir(parents=True, exist_ok=True)
        dc = meta.get("decompile_instructions", {})
        oc = dc.get("unpackaging",{}).get("on_conflict","skip")
        ow = dc.get("unpackaging",{}).get("overwrite_existing",False)
        for item in sorted(ss.rglob("*")):
            if not item.is_file(): continue
            try: rel = str(item.relative_to(ss).as_posix())
            except ValueError: continue
            tf = td / rel
            if tf.exists() and oc == "skip" and not ow: skipped += 1; continue
            try: tf.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(str(item), str(tf)); restored += 1
            except Exception as e: errors.append("Restore "+rel+": "+str(e))
        actions.append("Restored "+str(restored)+" files"+((" (skipped "+str(skipped)+")") if skipped else ""))
        # Databases
        ds = Path(dg_path).resolve() / "Databases" / "Default" / "Data"
        if ds.exists():
            for db in ds.glob("*.db"):
                try: shutil.copy2(str(db), td / db.name)
                except Exception as e: errors.append("DB "+db.name+": "+str(e))
        # Configs
        cs = Path(dg_path).resolve() / "Configs"
        if cs.exists() and self.data_dir:
            for cf in cs.glob("*.json"):
                try: shutil.copy2(str(cf), self.data_dir / cf.name)
                except Exception as e: errors.append("Config "+cf.name+": "+str(e))
        # Post-unpack actions
        for a in dc.get("post_unpack_actions",[]):
            if not a.get("enabled", True): continue
            t = a.get("type","")
            if t == "register_script": actions.append("Script registered")
            elif t == "install_dependencies":
                rf = a.get("requirements_file","requirements.txt")
                actions.append("Deps from "+rf if (td/rf).exists() else "No "+rf+" found")
            elif t in ("restore_configs","start_script"): actions.append(t)
        return DeployResult(True, sid, dg_path, str(td), restored, skipped, actions, bk,
            "Deployed "+sid+": "+str(restored)+" files, "+str(len(actions))+" actions", errors)

    def list_snapshots(self, base_dir=None) -> List[Dict]:
        bd = Path(base_dir or (str(self.data_dir / "snapshots") if self.data_dir else str(Path.home()/"yuni_datagrams")))
        if not bd.exists(): return []
        snaps = []
        for item in sorted(bd.iterdir()):
            if not item.is_dir(): continue
            m = self.load_snapshot_meta(str(item))
            if m and m.get("is_valid"):
                snaps.append({"datagram_path":str(item),"name":m.get("name",item.name),"script_id":m.get("script_id","unknown"),"file_count":m.get("file_count",0),"hash":m.get("hash",""),"version":m.get("version",""),"author":m.get("author","")})
        return snaps

def _format_size(s: int) -> str:
    if s < 1024: return str(s)+" bytes"
    elif s < 1048576: return f"{s/1024:.1f} KB"
    else: return f"{s/1048576:.1f} MB"
