"""What Ballpark's host says at fixed moments, in English and Venezuelan Spanish (docs/features/ballpark.md).

Ballpark's Richie is a game-show host who loves a bold guess: he teases the cowards with huge ranges, cheers the
brave ones with tiny ranges, and builds every answer up with suspense, always warm and kid-safe. Each moment has
several takes so he rarely repeats himself. Lines carry ElevenLabs audio tags in square brackets ("[playful]",
"[pause]"), from Big Brain's list (``TAGS``); ``shown`` strips them for the e-ink screen and the phones. Spanish
is Venezuelan and speaks to the room as "ustedes". Clips are made once by
``scripts/big_brain_voice.py --set ballpark`` into ``game-data/ballpark/voice/<moment>/<language>-NN.wav``.
Questions, answers, facts, team names and ranges are said live in the same host voice, so a take that follows
them (``widest`` after "Rojo: de 10 a 5.000.", ``near_miss`` after "¡Equipo azul!") must work after any of them,
and a take after names may follow one team or several.
"""

from typing import Final

from reachy_mini_conversation_app.games.big_brain_lines import TAGS, TEAMS, shown


__all__ = ["LINES", "TAGS", "TEAMS", "shown"]

_Lines = dict[str, tuple[str, ...]]

_MOMENTS: Final[dict[str, _Lines]] = {
    # The lobby: the game opens, the room joins, someone taps Start.
    "welcome": {
        "en": (
            "[booming] Welcome to BALLPARK! [playful] Where you don't need to know the answer... [pause] you just "
            "need to know roughly. Scan the code on the screen to join!",
            "[booming] Ladies and gentlemen... [excited] it's BALLPARK! [playful] The game where close enough is "
            "good enough. Scan the code on the screen to play.",
            "[clears throat] [booming] Live from the living room... it's BALLPARK! [playful] Nobody knows the exact "
            "number. Let's see who knows roughly. Scan the code on the screen to join!",
        ),
        "es": (
            "[booming] ¡Bienvenidos a BALLPARK! [playful] Aquí no hace falta saberse la respuesta... [pause] basta "
            "con saberla más o menos. ¡Escaneen el código de la pantalla para jugar!",
            "[booming] Damas y caballeros... [excited] ¡llegó BALLPARK! [playful] El juego donde más o menos es "
            "suficiente. Escaneen el código de la pantalla para entrar.",
            "[clears throat] [booming] Desde la sala de la casa... ¡esto es BALLPARK! [playful] Nadie sabe el número "
            "exacto, pero vamos a ver quién lo sabe más o menos. ¡Escaneen el código de la pantalla!",
        ),
    },
    "waiting": {
        "en": (
            "[warmly] When everyone's in, tap Start on your phone.",
            "[playful] I'll wait... [pause] roughly. When everyone's in, tap Start on your phone.",
            "[curious] Is everybody here? [warmly] When you're all in, tap Start on your phone.",
        ),
        "es": (
            "[warmly] Cuando estén todos, toquen Empezar en el teléfono.",
            "[playful] Yo espero... [pause] más o menos. Cuando estén todos, toquen Empezar en el teléfono.",
            "[curious] ¿Llegaron todos? [warmly] Cuando estén listos, toquen Empezar en el teléfono.",
        ),
    },
    "begin": {
        "en": (
            "[excited] Here we go! [warmly] Every answer is a number, but don't guess it: write a range, from "
            "this much to that much, where you're sure it is. [playful] If the answer's in your range, one point. "
            "The smallest range that gets it, three points! [pause] And if everybody gets it, the widest range "
            "gets nothing. First team to fifteen wins!",
            "[booming] Let's play! [warmly] I ask, the answer's a number, and you write a range you're sure it's "
            "in. [playful] Right range, one point. The tightest right range, three! [pause] But if you're all "
            "right, the widest gets zero, so no cheating with zero to a million. First to fifteen wins!",
        ),
        "es": (
            "[excited] ¡Arrancamos! [warmly] Cada respuesta es un número, pero no lo adivinen: escriban un rango, "
            "de tanto a tanto, donde estén seguros de que está. [playful] Si la respuesta cae en su rango, un punto. "
            "¡El rango más pequeño que acierte, tres puntos! [pause] Y si todos aciertan, el más ancho no gana nada. "
            "¡El primer equipo en llegar a quince, gana!",
            "[booming] ¡A jugar! [warmly] Yo pregunto, la respuesta es un número, y ustedes escriben un rango donde "
            "estén seguros de que está. [playful] Rango correcto, un punto. ¡El más ajustado que acierte, tres! "
            "[pause] Pero si todos aciertan, el más ancho saca cero, así que nada de poner de cero a un millón. "
            "¡El primero en llegar a quince, gana!",
        ),
    },
    "powers_rules": {
        "en": (
            "[playful] And each team has three powers, once per game, used in secret on the phone. [pause] Copy: "
            "take another team's range. Swap: two other teams trade ranges. And Double: your points count twice. "
            "[excited] Use them wisely!",
            "[curious] One more thing... [playful] every team gets three secret powers, once each. Copy steals "
            "another team's range, Swap makes two other teams trade, and Double doubles your points. [pause] "
            "Choose them on the phone!",
        ),
        "es": (
            "[playful] Y cada equipo tiene tres poderes, una vez por juego, que se usan en secreto en el teléfono. "
            "[pause] Copia: se llevan el rango de otro equipo. Cambio: dos equipos se intercambian los rangos. Y "
            "Doble: sus puntos valen el doble. [excited] ¡Úsenlos bien!",
            "[curious] Una cosa más... [playful] cada equipo tiene tres poderes secretos, una vez cada uno. Copia "
            "les roba el rango a otros, Cambio hace que otros dos equipos se intercambien, y Doble duplica sus "
            "puntos. [pause] ¡Se escogen en el teléfono!",
        ),
    },
    "powers_rules_two": {
        "en": (
            "[playful] And each team has two powers, once per game, used in secret on the phone. [pause] Copy: "
            "take the other team's range. And Double: your points count twice. [excited] Use them wisely!",
            "[curious] One more thing... [playful] each team gets two secret powers, once each. Copy steals the "
            "other team's range, and Double doubles your points. [pause] Choose them on the phone!",
        ),
        "es": (
            "[playful] Y cada equipo tiene dos poderes, una vez por juego, que se usan en secreto en el teléfono. "
            "[pause] Copia: se llevan el rango del otro equipo. Y Doble: sus puntos valen el doble. [excited] "
            "¡Úsenlos bien!",
            "[curious] Una cosa más... [playful] cada equipo tiene dos poderes secretos, una vez cada uno. Copia les "
            "roba el rango a los otros, y Doble duplica sus puntos. [pause] ¡Se escogen en el teléfono!",
        ),
    },
    "double_rules": {
        "en": (
            "[playful] And you have one power, once per game: Double, so a question's points count twice. "
            "[pause] It's on the phone.",
            "[curious] One more thing... [playful] once this game you can play Double, and that question's points "
            "count twice. Use it when you feel sure!",
        ),
        "es": (
            "[playful] Y tienen un poder, una vez por juego: Doble, para que los puntos de una pregunta valgan el "
            "doble. [pause] Está en el teléfono.",
            "[curious] Una cosa más... [playful] una vez en el juego pueden usar Doble, y esa pregunta vale el "
            "doble. ¡Úsenlo cuando estén seguros!",
        ),
    },
    # A question opens: the surprise, the lead-in, typing the range.
    "surprise": {
        "en": (
            "[gasps] Wait! [dramatic] This is the SURPRISE question!",
            "[dramatic] Oh-ho! [booming] It's the surprise question!",
            "[excited] Stop everything! [dramatic] This one's the surprise!",
        ),
        "es": (
            "[gasps] ¡Esperen! [dramatic] ¡Esta es la pregunta SORPRESA!",
            "[dramatic] ¡Epa! [booming] ¡Llegó la pregunta sorpresa!",
            "[excited] ¡Paren todo! [dramatic] ¡Esta es la sorpresa!",
        ),
    },
    "surprise_nail": {
        "en": (
            "[playful] Nail it: write the exact number, from it to itself, and if you're right... [booming] five "
            "points!",
            "[playful] If you dare to write the exact number and you're right... [booming] five points! [pause] "
            "Ranges still count as usual.",
        ),
        "es": (
            "[playful] En el clavo: escriban el número exacto, de él mismo a él mismo, y si aciertan... [booming] "
            "¡cinco puntos!",
            "[playful] Si se atreven a poner el número exacto y aciertan... [booming] ¡cinco puntos! [pause] Los "
            "rangos cuentan como siempre.",
        ),
    },
    "surprise_double": {
        "en": (
            "[booming] Double points for everybody! [playful] Every point on this question counts twice.",
            "[excited] This question is worth double, for every team! [playful] Make it count.",
        ),
        "es": (
            "[booming] ¡Puntos dobles para todos! [playful] Cada punto de esta pregunta vale por dos.",
            "[excited] ¡Esta pregunta vale el doble, para todos los equipos! [playful] Aprovéchenla.",
        ),
    },
    "asked": {
        "en": (
            "[curious] Here it is...",
            "[playful] Listen carefully...",
            "[thoughtful] Let's see...",
            "[curious] Tell me...",
        ),
        "es": ("[curious] Ahí va...", "[playful] Escuchen bien...", "[thoughtful] A ver...", "[curious] Díganme..."),
    },
    "type_first": {
        "en": (
            "[warmly] On your phone, write the lowest and the highest number you think it could be, then tap Send. "
            "[playful] You have a minute!",
            "[warmly] Type your range on the phone: from, and to. [playful] One minute, and you can change it until "
            "time's up.",
        ),
        "es": (
            "[warmly] En el teléfono, escriban el número más bajo y el más alto que creen que puede ser, y toquen "
            "Enviar. [playful] ¡Tienen un minuto!",
            "[warmly] Escriban su rango en el teléfono: desde y hasta. [playful] Un minuto, y lo pueden cambiar "
            "hasta que se acabe el tiempo.",
        ),
    },
    "type_it": {
        "en": (
            "[playful] Ranges, please!",
            "[warmly] Write your range!",
            "[playful] One minute. Go!",
            "[excited] Phones out!",
        ),
        "es": (
            "[playful] ¡Rangos, por favor!",
            "[warmly] ¡Escriban su rango!",
            "[playful] Un minuto. ¡Ya!",
            "[excited] ¡A los teléfonos!",
        ),
    },
    "hurry": {
        "en": (
            "[snappy] Fifteen seconds!",
            "[excited] Fifteen seconds left!",
            "[playful] Hurry, hurry! Fifteen seconds!",
        ),
        "es": (
            "[snappy] ¡Quince segundos!",
            "[excited] ¡Quedan quince segundos!",
            "[playful] ¡Apúrense! ¡Quince segundos!",
        ),
    },
    "all_sent": {
        "en": ("[excited] Everybody's in! Let's see those ranges.", "[playful] All sent! [pause] Let's have a look."),
        "es": (
            "[excited] ¡Todos mandaron! Veamos esos rangos.",
            "[playful] ¡Listo todo el mundo! [pause] Vamos a ver.",
        ),
    },
    "time_up": {
        "en": ("[snappy] Time's up! [playful] Let's see those ranges.", "[booming] Time! [pause] Let's have a look."),
        "es": (
            "[snappy] ¡Se acabó el tiempo! [playful] Veamos esos rangos.",
            "[booming] ¡Tiempo! [pause] Vamos a ver.",
        ),
    },
    "nobody_sent": {
        "en": (
            "[disbelief] Nobody sent anything? [sighs] I'll tell you the answer anyway.",
            "[mock disappointment] Not a single range... [playful] fine, here's the answer.",
        ),
        "es": (
            "[disbelief] ¿Nadie mandó nada? [sighs] Bueno, igual les digo la respuesta.",
            "[mock disappointment] Ni un solo rango... [playful] está bien, ahí va la respuesta.",
        ),
    },
    # The powers, the ranges, the answer.
    "powers_used": {
        "en": (
            "[dramatic] Hold on! [pause] Somebody used a power...",
            "[gasps] Ooh! [playful] Powers have been played...",
            "[hushed] Wait, wait... [dramatic] there's a power in play!",
        ),
        "es": (
            "[dramatic] ¡Un momento! [pause] Alguien usó un poder...",
            "[gasps] ¡Uy! [playful] Hay poderes en juego...",
            "[hushed] Esperen, esperen... [dramatic] ¡alguien usó un poder!",
        ),
    },
    "alone": {
        "en": ("[thoughtful] Brave, or careful? [pause] We'll see.", "[playful] Let's see if it holds."),
        "es": ("[thoughtful] ¿Valientes o prudentes? [pause] Ya veremos.", "[playful] A ver si aguanta."),
    },
    "same_size": {
        "en": (
            "[curious] All the same size! [playful] How about that.",
            "[amazed] Identical sizes! [pause] Suspicious.",
        ),
        "es": (
            "[curious] ¡Todos del mismo tamaño! [playful] Mira tú.",
            "[amazed] ¡Tamaños idénticos! [pause] Sospechoso.",
        ),
    },
    "widest": {
        "en": (
            "[playful] That's not a range, that's a whole neighborhood!",
            "[chuckles] Playing it safe, I see.",
            "[mock disappointment] So brave. [pause] So very brave.",
            "[playful] You could park a bus in that range.",
        ),
        "es": (
            "[playful] ¡Eso no es un rango, eso es una urbanización completa!",
            "[chuckles] Jugando a lo seguro, ya veo.",
            "[mock disappointment] Qué valentía. [pause] Qué valentía tan grande.",
            "[playful] En ese rango cabe un autobús estacionado.",
        ),
    },
    "widest_huge": {
        "en": (
            "[laughs] Wow! [playful] Why not just write every number?",
            "[disbelief] Seriously? [chuckles] That range has its own postcode.",
            "[laughs] That's not confidence, that's a safety net the size of a stadium!",
        ),
        "es": (
            "[laughs] ¡Guao! [playful] ¿Por qué no ponen todos los números de una vez?",
            "[disbelief] ¿En serio? [chuckles] Ese rango tiene su propio código postal.",
            "[laughs] ¡Eso no es confianza, eso es una red del tamaño de un estadio!",
        ),
    },
    "narrowest": {
        "en": (
            "[amazed] Now THAT's confidence!",
            "[proud] Tight! [playful] I like it.",
            "[excited] Brave! [pause] Very brave.",
        ),
        "es": (
            "[amazed] ¡Eso sí es tener confianza!",
            "[proud] ¡Ajustadito! [playful] Me gusta.",
            "[excited] ¡Valientes! [pause] Muy valientes.",
        ),
    },
    "exact_bet": {
        "en": (
            "[gasps] One single number! [dramatic] All or nothing.",
            "[amazed] An exact number! [playful] Wow, wow.",
        ),
        "es": ("[gasps] ¡Un solo número! [dramatic] Todo o nada.", "[amazed] ¡Un número exacto! [playful] Ay, mamá."),
    },
    "suspense": {
        "en": (
            "[dramatic] And the answer is...",
            "[hushed] The real number... [dramatic] is...",
            "[drawn out] Aaaand... [dramatic] the answer is...",
        ),
        "es": (
            "[dramatic] Y la respuesta es...",
            "[hushed] El número de verdad... [dramatic] es...",
            "[drawn out] Yyyy... [dramatic] la respuesta es...",
        ),
    },
    # What the answer did.
    "bullseye": {
        "en": (
            "[booming] BULLSEYE! [amazed] The exact number!",
            "[gasps] [amazed] Spot on! The exact number! [laughs] Did you cheat?",
        ),
        "es": (
            "[booming] ¡EN EL CLAVO! [amazed] ¡El número exacto!",
            "[gasps] [amazed] ¡Exactito! ¡El número exacto! [laughs] ¿Hicieron trampa?",
        ),
    },
    "best_tie": {
        "en": (
            "[excited] A tie for the tightest range! [playful] Big points for both.",
            "[amazed] Same tightest range! [pause] Everybody there takes the big points.",
        ),
        "es": (
            "[excited] ¡Empate en el rango más ajustado! [playful] Puntos gordos para todos ellos.",
            "[amazed] ¡El mismo rango más ajustado! [pause] Todos los que estaban ahí se llevan los puntos gordos.",
        ),
    },
    "nobody_right": {
        "en": (
            "[mock disappointment] Nobody got it! [playful] Not one range.",
            "[laughs] Everybody missed! [pause] Zero points all round.",
            "[disbelief] Nobody? [sighs] Not a single one.",
        ),
        "es": (
            "[mock disappointment] ¡Nadie le atinó! [playful] Ni un solo rango.",
            "[laughs] ¡Todos fallaron! [pause] Cero puntos para todo el mundo.",
            "[disbelief] ¿Nadie? [sighs] Ni uno solo.",
        ),
    },
    "everyone_right": {
        "en": (
            "[excited] Everybody got it! [playful] But you know the rule: the widest range gets nothing...",
            "[amazed] All of you, right! [pause] Which means the widest range goes home empty...",
        ),
        "es": (
            "[excited] ¡Todos acertaron! [playful] Pero ya saben la regla: el rango más ancho no gana nada...",
            "[amazed] ¡Todos le atinaron! [pause] O sea que el rango más ancho se va con las manos vacías...",
        ),
    },
    "widest_zero": {
        "en": ("[mock disappointment] Zero for you. [playful] Too safe!", "[chuckles] Sorry! [pause] Zero points."),
        "es": (
            "[mock disappointment] Cero para ustedes. [playful] ¡Por miedosos!",
            "[chuckles] ¡Lo siento! [pause] Cero puntos.",
        ),
    },
    "copy_paid": {
        "en": ("[playful] Copying paid off!", "[chuckles] Sneaky... and it worked!"),
        "es": ("[playful] ¡Copiarse salió bien!", "[chuckles] Pícaros... ¡y les funcionó!"),
    },
    "copy_flop": {
        "en": ("[laughs] You copied the wrong team!", "[mock disappointment] Copying didn't help this time."),
        "es": ("[laughs] ¡Se copiaron del equipo equivocado!", "[mock disappointment] Copiarse no sirvió esta vez."),
    },
    "double_paid": {
        "en": (
            "[booming] The double paid off! [excited] Twice the points!",
            "[proud] Double, and right! Well played.",
        ),
        "es": (
            "[booming] ¡El doble funcionó! [excited] ¡Puntos dobles!",
            "[proud] ¡Doble, y le atinaron! Bien jugado.",
        ),
    },
    "double_flop": {
        "en": ("[mock disappointment] Double... of nothing.", "[laughs] Twice zero is still zero!"),
        "es": ("[mock disappointment] El doble... de nada.", "[laughs] ¡Dos veces cero sigue siendo cero!"),
    },
    "near_miss": {
        "en": (
            "[gasps] So close! [pause] Just outside.",
            "[sighs] Oh, by a hair!",
            "[mock disappointment] Nearly! Just missed it.",
        ),
        "es": (
            "[gasps] ¡Casi! [pause] Se quedaron afuera por poquito.",
            "[sighs] ¡Ay, por un pelito!",
            "[mock disappointment] ¡Cerquita! Por nada.",
        ),
    },
    "way_off": {
        "en": ("[laughs] You were in a different galaxy!", "[disbelief] Not even close! [chuckles] Not even nearby."),
        "es": (
            "[laughs] ¡Ustedes estaban en otra galaxia!",
            "[disbelief] ¡Ni cerca! [chuckles] Ni en el mismo barrio.",
        ),
    },
    "double_all_paid": {
        "en": ("[excited] And remember: double points on this one!", "[playful] Doubled, as promised!"),
        "es": ("[excited] Y recuerden: ¡esta valía el doble!", "[playful] ¡Doblados, como prometí!"),
    },
    # Between questions and at the end.
    "match_point": {
        "en": (
            "[dramatic] One step from winning!",
            "[excited] So close to fifteen!",
            "[hushed] [dramatic] Almost there...",
        ),
        "es": (
            "[dramatic] ¡A un paso de ganar!",
            "[excited] ¡Cerquita de los quince!",
            "[hushed] [dramatic] Ya casi...",
        ),
    },
    "lead_change": {
        "en": ("[excited] We have a new leader!", "[gasps] The lead has changed hands!"),
        "es": ("[excited] ¡Tenemos nuevo líder!", "[gasps] ¡Cambió el primer lugar!"),
    },
    "runaway": {
        "en": (
            "[playful] Somebody's running away with this!",
            "[amazed] What a lead! [pause] Can anybody catch them?",
        ),
        "es": ("[playful] ¡Alguien se está escapando!", "[amazed] ¡Qué ventaja! [pause] ¿Alguien los alcanza?"),
    },
    "winner": {
        "en": ("[dramatic] And the winner of Ballpark is...", "[booming] We have a winner! It's..."),
        "es": ("[dramatic] Y el ganador de Ballpark es...", "[booming] ¡Tenemos ganador! Es..."),
    },
    "tie": {
        "en": ("[amazed] It's a tie! [excited] The winners are...", "[gasps] A tie! [booming] Champions..."),
        "es": ("[amazed] ¡Es un empate! [excited] Ganan...", "[gasps] ¡Empate! [booming] Los campeones..."),
    },
    "goodbye": {
        "en": (
            "[warmly] Thanks for playing Ballpark! [playful] Remember: you don't have to be right... [pause] just "
            "roughly right.",
            "[warmly] That's the game! [playful] Thanks for playing, and see you next time, more or less.",
        ),
        "es": (
            "[warmly] ¡Gracias por jugar Ballpark! [playful] Recuerden: no hay que tener la razón... [pause] basta "
            "con tenerla más o menos.",
            "[warmly] ¡Y eso fue todo! [playful] Gracias por jugar, y nos vemos la próxima, más o menos.",
        ),
    },
}


def _team_lines() -> dict[str, _Lines]:
    """Return the lines that name a team: the smallest right range, and a win that crowns it."""
    lines: dict[str, _Lines] = {}
    for key, (en, es) in TEAMS.items():
        lines[f"best_{key}"] = {
            "en": (
                f"[excited] The tightest range is {en} team's! [booming] Big points!",
                f"[proud] {en.capitalize()} team, the smallest range that got it! [excited] Well done!",
                f"[booming] {en.capitalize()} team takes the big points!",
            ),
            "es": (
                f"[excited] ¡El rango más ajustado es del equipo {es}! [booming] ¡Puntos gordos!",
                f"[proud] ¡Equipo {es}, el rango más pequeño que acertó! [excited] ¡Bien hecho!",
                f"[booming] ¡El equipo {es} se lleva los puntos gordos!",
            ),
        }
        lines[f"winner_{key}"] = {
            "en": (f"[booming] The {en.upper()} TEAM!", f"[excited] Team {en}! [booming] The kings of roughly!"),
            "es": (
                f"[booming] ¡El EQUIPO {es.upper()}!",
                f"[excited] ¡Equipo {es}! [booming] ¡Los reyes del más o menos!",
            ),
        }
    return lines


# Every moment's takes, by language.
LINES: Final[dict[str, _Lines]] = {**_MOMENTS, **_team_lines()}
