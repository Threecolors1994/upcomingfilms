#!/usr/bin/env python3
"""
Compare la version actuelle d'un JSON enrichi avec celle qui est dans git
(avant le commit en cours) et produit un résumé markdown des changements
notables : nouvelles dates de sortie confirmées, dates modifiées,
nouveaux films ajoutés à la liste.

Sortie : markdown sur stdout si des changements notables sont détectés,
         rien sinon (permet au workflow de tester $output != "").

Usage:
    python notify_changes.py data/Upcoming_-_2026.json
"""

import json
import subprocess
import sys
from pathlib import Path


def load_from_git(path):
    """Charge la version du fichier telle qu'elle est dans git HEAD."""
    try:
        result = subprocess.run(
            ["git", "show", f"HEAD:{path}"],
            capture_output=True, text=True, check=True,
        )
        return json.loads(result.stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return None


def film_key(f):
    """Identifiant stable d'un film entre deux runs."""
    return f.get("tmdb_id") or f.get("input_name")


def main():
    if len(sys.argv) < 2:
        print("Usage: notify_changes.py <path-to-json>", file=sys.stderr)
        sys.exit(1)

    path = sys.argv[1]
    new_data = json.loads(Path(path).read_text(encoding="utf-8"))
    old_data = load_from_git(path)

    if old_data is None:
        # Premier run, rien à comparer
        return

    old_films = {film_key(f): f for f in old_data.get("films", [])}
    new_films = {film_key(f): f for f in new_data.get("films", [])}

    newly_dated = []   # avait pas de date, maintenant oui
    date_changed = []  # date différente
    added = []         # nouveau film dans la liste

    for k, nf in new_films.items():
        of = old_films.get(k)
        if of is None:
            added.append(nf)
            continue
        old_date = of.get("release_date")
        new_date = nf.get("release_date")
        if not old_date and new_date:
            newly_dated.append(nf)
        elif old_date and new_date and old_date != new_date:
            date_changed.append((nf, old_date, new_date))

    if not (newly_dated or date_changed or added):
        return

    list_name = (new_data.get("list_meta") or {}).get("name") or "Liste"

    def line(film, extra=""):
        title = film.get("title", "—")
        directors = film.get("directors") or []
        dir_part = f" · *{', '.join(directors)}*" if directors else ""
        link = ""
        if film.get("tmdb_id"):
            link = f" [↗](https://www.themoviedb.org/movie/{film['tmdb_id']})"
        return f"- **{title}**{dir_part}{extra}{link}"

    out = [f"# {list_name} — mise à jour", ""]

    if newly_dated:
        out.append("## 📅 Nouvelles dates de sortie confirmées")
        out.append("")
        for f in newly_dated:
            country = f.get("release_country", "")
            country_part = f" ({country})" if country and country != "FR" else ""
            out.append(line(f, extra=f" → **{f['release_date']}**{country_part}"))
        out.append("")

    if date_changed:
        out.append("## 🔄 Dates de sortie modifiées")
        out.append("")
        for f, old, new in date_changed:
            out.append(line(f, extra=f" : {old} → **{new}**"))
        out.append("")

    if added:
        out.append("## ➕ Nouveaux films dans la liste")
        out.append("")
        for f in added:
            date = f.get("release_date")
            date_part = f" — sortie le {date}" if date else " — date non confirmée"
            out.append(line(f, extra=date_part))
        out.append("")

    print("\n".join(out))


if __name__ == "__main__":
    main()
