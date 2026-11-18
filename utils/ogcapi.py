"""What the OGC API locustfiles share: CRS URIs, metadata fetched once per process, collections, links, extents."""

import gevent
import requests
from pyproj import CRS
from pyproj.exceptions import CRSError

from utils.ogc import cached_capabilities, intersect, owslib_auth, parse_extent, with_params

CRS84 = "http://www.opengis.net/def/crs/OGC/1.3/CRS84"
MVT = ("application/vnd.mapbox-vector-tile",)


def crs_uri(code):
    """'EPSG:3763' or 'urn:ogc:def:crs:EPSG::3763' -> the OGC URI; CRS84 in any spelling -> CRS84; a URI passes."""
    if code.startswith("http"):
        return code
    if code.upper() in ("CRS:84", "OGC:CRS84", "CRS84"):
        return CRS84
    try:
        crs = CRS.from_user_input(code)
    except CRSError:
        crs = None
    if crs is not None and crs == CRS.from_user_input("OGC:CRS84"):
        return CRS84
    if crs is not None and crs.to_epsg():
        return f"http://www.opengis.net/def/crs/EPSG/0/{crs.to_epsg()}"
    authority, _, number = code.partition(":")
    return f"http://www.opengis.net/def/crs/{authority.upper()}/0/{number.lstrip(':')}"


def offered_crss(col, doc):
    """The collection's CRS list, Part 2's "#/crs" replaced by the global list of /collections; CRS84 if none."""
    offered = []
    for crs in col.get("crs") or [CRS84]:
        offered.extend(doc.get("crs", []) if crs == "#/crs" else [crs])
    return offered


def cached_json(url, headers, verify=False):
    """The JSON document at url (?f=json added), fetched once per process per url and credentials."""

    def fetch():
        resp = requests.get(with_params(url, {"f": "json"}), headers=headers, timeout=120, verify=verify)
        resp.raise_for_status()
        return resp.json()

    return cached_capabilities(("json", url, headers.get("Authorization")), fetch)[0]


def collections(host, headers, verify=False):
    """The service's collections (the /collections document)."""
    return cached_json(f"{host.rstrip('/')}/collections", headers, verify)["collections"]


def ogcapi_collections(api_class, host, opts, timeout=120):
    """
    The /collections document of an OGC API read through OWSLib 0.36 (`owslib.ogcapi.features.Features`, `maps.Maps`, ...),
    once per process: its constructor reads the landing page, then `collections()` reads /collections.

    OWSLib passes no timeout to these requests, so the whole fetch runs under a gevent timeout. Only the
    User-Agent goes in `headers` (OWSLib 0.36 merges them into a module-wide dict); credentials go in `auth`.
    """

    def fetch():
        with gevent.Timeout(timeout, TimeoutError(f"no answer from {host} in {timeout} s")):
            api = api_class(host, headers={"User-Agent": opts.user_agent}, auth=owslib_auth(opts), timeout=timeout)
            return api.collections()

    key = ("ogcapi", api_class, host, getattr(opts, "auth_user", ""), opts.verify_tls)
    return cached_capabilities(key, fetch)[0]


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


def lonlat_area(col, extent_text):
    """
    The lon/lat area to test: the collection's extent narrowed by --extent (ValueError if they don't overlap).

    When the collection's extent is missing or not lon/lat, --extent alone bounds the test.
    """
    extent = parse_extent(extent_text)
    try:
        own = lonlat_extent(col)
    except ValueError:
        if not extent:
            raise
        return extent
    box = intersect(own, extent) if extent else own
    if box is None:
        raise ValueError(f"--extent {extent_text} does not overlap the extent {own} of '{col.get('id')}'")
    return box
