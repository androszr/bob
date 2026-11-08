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

# 2-3 short casual reaction phrases per mood (caption next to the gif).
_REACTION_PHRASES: dict[str, list[str]] = {
    "happy": ["nice", "sorted, done", "lovely"],
    "eureka": ["that's exactly it", "oh, got it", "see, that was the point"],
    "grooving": ["smooth sailing", "rolling along nicely", "it just flows"],
    "confused": ["no idea, honestly", "hmm, hard to say", "not really sure"],
    "oops": ["oops, my bad", "whoops, slip-up", "got something mixed up"],
    "dizzy": ["what a muddle", "my head's spinning a bit", "what a mess"],
    "bye": ["I'm off", "heading out, see you", "bye for now"],
    "wizard": ["a little magic and done", "magic, what else", "problem gone, like magic"],
    "overheated": ["phew, barely breathing", "I'm wiped out", "that wore me out a bit"],
    "juggling": ["a lot going on at once", "juggling, but handling it", "plenty at once"],
    "waiting": ["still fishing, patience", "sitting by the water, waiting", "no bites yet"],
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
