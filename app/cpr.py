"""Global and local CPR (Compact Position Reporting) for airborne ADS-B.

Implements the decoders defined by RTCA DO-260 (see also ICAO Annex 10
Vol. IV):

* :func:`global_decode` combines one even and one odd frame to recover an
  unambiguous global position.
* :func:`local_decode` resolves a single frame against a trusted reference
  point, choosing the CPR grid cell nearest the reference.  The caller is
  responsible for sanity-checking the result against a maximum range so a
  grid ambiguity can never place the target in a distant cell.

Longitude is normalised to [-180, 180) so tracks crossing the antimeridian
stay on the correct side.
"""
from __future__ import annotations

import math

from .errors import DecodeError

NZ = 15               # number of CPR latitude zones
CPR_SCALE = 1 << 17   # 2^17 quantisation of the CPR fields

#: Mean Earth radius in nautical miles (6371 km / 1.852 km per NM), used by
#: the haversine range check around locally decoded positions.
EARTH_RADIUS_NM = 3440.065

_DLAT_EVEN = 360.0 / (4 * NZ)       # 6.0 degrees
_DLAT_ODD = 360.0 / (4 * NZ - 1)    # 360/59 degrees


def cpr_nl(lat: float) -> int:
    """Number of longitude zones (NL) in effect at ``lat`` degrees."""
    lat = abs(lat)
    if lat < 10.47047130: return 59
    if lat < 14.82817437: return 58
    if lat < 18.18626357: return 57
    if lat < 21.02939493: return 56
    if lat < 23.54504487: return 55
    if lat < 25.82924707: return 54
    if lat < 27.93898710: return 53
    if lat < 29.91135686: return 52
    if lat < 31.77209708: return 51
    if lat < 33.53993436: return 50
    if lat < 35.22899598: return 49
    if lat < 36.85025108: return 48
    if lat < 38.41241892: return 47
    if lat < 39.92256684: return 46
    if lat < 41.38651832: return 45
    if lat < 42.80914012: return 44
    if lat < 44.19454951: return 43
    if lat < 45.54626723: return 42
    if lat < 46.86733252: return 41
    if lat < 48.16024528: return 40
    if lat < 49.42776439: return 39
    if lat < 50.67150166: return 38
    if lat < 51.89342469: return 37
    if lat < 53.09516153: return 36
    if lat < 54.27817472: return 35
    if lat < 55.44378444: return 34
    if lat < 56.59318756: return 33
    if lat < 57.72747354: return 32
    if lat < 58.84763776: return 31
    if lat < 59.95459277: return 30
    if lat < 61.04917774: return 29
    if lat < 62.13216659: return 28
    if lat < 63.20427479: return 27
    if lat < 64.26616523: return 26
    if lat < 65.31845310: return 25
    if lat < 66.36171008: return 24
    if lat < 67.39646774: return 23
    if lat < 68.42322022: return 22
    if lat < 69.44242631: return 21
    if lat < 70.45451075: return 20
    if lat < 71.45986473: return 19
    if lat < 72.45884545: return 18
    if lat < 73.45177442: return 17
    if lat < 74.43893416: return 16
    if lat < 75.42056257: return 15
    if lat < 76.39684391: return 14
    if lat < 77.36789461: return 13
    if lat < 78.33374083: return 12
    if lat < 79.29428225: return 11
    if lat < 80.24923213: return 10
    if lat < 81.19801349: return 9
    if lat < 82.13956981: return 8
    if lat < 83.07199445: return 7
    if lat < 83.99173563: return 6
    if lat < 84.89166191: return 5
    if lat < 85.75541621: return 4
    if lat < 86.53536998: return 3
    if lat < 87.00000000: return 2
    return 1


def global_decode(even, odd) -> tuple[float, float]:
    """Decode a matched even/odd CPR pair.

    ``even`` and ``odd`` are :class:`~app.adsb.PositionFrame` objects with
    opposite CPR flags.  Returns ``(lat, lon)`` of the *newer* frame with
    longitude normalised to [-180, 180).

    Raises :class:`DecodeError` with code ``LATITUDE_ZONE_MISMATCH`` when
    the two frames fall into different NL latitude zones.
    """
    yz_even = even.lat_cpr / CPR_SCALE
    yz_odd = odd.lat_cpr / CPR_SCALE

    # Latitude zone index of the pair.
    j = math.floor(59 * yz_even - 60 * yz_odd + 0.5)

    rlat_even = _DLAT_EVEN * (j % 60 + yz_even)
    rlat_odd = _DLAT_ODD * (j % 59 + yz_odd)
    # Southern hemisphere: latitudes above 270 map to negative values.
    if rlat_even >= 270.0:
        rlat_even -= 360.0
    if rlat_odd >= 270.0:
        rlat_odd -= 360.0

    nl = cpr_nl(rlat_even)
    if nl != cpr_nl(rlat_odd):
        raise DecodeError(
            "LATITUDE_ZONE_MISMATCH",
            f"even frame latitude {rlat_even:.5f} and odd frame latitude "
            f"{rlat_odd:.5f} fall in different NL zones",
        )

    xz_even = even.lon_cpr / CPR_SCALE
    xz_odd = odd.lon_cpr / CPR_SCALE
    # Longitude zone index; the mod below absorbs antimeridian wrap-around.
    m = math.floor(xz_even * (nl - 1) - xz_odd * nl + 0.5)

    if even.time_ms >= odd.time_ms:
        ni = max(nl, 1)
        lon = (360.0 / ni) * (m % ni + xz_even)
        lat = rlat_even
    else:
        ni = max(nl - 1, 1)
        lon = (360.0 / ni) * (m % ni + xz_odd)
        lat = rlat_odd

    # Normalise to [-180, 180).
    if lon >= 180.0:
        lon -= 360.0
    return lat, lon


def local_decode(frame, ref_lat: float, ref_lon: float) -> tuple[float, float]:
    """Decode one CPR frame relative to a trusted nearby reference point.

    ``frame`` is a :class:`~app.adsb.PositionFrame`; ``ref_lat`` /
    ``ref_lon`` are decimal degrees of a contemporaneous reference fix.
    The CPR cell of the correct parity nearest the reference is selected
    independently for latitude and longitude; longitude is normalised to
    [-180, 180), so a legal frame near the poles or the antimeridian
    (reference at 179.99°, target cell at -179.99°) still lands on the
    grid cell adjacent to the reference.  The caller must additionally
    confirm the result lies within its acceptance radius (see
    :func:`great_circle_distance_nm`) so a distant grid ambiguity is never
    accepted.

    Raises :class:`DecodeError` with code ``POLAR_CPR_AMBIGUITY`` for an
    odd frame decoded above 87° latitude: NL collapses to 1 there, leaving
    zero odd longitude zones.  Even frames keep one longitude zone to the
    pole and remain decodable.
    """
    flag = 1 if frame.odd else 0
    dlat = 360.0 / (4 * NZ - flag)

    yz = frame.lat_cpr / CPR_SCALE
    j = math.floor(ref_lat / dlat) + math.floor(
        _fraction(ref_lat / dlat) - yz + 0.5
    )
    lat = dlat * (j + yz)

    nl = cpr_nl(lat)
    ni = nl - flag
    if ni < 1:
        # Odd frames above 87° (NL collapses to 1) carry no longitude zone
        # structure; airborne odd CPR is geometrically undefined there.
        raise DecodeError(
            "POLAR_CPR_AMBIGUITY",
            f"the {('odd' if frame.odd else 'even')} CPR frame cannot resolve "
            f"longitude at latitude {lat:.3f}",
        )
    dlon = 360.0 / ni

    xz = frame.lon_cpr / CPR_SCALE
    m = math.floor(ref_lon / dlon) + math.floor(
        _fraction(ref_lon / dlon) - xz + 0.5
    )
    lon = normalise_lon(dlon * (m + xz))
    return lat, lon


def _fraction(value: float) -> float:
    """Fractional part of ``value`` in [0, 1), also for negative numbers."""
    return value - math.floor(value)


def normalise_lon(lon: float) -> float:
    """Normalise any longitude to the half-open range [-180, 180)."""
    return (lon + 180.0) % 360.0 - 180.0


def great_circle_distance_nm(
    lat1: float, lon1: float, lat2: float, lon2: float
) -> float:
    """Great-circle distance between two fixes in nautical miles.

    The haversine formula is evaluated on longitude differences wrapped to
    [-180, 180), so fixes on opposite sides of the antimeridian
    (179.99° and -179.99°) measure the short way across the line.
    """
    rlat1 = math.radians(lat1)
    rlat2 = math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians((lon2 - lon1 + 540.0) % 360.0 - 180.0)
    h = (
        math.sin(dlat / 2.0) ** 2
        + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2.0) ** 2
    )
    # Clamp against floating-point overshoot before sqrt/asin.
    h = min(1.0, max(0.0, h))
    return 2.0 * EARTH_RADIUS_NM * math.asin(math.sqrt(h))


def encode_position(lat: float, lon: float, odd: bool) -> tuple[int, int]:
    """Encode ``lat``/``lon`` into 17-bit CPR fields.

    Inverse of :func:`global_decode`; used to build synthetic frames for
    tests and smoke checks.
    """
    flag = 1 if odd else 0
    dlat = 360.0 / (4 * NZ - flag)
    yz = math.floor(CPR_SCALE * ((lat % dlat) / dlat) + 0.5) % CPR_SCALE
    ni = max(cpr_nl(lat) - flag, 1)
    dlon = 360.0 / ni
    xz = math.floor(CPR_SCALE * ((lon % dlon) / dlon) + 0.5) % CPR_SCALE
    return yz, xz
