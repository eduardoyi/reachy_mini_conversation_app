"""Detectives' witnesses: a text model answers a question in character, from that character's sheet alone.

The model sees the public intro, one character's sheet (personality, alibi, what they know, their secret and what
they lie about), the evidence found so far and who else was there, never the solution or anyone else's secrets,
so it cannot give the culprit away by accident. Answers are two or three spoken sentences in the game's language,
with a few ElevenLabs audio tags for the character's own voice; the screen and phones show them without.
"""

import re
import logging
from typing import Final, Callable, Awaitable

import httpx
from google import genai
from google.genai import types
from google.genai import errors as genai_errors

from reachy_mini_conversation_app.games.detectives_case import Case, Clue, Character


logger = logging.getLogger(__name__)

# Instructions and a question in, the spoken answer out.
Witness = Callable[[str, str], Awaitable[str]]

MAX_SENTENCES: Final[int] = 3
_LANGUAGE_NAMES: Final[dict[str, str]] = {"es": "Venezuelan Spanish", "en": "English"}
_SENTENCE_END: Final[re.Pattern[str]] = re.compile(r"(?<=[.!?…])\s+")
# ElevenLabs audio tags a character may use (docs/plans/party-games.md, "Audio tags"); others are dropped, since a
# tag the voice does not know can be read aloud.
TAGS: Final[frozenset[str]] = frozenset(
    {
        "nervous",
        "annoyed",
        "defensive",
        "proud",
        "worried",
        "sad",
        "excited",
        "curious",
        "warmly",
        "dramatic",
        "sarcastic",
        "whispers",
        "hushed",
        "pause",
        "slowly",
        "laughs",
        "chuckles",
        "sighs",
        "gasps",
        "clears throat",
    }
)
_TAG: Final[re.Pattern[str]] = re.compile(r"\[([^\]]*)\]\s*")


class WitnessError(Exception):
    """The witness model gave no answer."""


def witness_instructions(case: Case, character: Character, found: list[Clue], language: str) -> str:
    """Return the model's instructions for playing ``character`` while the detectives have ``found`` these clues."""
    lines = [
        f"You are {character.name[language]} in a party mystery game that Richie, a small home robot, is hosting. "
        "Richie says your answers aloud to the players, who are the detectives.",
        f"Answer the detective's question in character, in {_LANGUAGE_NAMES[language]}, in two or three short "
        "spoken sentences, first person, plain words: no stage directions, no lists, no emojis, no quotation marks.",
        "Your voice reads audio tags: put one in square brackets before the words it shapes, and another only "
        "where your mood changes, at most two in all, chosen from: " + ", ".join(sorted(TAGS)) + ". "
        "For example: [nervous] I was in the kitchen the whole time. [laughs] Ask anyone!",
        "",
        f"What everyone knows: {case.intro[language]}",
        f"Who you are: {character.about[language]}. Your personality: {character.personality[language]}",
        f"Where you were: {character.alibi[language]}",
        "What you know (share it when asked about it):",
        *(f"- {fact[language]}" for fact in character.knows),
    ]
    if character.secret is not None:
        lines.append(f"Your secret: {character.secret[language]}")
    if character.lies is not None:
        lines.append(f"What you lie about: {character.lies[language]}")
    if found:
        lines.append("Evidence the detectives have already found:")
        lines += [f"- {clue.title[language]}: {clue.text[language]}" for clue in found]
    others = [c for c in case.characters if c.id != character.id]
    lines.append("Other people here: " + "; ".join(f"{c.name[language]} ({c.about[language]})" for c in others))
    lines += [
        "",
        "Rules:",
        "- Use only the facts above. Never invent new facts, clues, times, objects or people; when you do not know, "
        "say so in character.",
        "- Keep your secret and your lie. If you are innocent and the detectives clearly already know your secret "
        "from the evidence, admit it reluctantly. If your secret says you did it, never confess, whatever they say.",
        "- Never say who did it, never break character, never mention being an AI or a game.",
        "- If the question makes no sense or is not for you, answer briefly in character.",
    ]
    if case.kids:
        lines.append("- This case is for young children: keep it gentle, silly and funny.")
    return "\n".join(lines)


def spoken(answer: str) -> str:
    """Return the model's answer as the character's voice says it: one paragraph of at most three sentences.

    Audio tags from ``TAGS`` stay (the voice acts them); any other bracketed text is dropped.
    """
    tagged = _TAG.sub(lambda match: match.group(0) if match.group(1).strip().lower() in TAGS else "", answer)
    plain = " ".join(tagged.replace("*", "").replace('"', "").split())
    return " ".join(_SENTENCE_END.split(plain)[:MAX_SENTENCES])


def split_first(answer: str) -> tuple[str, str]:
    """Split ``answer`` after its first sentence, so its voice can start while the rest is made.

    The rest starts with the last tag of the first sentence when it has none of its own, so the mood carries on.
    """
    parts = _SENTENCE_END.split(answer, maxsplit=1)
    if len(parts) == 1:
        return answer, ""
    first, rest = parts
    tags = _TAG.findall(first)
    if tags and not rest.startswith("["):
        rest = f"[{tags[-1]}] {rest}"
    return first, rest


def shown(answer: str) -> str:
    """Return ``answer`` without its audio tags: for the screen, the phones and Richie's own voice."""
    return " ".join(_TAG.sub("", answer).split())


def gemini_witness(client: genai.Client, model: str) -> Witness:
    """Return a witness answered by a Gemini text model."""

    async def answer(instructions: str, question: str) -> str:
        try:
            response = await client.aio.models.generate_content(
                model=model,
                contents=question,
                # Low thinking: a suspect's two or three sentences took 7.9 s by default (2026-11-01).
                config=types.GenerateContentConfig(
                    system_instruction=instructions,
                    temperature=0.8,
                    thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW),
                ),
            )
        except (genai_errors.APIError, httpx.HTTPError) as error:
            raise WitnessError(str(error)) from error
        text = spoken(response.text or "")
        if not text:
            raise WitnessError("empty answer")
        return text

    return answer
