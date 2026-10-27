from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class InstallProbe:
    root: Path
    resources_assets: Path
    exists: bool
    size_bytes: int
    components: tuple[str, ...]
    message: str

def probe_install(root: str | Path) -> InstallProbe:
    root=Path(root); assets=root/'RainWorld_Data'/'resources.assets'
    names=tuple(p.name for p in (root/'RainWorld_Data').iterdir()) if (root/'RainWorld_Data').is_dir() else ()
    exists=assets.is_file(); size=assets.stat().st_size if exists else 0
    return InstallProbe(root,assets,exists,size,names,'resources.assets found' if exists else 'Rain World installation not found')
