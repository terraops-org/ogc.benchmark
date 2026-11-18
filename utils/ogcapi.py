"""What the OGC API locustfiles share: CRS URIs, JSON metadata fetched once per process, collections, links, extents."""

import requests

from utils.ogc import cached_capabilities, with_params

CRS84 = "http://www.opengis.net/def/crs/OGC/1.3/CRS84"
MVT = ("application/vnd.mapbox-vector-tile",)


def crs_uri(code):
    """'EPSG:3763' -> the OGC URI; 'CRS:84' / 'OGC:CRS84' -> CRS84; a URI passes through."""
    if code.startswith("http"):
        return code
    if code.upper() in ("CRS:84", "OGC:CRS84", "CRS84"):
        return CRS84
    authority, _, number = code.partition(":")
    return f"http://www.opengis.net/def/crs/{authority.upper()}/0/{number.lstrip(':')}"


def cached_json(url, headers):
    """The JSON document at url (?f=json added), fetched once per process per url and credentials."""

    def fetch():
        resp = requests.get(with_params(url, {"f": "json"}), headers=headers, timeout=120)
        resp.raise_for_status()
        return resp.json()

    return cached_capabilities(("json", url, headers.get("Authorization")), fetch)[0]


def collections(host, headers):
    """The service's collections (the /collections document)."""
    return cached_json(f"{host.rstrip('/')}/collections", headers)["collections"]


def link(obj, rel, type_prefix=""):
    """href of the first link whose rel is `rel` (or a URI ending in /rel) and whose type starts with type_prefix."""
    for item in obj.get("links", []):
        r = item.get("rel", "")
        if (r == rel or r.endswith("/" + rel)) and item.get("type", "").startswith(type_prefix):
            return item.get("href")
    return None


def pick_collection(cols, wanted, rel):
    """The collection `wanted` (it must have a `rel` link), or the first one with such a link."""
    with_rel = [c for c in cols if link(c, rel)]
    for c in with_rel:
        if wanted in (None, "", c.get("id")):
            return c
    raise ValueError(
        f"Collection '{wanted}' not found or without {rel}. "
        f"Collections with {rel}: {[c.get('id') for c in with_rel]}"
    )


def lonlat_extent(col):
    """The collection's spatial extent, read as CRS84 lon/lat whatever CRS it declares (servers get that wrong)."""
    try:
        box = tuple(float(v) for v in col["extent"]["spatial"]["bbox"][0][:4])
    except KeyError, IndexError, TypeError, ValueError:
        raise ValueError(f"Collection '{col.get('id')}' advertises no spatial extent (extent.spatial.bbox)") from None
    minx, miny, maxx, maxy = box
    if not (-180 <= minx < maxx <= 180 and -90 <= miny < maxy <= 90):
        raise ValueError(f"Collection '{col.get('id')}': extent {box} is not lon/lat; use --extent")
    return box
