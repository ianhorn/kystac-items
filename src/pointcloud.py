import json
import subprocess


def get_stac_info(laz_file: str) -> dict:
    """Run `pdal info --stac` on a point cloud file and return the parsed STAC item."""
    result = subprocess.run(
        ["pdal", "info", laz_file, "--stac"],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)["stac"]
