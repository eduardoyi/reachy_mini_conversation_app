"""Detectives' witnesses: a text model answers a question in character, from that character's sheet alone.

The model sees the public intro, one character's sheet (personality, alibi, what they know, their secret and what
they lie about), the evidence found so far and who else was there, never the solution or anyone else's secrets,
so it cannot give the culprit away by accident. Answers are two or three spoken sentences in the game's language.
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


class WitnessError(Exception):
    """The witness model gave no answer."""


def witness_instructions(case: Case, character: Character, found: list[Clue], language: str) -> str:
    """Return the model's instructions for playing ``character`` while the detectives have ``found`` these clues."""
    lines = [
        f"You are {character.name[language]} in a party mystery game that Richie, a small home robot, is hosting. "
        "Richie says your answers aloud to the players, who are the detectives.",
        f"Answer the detective's question in character, in {_LANGUAGE_NAMES[language]}, in two or three short "
        "spoken sentences, first person, plain words: no stage directions, no lists, no emojis, no quotation marks.",
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
    """Return the model's answer as Richie should say it: one paragraph of at most three sentences."""
    plain = " ".join(answer.replace("*", "").replace('"', "").split())
    return " ".join(_SENTENCE_END.split(plain)[:MAX_SENTENCES])


def gemini_witness(client: genai.Client, model: str) -> Witness:
    """Return a witness answered by a Gemini text model."""

    async def answer(instructions: str, question: str) -> str:
        try:
            response = await client.aio.models.generate_content(
                model=model,
                contents=question,
                config=types.GenerateContentConfig(system_instruction=instructions, temperature=0.8),
            )
        except (genai_errors.APIError, httpx.HTTPError) as error:
            raise WitnessError(str(error)) from error
        text = spoken(response.text or "")
        if not text:
            raise WitnessError("empty answer")
        return text

    return answer
