"""Generate a Repo overlay or verify the pinned YARP projects after sync.

The NX729J device tree lives inside this repository (``device/nubia/nx729j``)
and is symlinked into the synced source tree by ``build-yarp.sh``, so it is not
a repo project: the lock marks it ``"revision": "in-tree"`` and the overlay and
verification steps skip it, checking the symlink instead.
"""

import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET


def is_in_tree(project):
    return project.get("revision") == "in-tree"


def main():
    command, lock_path, source_path = sys.argv[1:]
    lock = json.loads(Path(lock_path).read_text(encoding="utf-8"))
    source = Path(source_path)
    if command == "overlay":
        manifest = ET.Element("manifest")
        for project in lock["projects"]:
            ET.SubElement(manifest, "extend-project", {
                **project, "upstream": "refs/heads/twrp-16.0"
            })
        if not is_in_tree(lock["device"]):
            ET.SubElement(manifest, "remote", {
                "name": "yarp-device", "fetch": "https://github.com/TWRP-Test"
            })
            ET.SubElement(manifest, "project", {
                **lock["device"], "remote": "yarp-device",
                "upstream": "refs/heads/twrp-16.0"
            })
        target = source / ".repo/local_manifests/yarp-pins.xml"
        target.parent.mkdir(parents=True, exist_ok=True)
        ET.ElementTree(manifest).write(target, encoding="utf-8", xml_declaration=True)
        print(f"Wrote {target}")
    elif command == "verify":
        projects = [{"path": ".repo/manifests", **lock["manifest"]}]
        projects += lock["projects"]
        for project in projects:
            actual = subprocess.check_output(
                ["git", "-C", str(source / project["path"]), "rev-parse", "HEAD"],
                text=True,
            ).strip()
            if actual != project["revision"]:
                raise RuntimeError(f"Source mismatch: {project['path']}: {actual}")
            print(f"Verified {project['path']}: {actual}")
        device = source / lock["device"]["path"]
        if not (device / "BoardConfig.mk").is_file():
            raise RuntimeError(f"Device tree is not linked into the source tree: {device}")
        print(f"Verified in-tree device tree: {device} -> {device.resolve()}")
    else:
        raise ValueError(f"Unknown command: {command}")


if __name__ == "__main__":
    main()
