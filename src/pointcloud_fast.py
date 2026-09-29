import datetime
import json
import os
import os.path
import struct
import sys
import time
import urllib.parse
import urllib.request

# rasterio/pyproj need PROJ_DATA to find proj.db. Conda normally sets this
# via activation scripts, which don't run if this interpreter was launched
# directly (e.g. VS Code's "Run Python File" without an activated terminal).
os.environ.setdefault(
    "PROJ_DATA", os.path.join(sys.prefix, "Library", "share", "proj")
)

from pdal import Pipeline
from pyproj import CRS
from pystac import Asset, Item
from pystac.extensions.pointcloud import PointcloudExtension, Schema
from pystac.extensions.projection import ProjectionExtension
from shapely.geometry import box, mapping
from stactools.core.projection import reproject_shape

# PDAL's default dimension type/size assignments for the standard LAS/LAZ
# dimension set. These come from PDAL's dimension registry, not the file
# itself, so they're constant for any file that only uses standard dims
# (i.e. no custom "extra bytes" dimensions).
DEFAULT_DIMENSION_SCHEMA = {
    "X": {"size": 8, "type": "floating"},
    "Y": {"size": 8, "type": "floating"},
    "Z": {"size": 8, "type": "floating"},
    "Intensity": {"size": 2, "type": "unsigned"},
    "ReturnNumber": {"size": 1, "type": "unsigned"},
    "NumberOfReturns": {"size": 1, "type": "unsigned"},
    "ScanDirectionFlag": {"size": 1, "type": "unsigned"},
    "EdgeOfFlightLine": {"size": 1, "type": "unsigned"},
    "Classification": {"size": 1, "type": "unsigned"},
    "Synthetic": {"size": 1, "type": "unsigned"},
    "KeyPoint": {"size": 1, "type": "unsigned"},
    "Withheld": {"size": 1, "type": "unsigned"},
    "Overlap": {"size": 1, "type": "unsigned"},
    "ScanAngleRank": {"size": 4, "type": "floating"},
    "UserData": {"size": 1, "type": "unsigned"},
    "PointSourceId": {"size": 2, "type": "unsigned"},
    "GpsTime": {"size": 8, "type": "floating"},
    "ScanChannel": {"size": 1, "type": "unsigned"},
}


def _retry(fn, attempts=3, delay=1.0, exceptions=(RuntimeError, OSError)):
    """Retries fn() on failure.

    PDAL's remote (arbiter/curl) reader intermittently fails on the first
    HTTP request of a process with a mangled URL / "File does not exist",
    then succeeds immediately on retry. Cheap to paper over here rather than
    debug arbiter's connection warm-up.
    """
    last_exc = None
    for attempt in range(attempts):
        try:
            return fn()
        except exceptions as exc:
            last_exc = exc
            if attempt < attempts - 1:
                time.sleep(delay)
    raise last_exc


def _thumbnail_href(href: str, item_id: str) -> str:
    """Derives a thumbnail URL from a data URL, e.g.:

    https://kyfromabove.s3.us-west-2.amazonaws.com/elevation/PointCloud/Phase3/foo.copc.laz
    -> https://kyfromabove-stac.s3.us-west-2.amazonaws.com/collections/laz-phase3/thumbnails/foo.copc.png
    """
    parsed = urllib.parse.urlparse(href)
    bucket, _, domain_rest = parsed.netloc.partition(".")
    thumb_netloc = f"{bucket}-stac.{domain_rest}"
    phase_folder = parsed.path.rstrip("/").split("/")[-2]
    thumb_path = f"/collections/laz-{phase_folder.lower()}/thumbnails/{item_id}.png"
    return urllib.parse.urlunparse((parsed.scheme, thumb_netloc, thumb_path, "", "", ""))


def create_item_fast(href, a_srs="EPSG:4326"):
    """Like stactools.pointcloud.stac.create_item, but for COPC files only.

    Reads only the file's header/VLRs via PDAL's quickinfo plus a small HTTP
    range request, instead of streaming every point over the network just to
    get header-level facts. Does not support compute_statistics or non-COPC
    readers.
    """
    reader = {"type": "readers.copc", "filename": href}
    pipeline = Pipeline(json.dumps([reader]))
    info = _retry(lambda: pipeline.quickinfo["readers.copc"])

    req = urllib.request.Request(href, headers={"Range": "bytes=0-127"})
    header = _retry(lambda: urllib.request.urlopen(req).read())
    creation_doy, creation_year = struct.unpack("<HH", header[90:94])

    spatialreference = CRS.from_wkt(info["srs"]["compoundwkt"])
    b = info["bounds"]
    original_bbox = box(b["minx"], b["miny"], b["maxx"], b["maxy"])
    geometry = reproject_shape(spatialreference, a_srs, original_bbox, precision=6)
    dt = datetime.datetime(creation_year, 1, 1) + datetime.timedelta(creation_doy - 1)

    id = os.path.splitext(os.path.basename(href))[0]
    encoding = os.path.splitext(href)[1][1:]
    item = Item(
        id=id,
        geometry=mapping(geometry),
        bbox=geometry.bounds,
        datetime=dt,
        properties={},
    )
    item.add_asset(
        "data",
        Asset(
            href=href,
            media_type="application/vnd.laszip+copc",
            roles=["data"],
            title="copc data",
        ),
    )
    item.add_asset(
        "thumbnail",
        Asset(
            href=_thumbnail_href(href, id),
            media_type="image/png",
            roles=["thumbnail"],
            title="thumbnail",
        ),
    )

    dimension_names = [d.strip() for d in info["dimensions"].split(",")]
    schemas = []
    for name in dimension_names:
        dim = DEFAULT_DIMENSION_SCHEMA.get(name)
        if dim is None:
            raise ValueError(
                f"Unknown dimension {name!r}; this file may have custom "
                "extra-bytes dimensions not covered by create_item_fast."
            )
        schemas.append(Schema({"name": name, "size": dim["size"], "type": dim["type"]}))

    pc = PointcloudExtension.ext(item, add_if_missing=True)
    pc.count = info["num_points"]
    pc.type = "lidar"
    pc.encoding = encoding
    pc.schemas = schemas

    proj = ProjectionExtension.ext(item, add_if_missing=True)
    epsg = spatialreference.to_epsg()
    if epsg:
        proj.epsg = epsg
    proj.wkt2 = spatialreference.to_wkt()
    proj.bbox = list(original_bbox.bounds)

    return item
