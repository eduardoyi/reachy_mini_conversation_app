"""What Richie says in the kids' games (the color hunt, the treasure hunt and Simón dice), recorded once.

Each moment has two or more takes so the games don't sound the same every round, written for children of 2 to
6 with adults in the room: warm and playful, short, never shouting. Lines carry ElevenLabs audio tags in square
brackets (docs/plans/party-games.md, "Host, sounds and music"); ``shown`` strips them for anything displayed.
Spanish is Venezuelan and avoids words that change with the child's gender ("¡qué buen ojo!", not
"¡qué campeón!"). Clips are made by ``scripts/big_brain_voice.py --set kids`` into ``game-data/kids/voice/<moment>/<language>-
NN.wav`` and played by ``games/kids_audio.py``; a moment without clips is said in Richie's everyday voice, with
its first take's words.
"""

from typing import Final

from reachy_mini_conversation_app.games.color_hunt import TARGETS
from reachy_mini_conversation_app.games.simon_says import POSES
from reachy_mini_conversation_app.games.treasure_hunt import TREASURES


Takes = dict[str, tuple[str, ...]]

# The tags these lines use (a check flags a clip that reads one aloud).
TAGS: Final[frozenset[str]] = frozenset(
    {"cheerful", "excited", "playful", "whispers", "giggles", "curious", "warm", "gasps", "softly", "proud"}
)

_SHARED: Final[dict[str, Takes]] = {
    "round_first": {
        "es": ("[cheerful] ¡Primera ronda!", "[excited] ¡Arrancamos!"),
        "en": ("[cheerful] Round one!", "[excited] Here we go!"),
    },
    "round_next": {
        "es": ("[playful] ¡Otra!", "[cheerful] ¡Siguiente!", "[excited] ¡Vamos con otra!", "[playful] ¡Ahí va otra!"),
        "en": ("[playful] Another one!", "[cheerful] Next!", "[excited] Here comes another!", "[playful] Ready? Next one!"),
    },
    "round_last": {
        "es": ("[excited] ¡Última ronda!", "[whispers] Esta es la última... [excited] ¡vamos!"),
        "en": ("[excited] Last round!", "[whispers] This is the last one... [excited] let's go!"),
    },
    "nudge": {
        "es": (
            "[curious] ¿Dónde estará? [playful] Busca, busca...",
            "[whispers] Mira debajo de las cosas...",
            "[playful] ¡Tú puedes! Sigue buscando.",
            "[cheerful] ¡Busca por toda la casa!",
        ),
        "en": (
            "[curious] Where could it be? [playful] Look, look...",
            "[whispers] Try looking under things...",
            "[playful] You can do it! Keep looking.",
            "[cheerful] Look all around the house!",
        ),
    },
    "hurry": {
        "es": ("[excited] ¡Rápido, rápido, que se acaba el tiempo!", "[playful] ¡Apúrate, ya casi!", "[excited] ¡Quedan diez segundos!"),
        "en": ("[excited] Quick, quick, time's almost up!", "[playful] Hurry, hurry!", "[excited] Ten seconds left!"),
    },
    "time_up": {
        "es": (
            "[softly] ¡Uy! Se acabó el tiempo.",
            "[playful] ¡Tiempo! [giggles] Ese estaba difícil.",
            "[warm] Se acabó el tiempo... ¡la próxima lo encontramos!",
        ),
        "en": (
            "[softly] Oops! Time's up.",
            "[playful] Time! [giggles] That one was tricky.",
            "[warm] Time's up... we'll find it next time!",
        ),
    },
    "wrong": {
        "es": (
            "[playful] Mmm, eso no es.",
            "[giggles] ¡Uy, casi! Pero no es eso.",
            "[curious] ¿Eso? Mmm... no.",
        ),
        "en": (
            "[playful] Hmm, that's not it.",
            "[giggles] Ooh, close! But not that one.",
            "[curious] That? Hmm... nope.",
        ),
    },
    "again": {
        "es": ("[cheerful] ¿Jugamos otra vez?", "[playful] ¿Otra? ¿Sí?"),
        "en": ("[cheerful] Shall we play again?", "[playful] Again? Yes?"),
    },
    "thanks": {
        "es": (
            "[warm] ¡Gracias por jugar conmigo!",
            "[cheerful] ¡Qué divertido! ¡Gracias por jugar!",
            "[giggles] ¡Chao, chao! ¡Gracias por jugar!",
        ),
        "en": (
            "[warm] Thanks for playing with me!",
            "[cheerful] That was fun! Thanks for playing!",
            "[giggles] Bye-bye! Thanks for playing!",
        ),
    },
    # How many stars at the end: up to five in the hunts, up to eight in Simón dice.
    "stars_0": {
        "es": (
            "[warm] Esta vez no hubo estrellas, [cheerful] ¡pero qué divertido fue!",
            "[softly] Ninguna estrella esta vez... [playful] ¡la próxima seguro que sí!",
        ),
        "en": (
            "[warm] No stars this time, [cheerful] but that was fun!",
            "[softly] No stars this time... [playful] next time for sure!",
        ),
    },
    "stars_1": {
        "es": ("[cheerful] ¡Ganaste una estrella!", "[excited] ¡Una estrella! ¡Bien!"),
        "en": ("[cheerful] You won a star!", "[excited] One star! Yay!"),
    },
    "stars_2": {
        "es": ("[cheerful] ¡Dos estrellas!", "[excited] ¡Dos estrellas! ¡Muy bien!"),
        "en": ("[cheerful] Two stars!", "[excited] Two stars! Well done!"),
    },
    "stars_3": {
        "es": ("[excited] ¡Tres estrellas!", "[proud] ¡Tres estrellas! ¡Qué bien buscas!"),
        "en": ("[excited] Three stars!", "[proud] Three stars! You're a great finder!"),
    },
    "stars_4": {
        "es": ("[excited] ¡Cuatro estrellas!", "[gasps] ¡Cuatro estrellas! ¡Casi todas!"),
        "en": ("[excited] Four stars!", "[gasps] Four stars! Almost all of them!"),
    },
    "stars_5": {
        "es": ("[excited] ¡Cinco estrellas! [gasps] ¡Increíble!", "[proud] ¡Cinco estrellas! [giggles] ¡Qué maravilla!"),
        "en": ("[excited] Five stars! [gasps] Amazing!", "[proud] Five stars! [giggles] Wonderful!"),
    },
    "stars_6": {
        "es": ("[excited] ¡Seis estrellas!", "[proud] ¡Seis estrellas! ¡Qué bien!"),
        "en": ("[excited] Six stars!", "[proud] Six stars! Great job!"),
    },
    "stars_7": {
        "es": ("[excited] ¡Siete estrellas!", "[gasps] ¡Siete estrellas! ¡Casi todas!"),
        "en": ("[excited] Seven stars!", "[gasps] Seven stars! Almost all of them!"),
    },
    "stars_8": {
        "es": ("[excited] ¡Ocho estrellas! [gasps] ¡Todas!", "[proud] ¡Ocho de ocho! [giggles] ¡Qué maravilla!"),
        "en": ("[excited] Eight stars! [gasps] All of them!", "[proud] Eight out of eight! [giggles] Wonderful!"),
    },
}  # fmt: skip

_COLORS: Final[dict[str, Takes]] = {
    "colors_start": {
        "es": (
            "[excited] ¡Vamos a jugar a los colores! [playful] Yo digo un color... y tú me traes algo de ese color.",
            "[cheerful] ¡Epa! ¡Llegó la hora de los colores!",
            "[playful] ¿Listos? [excited] ¡A cazar colores!",
        ),
        "en": (
            "[excited] Let's play color hunt! [playful] I say a color... and you bring me something that color.",
            "[cheerful] Hey! It's color hunt time!",
            "[playful] Ready? [excited] Let's go hunting for colors!",
        ),
    },
}
for _key, (_spanish, _english, _) in TARGETS.items():
    _es, _en = _spanish, _english
    _COLORS[f"colors_find_{_key}"] = {
        "es": (
            f"[playful] ¿Quién me trae algo... [excited] ¡{_es.upper()}!",
            f"[curious] A ver, a ver... [cheerful] ¡busca algo {_es}!",
            f"[whispers] Psst... busca algo... [excited] ¡{_es.upper()}!",
        ),
        "en": (
            f"[playful] Who can bring me something... [excited] {_en.upper()}!",
            f"[curious] Hmm, let's see... [cheerful] find something {_en}!",
            f"[whispers] Psst... go find something... [excited] {_en.upper()}!",
        ),
    }
    _COLORS[f"colors_again_{_key}"] = {
        "es": (f"[cheerful] ¡Busca algo {_es}!",),
        "en": (f"[cheerful] Find something {_en}!",),
    }
    _COLORS[f"colors_found_{_key}"] = {
        "es": (
            f"[excited] ¡Sí! ¡{_es.capitalize()}! [giggles] ¡Qué buen {_es}!",
            f"[gasps] ¡Lo encontraste! [cheerful] ¡Eso es {_es}!",
            f"[proud] ¡Muy bien! [playful] {_es.capitalize()}, {_es}, ¡{_es}!",
        ),
        "en": (
            f"[excited] Yes! {_en.capitalize()}! [giggles] What a great {_en}!",
            f"[gasps] You found it! [cheerful] That's {_en}!",
            f"[proud] Well done! [playful] {_en.capitalize()}, {_en}, {_en}!",
        ),
    }
    _COLORS[f"colors_seen_{_key}"] = {
        "es": (f"[curious] Mmm... eso es {_es}.", f"[playful] ¡Uy! Eso es {_es}."),
        "en": (f"[curious] Hmm... that's {_en}.", f"[playful] Oops! That's {_en}."),
    }  # fmt: skip

_TREASURE: Final[dict[str, Takes]] = {
    "treasure_start": {
        "es": (
            "[excited] ¡Vamos a buscar tesoros! [whispers] La casa está llena de cosas escondidas...",
            "[playful] ¡Ponte tu sombrero de explorador! [excited] ¡Nos vamos de cacería de tesoros!",
            "[cheerful] ¡A buscar tesoros por toda la casa!",
        ),
        "en": (
            "[excited] Let's go on a treasure hunt! [whispers] The house is full of hidden things...",
            "[playful] Put on your explorer hat! [excited] We're going treasure hunting!",
            "[cheerful] Let's hunt for treasure all around the house!",
        ),
    },
}
for _key, _treasure in TREASURES.items():
    _es, _en = _treasure.spanish, _treasure.english
    _TREASURE[f"treasure_find_{_key}"] = {
        "es": (f"[playful] ¡Busca {_es}!", f"[excited] ¡Tráeme {_es}!", f"[whispers] Psst... ¿me traes {_es}?"),
        "en": (f"[playful] Find {_en}!", f"[excited] Bring me {_en}!", f"[whispers] Psst... can you find {_en}?"),
    }
    _TREASURE[f"treasure_again_{_key}"] = {"es": (f"[cheerful] ¡Busca {_es}!",), "en": (f"[cheerful] Find {_en}!",)}
    _TREASURE[f"treasure_found_{_key}"] = {
        "es": (f"[excited] ¡Sí! ¡{_es[:1].upper()}{_es[1:]}! [giggles] ¡Qué bien!", f"[gasps] ¡Encontraste {_es}! [proud] ¡Qué buen ojo!"),
        "en": (f"[excited] Yes! {_en[:1].upper()}{_en[1:]}! [giggles] Brilliant!", f"[gasps] You found {_en}! [proud] Great eyes!"),
    }  # fmt: skip

_SIMON: Final[dict[str, Takes]] = {
    "simon_start": {
        "es": (
            "[excited] ¡Vamos a jugar a Simón dice! [playful] Haz lo que yo diga... y no te muevas.",
            "[cheerful] ¡Simón dice que vamos a jugar!",
            "[playful] ¿Listos para copiarme? [excited] ¡Simón dice!",
        ),
        "en": (
            "[excited] Let's play Simon says! [playful] Do what I say... and hold still.",
            "[cheerful] Simon says it's time to play!",
            "[playful] Ready to copy me? [excited] Simon says!",
        ),
    },
    "simon_praise": {
        "es": ("[excited] ¡Muy bien!", "[cheerful] ¡Eso!", "[giggles] ¡Chévere!", "[proud] ¡Perfecto!", "[gasps] ¡Igualito!", "[playful] ¡Así mismo!", "[excited] ¡Qué bien lo haces!", "[giggles] ¡Me encanta!"),
        "en": ("[excited] Great!", "[cheerful] Yes!", "[giggles] Nice one!", "[proud] Perfect!", "[gasps] Just like that!", "[playful] Spot on!", "[excited] You're so good at this!", "[giggles] I love it!"),
    },
    "simon_like_this": {
        "es": ("[warm] ¡Así! Mira la pantalla.", "[playful] Mira el dibujo... ¡así se hace!", "[softly] Casi... ¡mira cómo es en la pantalla!"),
        "en": ("[warm] Like this! Look at the screen.", "[playful] Look at the picture... that's how!", "[softly] Almost... look at the screen!"),
    },
    "simon_step_back": {
        "es": ("[playful] ¡Da un paso hacia atrás, que quiero ver tus pies!", "[cheerful] ¡Un pasito para atrás!"),
        "en": ("[playful] Take a step back, I want to see your feet!", "[cheerful] One little step back!"),
    },
    "simon_come_closer": {
        "es": ("[cheerful] ¡Ahora acércate!", "[playful] ¡Ven, ven, acércate otra vez!"),
        "en": ("[cheerful] Now come closer!", "[playful] Come here, come closer again!"),
    },
}  # fmt: skip
for _key, _pose in POSES.items():
    _SIMON[f"simon_{_key}"] = {
        "es": (f"[playful] Simón dice... [excited] ¡{_pose.spanish}!", f"[whispers] Atención... [cheerful] Simón dice: ¡{_pose.spanish}!"),
        "en": (f"[playful] Simon says... [excited] {_pose.english}!", f"[whispers] Ready... [cheerful] Simon says: {_pose.english}!"),
    }  # fmt: skip

LINES: Final[dict[str, Takes]] = {**_SHARED, **_COLORS, **_TREASURE, **_SIMON}
