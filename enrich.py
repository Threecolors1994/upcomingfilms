#!/usr/bin/env python3
"""
Enrichit une liste Letterboxd (exportée en CSV) avec les informations TMDB
nécessaires à Cinéscope : titre FR, affiche, dates de sortie françaises.

Usage:
    export TMDB_API_KEY="ta_cle_ici"
    python enrich.py "Upcoming - 2026.csv" > cinescope.json

    # Ou avec un argument :
    python enrich.py "Upcoming - 2026.csv" --output cinescope.json

Dépendances:
    pip install requests
"""

import argparse
import base64
import csv
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

TMDB_BASE = "https://api.themoviedb.org/3"
TMDB_IMG = "https://image.tmdb.org/t/p"
POSTER_SIZE = "w185"  # embarqué en base64 dans le JSON


def parse_letterboxd_list(path: Path):
    """Parse un export de liste Letterboxd (format avec deux sections)."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    # Trouver la section films
    films_header_idx = -1
    for i, line in enumerate(lines):
        if re.match(r"^Position\s*,\s*Name\s*,\s*Year", line, re.IGNORECASE):
            films_header_idx = i
            break

    # Extraire les métadonnées de la liste (si présentes)
    list_meta = None
    if films_header_idx > 0:
        for i in range(films_header_idx):
            if re.match(r"^Date\s*,\s*Name\s*,", lines[i], re.IGNORECASE):
                meta_text = "\n".join(lines[i:films_header_idx])
                reader = csv.DictReader(meta_text.splitlines())
                for row in reader:
                    list_meta = {
                        "date": row.get("Date"),
                        "name": row.get("Name"),
                        "url": row.get("URL"),
                        "description": row.get("Description"),
                    }
                    break
                break

    # Parser la section films
    films_text = (
        "\n".join(lines[films_header_idx:])
        if films_header_idx >= 0
        else text
    )
    reader = csv.DictReader(films_text.splitlines())
    films = []
    for row in reader:
        name = (row.get("Name") or "").strip()
        if not name:
            continue
        films.append(
            {
                "name": name,
                "year": int(row["Year"]) if row.get("Year") else None,
                "position": int(row["Position"]) if row.get("Position") else None,
                "user_description": (row.get("Description") or "").strip() or None,
            }
        )

    return list_meta, films


def tmdb_search(session, title, year, api_key):
    params = {
        "query": title,
        "language": "fr-FR",
        "include_adult": "false",
        "api_key": api_key,
    }
    if year:
        params["year"] = year
    r = session.get(f"{TMDB_BASE}/search/movie", params=params, timeout=15)
    r.raise_for_status()
    results = r.json().get("results", [])
    return results[0] if results else None


def tmdb_release_dates(session, movie_id, api_key):
    r = session.get(
        f"{TMDB_BASE}/movie/{movie_id}/release_dates",
        params={"api_key": api_key},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def tmdb_credits(session, movie_id, api_key):
    r = session.get(
        f"{TMDB_BASE}/movie/{movie_id}/credits",
        params={"api_key": api_key},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def pick_directors(credits_data):
    """Liste des réalisateurs (souvent un, parfois plusieurs)."""
    if not credits_data or not credits_data.get("crew"):
        return []
    return [
        member["name"]
        for member in credits_data["crew"]
        if member.get("job") == "Director"
    ]


def fetch_poster_b64(session, poster_path):
    """Télécharge l'affiche et la renvoie en base64. None si échec ou absente."""
    if not poster_path:
        return None
    try:
        url = f"{TMDB_IMG}/{POSTER_SIZE}{poster_path}"
        r = session.get(url, timeout=20)
        r.raise_for_status()
        return base64.b64encode(r.content).decode("ascii")
    except Exception:
        return None


def pick_french_release(release_data):
    """Priorité : FR théâtre > FR limitée > FR première > BE > CH > CA > monde."""
    if not release_data or not release_data.get("results"):
        return None, None, None

    def find_country(iso):
        for entry in release_data["results"]:
            if entry["iso_3166_1"] == iso:
                return entry
        return None

    # Types : 1=premiere, 2=limited, 3=theatrical, 4=digital, 5=physical, 6=tv
    priority_types = [3, 2, 1, 4]
    for iso in ["FR", "BE", "CH", "CA", "LU"]:
        entry = find_country(iso)
        if not entry:
            continue
        for t in priority_types:
            for d in entry["release_dates"]:
                if d["type"] == t:
                    return d["release_date"], t, iso

    # Fallback : la première sortie cinéma mondiale
    earliest = None
    for entry in release_data["results"]:
        for d in entry["release_dates"]:
            if d["type"] in (2, 3):
                if earliest is None or d["release_date"] < earliest[0]:
                    earliest = (d["release_date"], d["type"], entry["iso_3166_1"])
    return earliest if earliest else (None, None, None)


def enrich_films(films, api_key, verbose=True):
    enriched = []
    session = requests.Session()
    total = len(films)
    for i, film in enumerate(films, 1):
        if verbose:
            print(
                f"[{i:>2}/{total}] {film['name']:<45}",
                end="",
                file=sys.stderr,
                flush=True,
            )
        try:
            movie = tmdb_search(session, film["name"], film["year"], api_key)
            if not movie:
                enriched.append(
                    {
                        **film,
                        "title": film["name"],
                        "release_date": None,
                        "status": "notfound",
                    }
                )
                if verbose:
                    print("  → introuvable", file=sys.stderr)
                continue

            releases = tmdb_release_dates(session, movie["id"], api_key)
            date, rtype, country = pick_french_release(releases)
            credits = tmdb_credits(session, movie["id"], api_key)
            directors = pick_directors(credits)
            poster_b64 = fetch_poster_b64(session, movie.get("poster_path"))

            enriched.append(
                {
                    "position": film["position"],
                    "user_description": film["user_description"],
                    "input_name": film["name"],
                    "tmdb_id": movie["id"],
                    "title": movie["title"],
                    "original_title": movie["original_title"],
                    "year": (movie.get("release_date") or "")[:4] or film["year"],
                    "directors": directors,
                    "poster_path": movie.get("poster_path"),
                    "poster_b64": poster_b64,
                    "overview": movie.get("overview"),
                    "vote_average": movie.get("vote_average"),
                    "release_date": date[:10] if date else None,
                    "release_type": rtype,
                    "release_country": country,
                    "status": "ok",
                }
            )
            if verbose:
                date_str = date[:10] if date else "—"
                country_str = f" ({country})" if country and country != "FR" else ""
                dir_str = f" · {', '.join(directors)}" if directors else ""
                print(f"  → {date_str}{country_str}{dir_str}", file=sys.stderr)

        except requests.HTTPError as e:
            enriched.append(
                {
                    **film,
                    "title": film["name"],
                    "release_date": None,
                    "status": "error",
                    "error_message": f"HTTP {e.response.status_code}",
                }
            )
            if verbose:
                print(f"  → ERREUR {e.response.status_code}", file=sys.stderr)
        except Exception as e:
            enriched.append(
                {
                    **film,
                    "title": film["name"],
                    "release_date": None,
                    "status": "error",
                    "error_message": str(e),
                }
            )
            if verbose:
                print(f"  → ERREUR {e}", file=sys.stderr)

        time.sleep(0.05)  # courtoisie envers l'API
    return enriched


def main():
    parser = argparse.ArgumentParser(
        description="Enrichit une liste Letterboxd avec TMDB pour Cinéscope."
    )
    parser.add_argument("csv", type=Path, help="Chemin vers le CSV Letterboxd")
    parser.add_argument(
        "--output", "-o", type=Path, default=None,
        help="Fichier de sortie (défaut : stdout)",
    )
    args = parser.parse_args()

    api_key = os.environ.get("TMDB_API_KEY")
    if not api_key:
        print(
            "Erreur : la variable d'environnement TMDB_API_KEY n'est pas définie.\n"
            "Exemple : export TMDB_API_KEY='ta_cle_ici'",
            file=sys.stderr,
        )
        sys.exit(1)

    if not args.csv.exists():
        print(f"Erreur : fichier introuvable : {args.csv}", file=sys.stderr)
        sys.exit(1)

    list_meta, films = parse_letterboxd_list(args.csv)
    print(
        f"Liste : {list_meta['name'] if list_meta else '(sans titre)'} — "
        f"{len(films)} films",
        file=sys.stderr,
    )
    print(file=sys.stderr)

    enriched = enrich_films(films, api_key)

    output = {
        "list_meta": list_meta,
        "films": enriched,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }

    json_str = json.dumps(output, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(json_str, encoding="utf-8")
        print(f"\n✓ Écrit dans {args.output}", file=sys.stderr)
    else:
        print(json_str)

    # Récap
    ok = sum(1 for f in enriched if f.get("status") == "ok")
    dated = sum(1 for f in enriched if f.get("release_date"))
    print(
        f"\nRécap : {ok}/{len(films)} trouvés sur TMDB, {dated} avec date de sortie.",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
