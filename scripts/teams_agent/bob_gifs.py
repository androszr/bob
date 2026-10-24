"""Bob's own mascot gifs for the reply-time [BOB_GIF: mood] reaction tag.

The gif files are not part of this repo: BOB_GIF_DIR names the folder that holds
them (file names in REACTION_MOODS). Unset = no reaction gifs; the tag is dropped.

Public API:
    REACTION_MOODS                                    -- mood slug -> gif filename
    gif_path(name: str) -> Path                       -- absolute path under GIF_DIR
    pick_reaction_gif_and_phrase(mood: str) -> tuple[str, str] | None
"""

import os
import random
from pathlib import Path

GIF_DIR: Path | None = Path(os.environ["BOB_GIF_DIR"]) if os.environ.get("BOB_GIF_DIR") else None

# Fixed mood vocabulary the model may emit as [BOB_GIF: mood] (see prompts/bob.md).
REACTION_MOODS: dict[str, str] = {
    "happy": "bob-happy.gif",
    "eureka": "bob-eureka.gif",
    "grooving": "bob-grooving.gif",
    "confused": "bob-working-confused.gif",
    "oops": "bob-hat-mishap.gif",
    "dizzy": "bob-dizzy.gif",
    "bye": "bob-going-away.gif",
    "wizard": "bob-working-wizard.gif",
    "overheated": "bob-working-overheated.gif",
    "juggling": "bob-working-juggling.gif",
    "waiting": "bob-fishing.gif",
}

# 2-3 short casual Polish reaction phrases per mood (caption next to the gif).
_REACTION_PHRASES: dict[str, list[str]] = {
    "happy": ["no i git", "spoko, załatwione", "no i pięknie"],
    "eureka": ["o właśnie o to chodzi", "o, mam to", "widzisz, o to chodziło"],
    "grooving": ["idzie jak z płatka", "leci gładko", "no i płynie samo"],
    "confused": ["sam nie wiem", "hmm, ciężko powiedzieć", "no nie jestem pewien"],
    "oops": ["ups, moja wina", "ojej, wpadka", "no coś mi się pomieszało"],
    "dizzy": ["ale mętlik", "trochę mi się kręci od tego", "ale zamieszanie"],
    "bye": ["to ja lecę", "spadam, do zobaczenia", "no to na razie"],
    "wizard": ["trochę czarów i gotowe", "magia, nie inaczej", "i po problemie, jak czary"],
    "overheated": ["uf, ledwo zipię", "no i padłem", "trochę mnie to wykończyło"],
    "juggling": ["mam tego kupę naraz", "żonglerka, ale ogarniam", "sporo tego naraz"],
    "waiting": ["no i dalej łowię, cierpliwości", "siedzę nad wodą i czekam", "ryba jeszcze nie brała"],
}


def gif_path(name: str) -> Path | None:
    """Absolute path to a gif file under GIF_DIR, or None when BOB_GIF_DIR is unset."""
    return GIF_DIR / name if GIF_DIR is not None else None


def pick_reaction_gif_and_phrase(mood: str) -> tuple[str, str] | None:
    """(gif filename, phrase) for `mood`, or None for an unknown mood or a missing file."""
    gif = REACTION_MOODS.get((mood or "").strip().lower())
    path = gif_path(gif) if gif is not None else None
    if path is None or not path.is_file():
        return None
    return gif, random.choice(_REACTION_PHRASES[mood.strip().lower()])  # noqa: S311
