"""What Richie says in the kids' games (the color hunt, the treasure hunt, Simón dice, la gallina loca and the number race), recorded once.

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
from reachy_mini_conversation_app.games.number_race import NUMBER_WORDS
from reachy_mini_conversation_app.games.gallina_loca import FORFEITS, CHALLENGES
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

_GALLINA: Final[dict[str, Takes]] = {
    "gallina_start": {
        "es": (
            "[excited] ¡Soy la gallina loca! [playful] Yo digo tu nombre y algo loco que hacer. Lo haces rapidito... ¡y me tocas la cabeza o gritas listo! [whispers] Pero ojo... [excited] cuando yo grite ¡COCOROCÓ!, [giggles] ¡al que le toque, pone un huevo!",
            "[giggles] ¡Coc, coc! ¡Llegó la gallina loca! [playful] Les toca uno por uno: hacen la locura que les diga, me tocan la cabeza o dicen ¡listo!, y le toca al siguiente. [whispers] Y cuando grite ¡COCOROCÓ!... [giggles] ¡el que tenga la gallina pone un huevo!",
        ),
        "en": (
            "[excited] I'm the crazy hen! [playful] I say your name and something silly to do. Do it fast... then pat my head or shout ready! [whispers] But watch out... [excited] when I crow BAWK BAWK, [giggles] whoever's turn it is lays an egg!",
            "[giggles] Cluck, cluck! The crazy hen is here! [playful] One at a time: do the silly thing I say, pat my head or say ready, and it's the next one's turn. [whispers] And when I crow BAWK BAWK... [giggles] whoever has the hen lays an egg!",
        ),
    },
    "gallina_who": {
        "es": ("[cheerful] ¿Quién juega? [playful] ¡Díganme sus nombres, uno por uno!", "[curious] A ver... ¿cómo se llaman los que van a jugar?"),
        "en": ("[cheerful] Who's playing? [playful] Tell me your names, one at a time!", "[curious] Let's see... what are the players' names?"),
    },
    "gallina_more": {
        "es": ("[curious] ¿Quién más?", "[playful] ¿Alguien más juega?", "[cheerful] ¿Y quién más?"),
        "en": ("[curious] Who else?", "[playful] Anyone else playing?", "[cheerful] And who else?"),
    },
    "gallina_no_names": {
        "es": ("[playful] ¡No escuché nombres! [cheerful] Igual jugamos: cuando diga ¡siguiente!, le toca al que sigue en la rueda.", "[giggles] Bueno, ¡sin nombres! [playful] Cuando diga ¡siguiente!, le toca al de al lado."),
        "en": ("[playful] I didn't hear any names! [cheerful] We'll play anyway: when I say next, it's the next one in the circle.", "[giggles] Okay, no names! [playful] When I say next, it's the turn of the one beside you."),
    },
    "gallina_order": {
        "es": ("[cheerful] Vamos en este orden:", "[playful] Les toca así:"),
        "en": ("[cheerful] Here's the order:", "[playful] We go like this:"),
    },
    "gallina_ready": {
        "es": ("[excited] ¡Listo! [playful] ¡Que empiece la gallina loca!", "[giggles] ¡Coc, coc, coc! [excited] ¡A jugar!"),
        "en": ("[excited] All set! [playful] Let the crazy hen begin!", "[giggles] Cluck, cluck, cluck! [excited] Let's play!"),
    },
    "gallina_next": {
        "es": ("[excited] ¡Siguiente!", "[playful] ¡Le toca al siguiente!", "[giggles] ¡Al que sigue!"),
        "en": ("[excited] Next!", "[playful] Next one's turn!", "[giggles] Next in line!"),
    },
    "gallina_good": {
        "es": ("[excited] ¡Bien!", "[cheerful] ¡Eso!", "[giggles] ¡Qué gallina tan rápida!", "[proud] ¡Muy bien!", "[playful] ¡Chévere!", "[excited] ¡Así se hace!"),
        "en": ("[excited] Good!", "[cheerful] Yes!", "[giggles] What a speedy chicken!", "[proud] Well done!", "[playful] Nice!", "[excited] That's how you do it!"),
    },
    "gallina_slow": {
        "es": ("[playful] ¡Uy, la gallina no espera! [excited] ¡Sigue!", "[giggles] ¡Muy lento! ¡La gallina sigue!"),
        "en": ("[playful] Oops, the hen doesn't wait! [excited] Moving on!", "[giggles] Too slow! The hen moves on!"),
    },
    "gallina_hurry": {
        "es": ("[gasps] ¡La gallina se está poniendo loca!", "[whispers] Oigan... [excited] ¡la gallina está cacareando más rápido!", "[giggles] ¡Coc, coc, coc! ¡Rápido, rápido!"),
        "en": ("[gasps] The hen is getting crazy!", "[whispers] Listen... [excited] the hen is clucking faster!", "[giggles] Cluck, cluck, cluck! Quick, quick!"),
    },
    "gallina_boom": {
        "es": ("[excited] ¡COCOROCÓ! [giggles] ¡La gallina se volvió loca!", "[gasps] ¡COCOROCÓ! [excited] ¡Se acabó el tiempo!", "[excited] ¡COCOROCÓ, COCOROCÓ!"),
        "en": ("[excited] BAWK BAWK! [giggles] The hen went crazy!", "[gasps] BAWK! [excited] Time's up!", "[excited] BAWK, BAWK, BAWK!"),
    },
    "gallina_caught": {
        "es": ("[giggles] ¡Te toca poner un huevo!", "[playful] ¡La gallina te agarró! [giggles] A poner un huevo...", "[excited] ¡Tú pones el huevo!"),
        "en": ("[giggles] You have to lay an egg!", "[playful] The hen got you! [giggles] Time to lay an egg...", "[excited] You lay the egg!"),
    },
    "gallina_caught_anyone": {
        "es": ("[giggles] ¡El que tenía la gallina... pone un huevo!", "[playful] ¿A quién le tocaba? [giggles] ¡Pone un huevo!"),
        "en": ("[giggles] Whoever had the hen... lays an egg!", "[playful] Whose turn was it? [giggles] Lay an egg!"),
    },
    "gallina_laid": {
        "es": ("[excited] ¡Puso un huevo! [giggles] ¡Qué gallina tan ponedora!", "[giggles] ¡Jajaja! [excited] ¡Un huevo más!", "[cheerful] ¡Plop! [giggles] ¡Hasta yo me reí!"),
        "en": ("[excited] An egg! [giggles] What a great egg-layer!", "[giggles] Ha ha ha! [excited] One more egg!", "[cheerful] Plop! [giggles] Even I laughed!"),
    },
    "gallina_golden": {
        "es": ("[whispers] Y la gallina de oro... la que puso más huevos... [excited] es...", "[playful] ¿Quién puso más huevos? [excited] La gallina de oro es..."),
        "en": ("[whispers] And the golden hen... who laid the most eggs... [excited] is...", "[playful] Who laid the most eggs? [excited] The golden hen is..."),
    },
    "gallina_golden_tie": {
        "es": ("[gasps] ¡Empate de huevos! [excited] Las gallinas de oro son...", "[giggles] ¡Pusieron los mismos huevos! [excited] Gallinas de oro..."),
        "en": ("[gasps] It's an egg tie! [excited] The golden hens are...", "[giggles] Same number of eggs! [excited] Golden hens..."),
    },
    "gallina_golden_after": {
        "es": ("[giggles] ¡Cocorocó! [cheerful] ¡Qué ponedores!", "[excited] ¡Un aplauso para los huevos! [giggles] ¡Coc, coc!"),
        "en": ("[giggles] Bawk bawk! [cheerful] Champion egg-layers!", "[excited] A round of applause for the eggs! [giggles] Cluck, cluck!"),
    },
}  # fmt: skip


def _cried(words: str, language: str) -> str:
    """Return "¡Salta como rana y di ¡croac!" from "salta como rana y di ¡croac!" (one "!" at the end)."""
    capital = words[:1].upper() + words[1:]
    return ("¡" if language == "es" else "") + capital + ("" if capital.endswith("!") else "!")


for _key, _challenge in CHALLENGES.items():
    _es, _en = _challenge.spanish, _challenge.english
    if _challenge.move:  # he does the move right after
        _GALLINA[f"gallina_do_{_key}"] = {
            "es": ("[excited] ¡Copia a Richie! [playful] ¡Mira!", "[giggles] ¡Copia a Richie! ¡Así!"),
            "en": ("[excited] Copy Richie! [playful] Watch!", "[giggles] Copy Richie! Like this!"),
        }
        continue
    _GALLINA[f"gallina_do_{_key}"] = {
        "es": (f"[excited] {_cried(_es, 'es')}", f"[giggles] {_cried(_es, 'es')}"),
        "en": (f"[excited] {_cried(_en, 'en')}", f"[giggles] {_cried(_en, 'en')}"),
    }
for _key, _forfeit in FORFEITS.items():
    _es, _en = _forfeit.spanish, _forfeit.english
    _GALLINA[f"gallina_forfeit_{_key}"] = {
        "es": (f"[playful] ¡{_es[:1].upper() + _es[1:]}!",),
        "en": (f"[playful] {_en[:1].upper() + _en[1:]}!",),
    }

# La carrera de números: Tito (the kids' car) and Flecha (Richie's), from the story La gran carrera.
_RACE: Final[dict[str, Takes]] = {
    "race_start": {
        "es": (
            "[excited] ¡La carrera de números! [playful] Ustedes manejan a Tito, y yo manejo a Flecha. Me tocan la cabeza para girar la rueda... [cheerful] y cuentan las casillas en voz alta. [excited] ¡El primero que llegue al diez gana!",
            "[cheerful] ¡Tito y Flecha van a echar una carrera! [playful] Tito es de ustedes, Flecha es mío. Para girar la rueda, me tocan la cabeza... [excited] ¡y cuentan las casillas que avanza Tito! [cheerful] ¡A ver quién llega primero al diez!",
        ),
        "en": (
            "[excited] The number race! [playful] You drive Tito, and I drive Flecha. Pat my head to spin the wheel... [cheerful] and count the squares out loud. [excited] The first one to ten wins!",
            "[cheerful] Tito and Flecha are having a race! [playful] Tito is yours, Flecha is mine. To spin the wheel, pat my head... [excited] and count the squares Tito goes! [cheerful] Let's see who gets to ten first!",
        ),
    },
    "race_turn_first": {
        "es": ("[excited] ¡Arranca Tito! [playful] Tócame la cabeza para girar la rueda.", "[cheerful] ¡Empieza Tito! [playful] Tócame la cabeza... ¡y gira la rueda!"),
        "en": ("[excited] Tito goes first! [playful] Pat my head to spin the wheel.", "[cheerful] Tito starts! [playful] Pat my head... and spin the wheel!"),
    },
    "race_turn": {
        "es": ("[cheerful] ¡Le toca a Tito!", "[playful] ¡Tito! ¡Gira la rueda!", "[excited] ¡Te toca!", "[cheerful] ¡Turno de Tito!"),
        "en": ("[cheerful] Tito's turn!", "[playful] Tito! Spin the wheel!", "[excited] Your turn!", "[cheerful] Over to Tito!"),
    },
    "race_spin_nudge": {
        "es": ("[playful] Tócame la cabeza para girar la rueda.", "[whispers] Psst... [playful] ¡tócame la cabeza y la rueda gira!"),
        "en": ("[playful] Pat my head to spin the wheel.", "[whispers] Psst... [playful] pat my head and the wheel spins!"),
    },
    "race_count_1": {
        "es": ("[excited] ¡Uno! [cheerful] ¿A dónde llega Tito? ¡Cuenta!", "[cheerful] ¡Salió uno! [playful] Cuenta la casilla..."),
        "en": ("[excited] One! [cheerful] Where does Tito get to? Count!", "[cheerful] It's a one! [playful] Count the square..."),
    },
    "race_count_2": {
        "es": ("[excited] ¡Dos! [cheerful] A contar las casillas...", "[cheerful] ¡Salió dos! [playful] Cuenta las casillas de Tito..."),
        "en": ("[excited] Two! [cheerful] Count the squares...", "[cheerful] It's a two! [playful] Count Tito's squares..."),
    },
    "race_right": {
        "es": ("[excited] ¡Muy bien!", "[proud] ¡Eso! ¡Contaste muy bien!", "[cheerful] ¡Chévere!", "[excited] ¡Así se cuenta!"),
        "en": ("[excited] Well done!", "[proud] Yes! Great counting!", "[cheerful] Awesome!", "[excited] That's how you count!"),
    },
    "race_together": {
        "es": ("[warm] ¡Contemos juntos!", "[cheerful] ¡Vamos a contar juntos!", "[playful] Cuenta conmigo..."),
        "en": ("[warm] Let's count together!", "[cheerful] Let's count it together!", "[playful] Count with me..."),
    },
    "race_flecha_1": {
        "es": ("[playful] Ahora Flecha... [excited] ¡uno!", "[cheerful] ¡Turno de Flecha! [playful] Salió uno..."),
        "en": ("[playful] Now Flecha... [excited] one!", "[cheerful] Flecha's turn! [playful] It's a one..."),
    },
    "race_flecha_2": {
        "es": ("[playful] Ahora Flecha... [excited] ¡dos!", "[cheerful] ¡Turno de Flecha! [playful] Salió dos..."),
        "en": ("[playful] Now Flecha... [excited] two!", "[cheerful] Flecha's turn! [playful] It's a two..."),
    },
    "race_tito_wins": {
        "es": ("[excited] ¡Tito llegó al diez! [cheerful] ¡Ganaron! [giggles] Poquito a poquito se llega lejos.", "[gasps] ¡Diez! [excited] ¡Tito ganó la carrera! [proud] ¡Qué bien contaron!"),
        "en": ("[excited] Tito made it to ten! [cheerful] You won! [giggles] Little by little, you go a long way.", "[gasps] Ten! [excited] Tito won the race! [proud] What great counting!"),
    },
    "race_flecha_wins": {
        "es": ("[gasps] ¡Flecha llegó primero al diez! [playful] Pero Tito contó muy bien... [cheerful] ¡la próxima le ganamos!", "[giggles] ¡Flecha llegó al diez! [warm] Uy, por poquito. [cheerful] ¡La próxima gana Tito!"),
        "en": ("[gasps] Flecha got to ten first! [playful] But Tito counted so well... [cheerful] next time we'll beat him!", "[giggles] Flecha made it to ten! [warm] Ooh, so close. [cheerful] Next time Tito wins!"),
    },
}  # fmt: skip
for _n, (_es, _en) in enumerate(zip(NUMBER_WORDS["es"], NUMBER_WORDS["en"]), start=1):
    _RACE[f"num_{_n}"] = {  # said one after another while counting ("cinco... seis"), so each is its own clip
        "es": (f"[cheerful] ¡{_es.capitalize()}!", f"[playful] {_es.capitalize()}..."),
        "en": (f"[cheerful] {_en.capitalize()}!", f"[playful] {_en.capitalize()}..."),
    }

LINES: Final[dict[str, Takes]] = {**_SHARED, **_COLORS, **_TREASURE, **_SIMON, **_GALLINA, **_RACE}
