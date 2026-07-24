"""Generate the committed Slice 6 .blend fixture with real Blender."""

from __future__ import annotations

import sys
from pathlib import Path

import bpy


def main() -> None:
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    if len(arguments) != 1:
        raise SystemExit(
            "usage: blender --background --factory-startup --python "
            "scripts/generate_scene_fixture.py -- OUTPUT.blend"
        )
    output = Path(arguments[0]).expanduser().resolve()
    if output.suffix.lower() != ".blend":
        raise SystemExit("output path must end in .blend")
    output.parent.mkdir(parents=True, exist_ok=True)

    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for datablocks in (bpy.data.meshes, bpy.data.curves, bpy.data.materials):
        for datablock in tuple(datablocks):
            datablocks.remove(datablock)

    bpy.context.scene.name = "Slice6FixtureScene"
    bpy.context.scene["blendermcp_auto_start_server"] = False
    bpy.ops.mesh.primitive_cube_add(location=(1.0, 2.0, 3.0))
    bpy.context.object.name = "Slice6FixtureCube"
    anchor = bpy.data.objects.new("Slice6FixtureAnchor", None)
    bpy.context.scene.collection.objects.link(anchor)
    bpy.ops.wm.save_as_mainfile(
        filepath=str(output),
        check_existing=False,
        compress=True,
    )
    print(f"Generated {output}")


main()
