"""SCAP's single locked visual style, plus the lettering filter for image prompts.

The look itself (Pixel Night Lab) lives in pixel_scenes.py and moss_sprite.py;
the direction packet is docs/style-lock/STYLE.md.
"""

import re

DEFAULT_STYLE = "pixel_night_lab"


# Scene descriptions often ask for lettering ("vials labeled H5N1", "grids
# labeled 'BigGAN 2018'"). Image models then paint misspelled words under the
# burned-in captions, so the request is removed before any prompt is built.
_LETTERING_REQUESTS = (
    re.compile(
        r"\s*\b(?:labell?ed|captioned|annotated)\b(?:\s+(?:with|as))?"
        r"(?:\s+(?!(?:being|showing|while|and|in|on|at|next|beside|against|"
        r"that|which|from|under|over|near|glowing|floating)\b)[^\s,.;]+){1,5}",
        re.I,
    ),
    re.compile(r"\s*\b(?:reading|that says|saying)\s+['\"][^'\"]*['\"]", re.I),
    re.compile(r"\s*\"[^\"]{1,80}\""),
    re.compile(r"\s*(?<![A-Za-z])'[^']{1,80}'(?![A-Za-z])"),
)



def strip_lettering_requests(text: str) -> str:
    """Drop requests for words, labels, or quoted strings from an image idea."""
    cleaned = str(text or "")
    for pattern in _LETTERING_REQUESTS:
        cleaned = pattern.sub("", cleaned)
    cleaned = re.sub(r"\s+([,.;])", r"\1", cleaned)
    cleaned = re.sub(r"([,;])(?:\s*[,;])+", r"\1", cleaned)
    return re.sub(r"\s{2,}", " ", cleaned).strip(" ,;")

