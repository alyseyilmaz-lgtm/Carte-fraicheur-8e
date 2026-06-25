#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
filter_data.py
==============
Prépare les données légères pour la "Carte de fraîcheur du 8e arrondissement".

Le fichier national du bâti ("bati_paris.geojson", > 30 Mo) n'est JAMAIS stocké
localement : on récupère uniquement les bâtiments du 8e directement filtrés côté
serveur via l'API Paris Open Data. Les 4 fichiers GeoJSON fournis localement sont
découpés sur le polygone officiel du 8e arrondissement, puis allégés.

Ce script n'a AUCUNE dépendance externe (Python 3 standard uniquement) :
- point-in-polygon (ray casting)
- distances en mètres (projection équirectangulaire à la latitude de Paris)
- index spatial sur grille pour la recherche de voisinage

Sorties (dans ./data/) :
  - arrondissement_8e.geojson     : limite du 8e (affichage + clip)
  - troncon_8e.geojson            : rues du 8e + h_bati (hauteur bâti voisine) + cool/cool_dist
  - espaces_verts_8e.geojson      : parcs et jardins du 8e
  - ilots_fraicheur_8e.geojson    : îlots de fraîcheur du 8e
  - arbres_8e.geojson             : arbres remarquables du 8e
  - meta.json                     : compteurs et paramètres
"""

import json
import math
import os
import sys
import urllib.request

# --------------------------------------------------------------------------- #
# Configuration des chemins
# --------------------------------------------------------------------------- #
HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")

# Dossier contenant les 4 GeoJSON bruts fournis. Adaptez si besoin.
RAW_DIR = os.environ.get(
    "RAW_DIR",
    "/root/.claude/uploads/4c1efdc6-de98-50eb-bb13-aec4e9d28f78",
)

SRC = {
    "troncon": os.path.join(RAW_DIR, "197f7f48-troncon_voie.geojson"),
    "arbres": os.path.join(RAW_DIR, "3cfcbbc2-arbresremarquablesparis.geojson"),
    "ilots": os.path.join(RAW_DIR, "971444da-ilotsdefraicheurespacesvertsfrais.geojson"),
    "espaces_verts": os.path.join(RAW_DIR, "a1fb43ec-espaces_verts.geojson"),
}

# Endpoints Paris Open Data (filtrage côté serveur => téléchargements légers)
URL_ARR = ("https://opendata.paris.fr/api/explore/v2.1/catalog/datasets/"
           "arrondissements/exports/geojson")
URL_BATI = ("https://opendata.paris.fr/api/explore/v2.1/catalog/datasets/"
            "volumesbatisparis/exports/geojson?where=n_ar%3D8&select=nb_pl")

ARRT = 8                 # arrondissement cible
COOL_DIST_M = 30.0       # rayon de fraîcheur (m)
FLOOR_HEIGHT_M = 3.0     # hauteur estimée par niveau (m)
LAT0 = 48.873            # latitude de référence pour la projection métrique


# --------------------------------------------------------------------------- #
# Géométrie (sans dépendance)
# --------------------------------------------------------------------------- #
M_PER_DEG_LAT = 111_320.0
M_PER_DEG_LON = 111_320.0 * math.cos(math.radians(LAT0))


def to_m(lon, lat):
    """Projection locale (m) approchée autour de Paris."""
    return (lon * M_PER_DEG_LON, lat * M_PER_DEG_LAT)


def iter_rings(geom):
    """Itère sur les anneaux extérieurs d'un (Multi)Polygon."""
    if not geom:
        return
    t = geom.get("type")
    c = geom.get("coordinates")
    if t == "Polygon":
        if c:
            yield c[0]
    elif t == "MultiPolygon":
        for poly in c:
            if poly:
                yield poly[0]


def iter_coords(geom):
    """Itère sur tous les couples (lon, lat) d'une géométrie quelconque."""
    if not geom:
        return
    t = geom.get("type")
    c = geom.get("coordinates")
    if t == "Point":
        yield c[0], c[1]
    elif t in ("LineString", "MultiPoint"):
        for x in c:
            yield x[0], x[1]
    elif t in ("MultiLineString", "Polygon"):
        for part in c:
            for x in part:
                yield x[0], x[1]
    elif t == "MultiPolygon":
        for poly in c:
            for ring in poly:
                for x in ring:
                    yield x[0], x[1]


def point_in_ring(lon, lat, ring):
    """Ray casting : le point est-il dans l'anneau (liste de [lon,lat]) ?"""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if ((yi > lat) != (yj > lat)) and (
            lon < (xj - xi) * (lat - yi) / (yj - yi + 1e-15) + xi
        ):
            inside = not inside
        j = i
    return inside


def point_in_polygon(lon, lat, rings):
    """True si le point est dans l'un des anneaux extérieurs fournis."""
    for ring in rings:
        if point_in_ring(lon, lat, ring):
            return True
    return False


def centroid(geom):
    """Centroïde grossier (moyenne des sommets)."""
    xs = ys = 0.0
    n = 0
    for lon, lat in iter_coords(geom):
        xs += lon
        ys += lat
        n += 1
    if n == 0:
        return None
    return xs / n, ys / n


def dist_point_seg_m(px, py, ax, ay, bx, by):
    """Distance (m) d'un point P au segment AB, le tout en coordonnées projetées (m)."""
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    cx, cy = ax + t * dx, ay + t * dy
    return math.hypot(px - cx, py - cy)


# --------------------------------------------------------------------------- #
# Index spatial sur grille (pour le voisinage bâti)
# --------------------------------------------------------------------------- #
class Grid:
    def __init__(self, cell_m):
        self.cell = cell_m
        self.cells = {}

    def _key(self, xm, ym):
        return (int(xm // self.cell), int(ym // self.cell))

    def add(self, xm, ym, payload):
        self.cells.setdefault(self._key(xm, ym), []).append((xm, ym, payload))

    def near(self, xm, ym, radius_m):
        r = int(math.ceil(radius_m / self.cell))
        cx, cy = self._key(xm, ym)
        for i in range(cx - r, cx + r + 1):
            for j in range(cy - r, cy + r + 1):
                for item in self.cells.get((i, j), ()):
                    yield item


# --------------------------------------------------------------------------- #
# Chargement
# --------------------------------------------------------------------------- #
def load_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def fetch_json(url, timeout=180):
    print(f"  -> téléchargement {url[:70]}...")
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def write_fc(name, features):
    fc = {"type": "FeatureCollection", "features": features}
    path = os.path.join(DATA_DIR, name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(fc, fh, ensure_ascii=False, separators=(",", ":"))
    size_kb = os.path.getsize(path) / 1024
    print(f"  [ok] {name:32s} {len(features):6d} entités  ({size_kb:8.1f} Ko)")
    return len(features)


def slim(props, keep):
    return {k: props.get(k) for k in keep if k in props}


# --------------------------------------------------------------------------- #
# Programme principal
# --------------------------------------------------------------------------- #
def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    print("== Filtrage des données pour le 8e arrondissement ==\n")

    # 1) Limite du 8e (polygone officiel) -----------------------------------
    print("1) Limite de l'arrondissement")
    arr = fetch_json(URL_ARR)
    feat8 = None
    for f in arr["features"]:
        if f["properties"].get("c_ar") == ARRT:
            feat8 = f
            break
    if feat8 is None:
        sys.exit("Polygone du 8e introuvable.")
    rings8 = list(iter_rings(feat8["geometry"]))
    boundary = {
        "type": "Feature",
        "properties": {"arrondissement": "75008", "nom": "Paris 8e"},
        "geometry": feat8["geometry"],
    }
    write_fc("arrondissement_8e.geojson", [boundary])

    def in_8e_point(lon, lat):
        return point_in_polygon(lon, lat, rings8)

    # 2) Bâti du 8e (API, déjà filtré n_ar=8) -> hauteurs --------------------
    print("\n2) Bâti du 8e (récupéré filtré, jamais stocké en entier)")
    bati = fetch_json(URL_BATI)
    grid = Grid(60.0)  # ~60 m
    nb = 0
    for f in bati["features"]:
        c = centroid(f.get("geometry"))
        if not c:
            continue
        nb_pl = f["properties"].get("nb_pl") or 0
        h = max(FLOOR_HEIGHT_M, float(nb_pl) * FLOOR_HEIGHT_M)
        xm, ym = to_m(c[0], c[1])
        grid.add(xm, ym, h)
        nb += 1
    print(f"  {nb} bâtiments indexés (hauteur ~ nb_niveaux x {FLOOR_HEIGHT_M} m)")

    # 3) Espaces verts du 8e ------------------------------------------------
    print("\n3) Espaces verts")
    ev = load_json(SRC["espaces_verts"])
    ev_feats = []
    for f in ev["features"]:
        props = f["properties"]
        cp = str(props.get("adresse_codepostal") or "")
        c = centroid(f.get("geometry"))
        keep = cp == "75008" or (c and in_8e_point(c[0], c[1]))
        if keep:
            f["properties"] = slim(props, [
                "nom_ev", "type_ev", "categorie", "surface_totale_reelle",
            ])
            ev_feats.append(f)
    write_fc("espaces_verts_8e.geojson", ev_feats)

    # 4) Îlots de fraîcheur du 8e -------------------------------------------
    print("\n4) Îlots de fraîcheur")
    il = load_json(SRC["ilots"])
    il_feats = []
    for f in il["features"]:
        props = f["properties"]
        ar = str(props.get("arrondissement") or "")
        c = centroid(f.get("geometry"))
        keep = ar == "75008" or (c and in_8e_point(c[0], c[1]))
        if keep:
            f["properties"] = slim(props, [
                "nom", "type", "categorie", "statut_ouverture",
                "proportion_vegetation_haute",
            ])
            il_feats.append(f)
    write_fc("ilots_fraicheur_8e.geojson", il_feats)

    # 5) Arbres remarquables du 8e ------------------------------------------
    print("\n5) Arbres remarquables")
    ar = load_json(SRC["arbres"])
    ar_feats = []
    for f in ar["features"]:
        props = f["properties"]
        a = str(props.get("com_arrondissement") or "")
        g = f.get("geometry")
        c = centroid(g)
        keep = a == "8" or (c and in_8e_point(c[0], c[1]))
        if keep:
            f["properties"] = slim(props, [
                "com_nom_usuel", "com_nom_latin", "arbres_hauteurenm",
                "arbres_libellefrancais", "com_adresse",
            ])
            ar_feats.append(f)
    write_fc("arbres_8e.geojson", ar_feats)

    # --- Points "frais" pour le test de proximité (sommets parcs/ilots + arbres)
    cool_edges = []   # segments (en m) des contours frais
    cool_pts = []     # points frais (arbres) en m

    def add_polygon_edges(geom):
        for ring in iter_rings(geom):
            pm = [to_m(p[0], p[1]) for p in ring]
            for i in range(len(pm) - 1):
                cool_edges.append((pm[i][0], pm[i][1], pm[i + 1][0], pm[i + 1][1]))

    for f in ev_feats:
        add_polygon_edges(f["geometry"])
    for f in il_feats:
        add_polygon_edges(f["geometry"])
    for f in ar_feats:
        for lon, lat in iter_coords(f["geometry"]):
            cool_pts.append(to_m(lon, lat))

    # Index des contours frais pour accélérer la proximité
    cool_grid = Grid(COOL_DIST_M)
    for idx, e in enumerate(cool_edges):
        midx, midy = (e[0] + e[2]) / 2, (e[1] + e[3]) / 2
        cool_grid.add(midx, midy, ("edge", idx))
    for idx, p in enumerate(cool_pts):
        cool_grid.add(p[0], p[1], ("pt", idx))

    def cool_distance_m(xm, ym):
        best = float("inf")
        for _, _, payload in cool_grid.near(xm, ym, COOL_DIST_M * 2):
            kind, i = payload
            if kind == "edge":
                e = cool_edges[i]
                d = dist_point_seg_m(xm, ym, e[0], e[1], e[2], e[3])
            else:
                p = cool_pts[i]
                d = math.hypot(xm - p[0], ym - p[1])
            if d < best:
                best = d
        return best

    # 6) Tronçons de voie du 8e + hauteur bâti + fraîcheur ------------------
    print("\n6) Tronçons de voie (rues) + calculs spatiaux")
    tv = load_json(SRC["troncon"])
    tv_feats = []
    for f in tv["features"]:
        g = f.get("geometry")
        if not g or g.get("type") not in ("LineString", "MultiLineString"):
            continue
        pts = list(iter_coords(g))
        if not pts:
            continue
        # Garder la rue si un de ses sommets (ou son milieu) est dans le 8e
        mid = pts[len(pts) // 2]
        inside = in_8e_point(mid[0], mid[1]) or any(in_8e_point(p[0], p[1]) for p in pts)
        if not inside:
            continue

        # Hauteur bâti voisine = max des bâtiments à < ~35 m des sommets
        h_max = 0.0
        cool_d = float("inf")
        for lon, lat in pts:
            xm, ym = to_m(lon, lat)
            for bxm, bym, bh in grid.near(xm, ym, 35.0):
                if math.hypot(xm - bxm, ym - bym) <= 35.0 and bh > h_max:
                    h_max = bh
            d = cool_distance_m(xm, ym)
            if d < cool_d:
                cool_d = d

        props = f["properties"]
        f["properties"] = {
            "nom": props.get("c_tvniv1") or props.get("n_sq_vo") or "",
            "h_bati": round(h_max, 1),
            "cool": bool(cool_d <= COOL_DIST_M),
            "cool_dist": round(cool_d, 1) if cool_d != float("inf") else None,
        }
        tv_feats.append(f)
    n_tv = write_fc("troncon_8e.geojson", tv_feats)

    # 7) meta ---------------------------------------------------------------
    n_cool = sum(1 for f in tv_feats if f["properties"]["cool"])
    heights = [f["properties"]["h_bati"] for f in tv_feats]
    meta = {
        "arrondissement": "75008",
        "center": [48.8718, 2.3120],
        "params": {
            "cool_dist_m": COOL_DIST_M,
            "floor_height_m": FLOOR_HEIGHT_M,
        },
        "counts": {
            "troncons": n_tv,
            "troncons_frais": n_cool,
            "espaces_verts": len(ev_feats),
            "ilots_fraicheur": len(il_feats),
            "arbres": len(ar_feats),
            "batiments_indexes": nb,
        },
        "h_bati": {
            "max": max(heights) if heights else 0,
            "mean": round(sum(heights) / len(heights), 1) if heights else 0,
        },
    }
    with open(os.path.join(DATA_DIR, "meta.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)
    print("\n== Terminé ==")
    print(json.dumps(meta["counts"], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
