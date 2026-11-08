"""What Big Brain's host says at fixed moments, in English and Venezuelan Spanish (docs/plans/party-games.md).

Each moment has several takes so Richie rarely repeats himself. Lines carry ElevenLabs audio tags in square
brackets ("[booming]", "[pause]") that shape how the host voice reads them; `shown` strips them for the e-ink
screen and the phones. Clips are made once with Eleven v4 Turbo (scripts/big_brain_voice.py) and played by
moment. Lines that need a player's name or a clue are spoken live instead.
"""

import re
from typing import Final


# Audio tags a line may use. Free-form tags work too, but a fixed list keeps the host in character and lets the
# tests catch typos, which ElevenLabs would read aloud.
TAGS: Final[frozenset[str]] = frozenset(
    {
        "booming",
        "excited",
        "playful",
        "amazed",
        "proud",
        "curious",
        "thoughtful",
        "warmly",
        "dramatic",
        "mock disappointment",
        "disbelief",
        "whispers",
        "hushed",
        "softly",
        "shouts",
        "pause",
        "long pause",
        "drawn out",
        "snappy",
        "slowly",
        "laughs",
        "chuckles",
        "sighs",
        "gasps",
        "clears throat",
    }
)

# Team colors, as the lights and the phones show them.
TEAMS: Final[dict[str, tuple[str, str]]] = {
    "red": ("red", "rojo"),
    "blue": ("blue", "azul"),
    "green": ("green", "verde"),
    "yellow": ("yellow", "amarillo"),
}

_Lines = dict[str, tuple[str, ...]]

_MOMENTS: Final[dict[str, _Lines]] = {
    "welcome": {
        "en": (
            "[booming] Good evening, and welcome... [pause] to BIG BRAIN! [playful] The only quiz show hosted by a "
            "robot with antennas.",
            "[booming] Ladies and gentlemen, boys and girls... [dramatic] it's time for BIG BRAIN!",
            "[booming] Welcome, welcome, welcome to BIG BRAIN! [playful] Tonight, someone leaves this living room a "
            "genius.",
            "[clears throat] [booming] From the living room, live and in person... [pause] this is BIG BRAIN!",
            "[booming] Brains on, phones out... [excited] it's BIG BRAIN time!",
            "[booming] Welcome to BIG BRAIN! [playful] Where the questions are hard and the host is... [pause] "
            "made of plastic.",
        ),
        "es": (
            "[booming] Muy buenas noches, y bienvenidos... [pause] ¡a BIG BRAIN! [playful] El único concurso "
            "presentado por un robot con antenas.",
            "[booming] Damas y caballeros, niños y niñas... [dramatic] ¡llegó la hora de BIG BRAIN!",
            "[booming] ¡Bienvenidos, bienvenidos, bienvenidos a BIG BRAIN! [playful] Esta noche alguien sale de "
            "esta sala hecho un cráneo.",
            "[clears throat] [booming] Desde la sala de la casa, en vivo y en directo... [pause] ¡esto es BIG BRAIN!",
            "[booming] Cerebro prendido, teléfono en mano... [excited] ¡es hora de BIG BRAIN!",
            "[booming] ¡Bienvenidos a BIG BRAIN! [playful] Donde las preguntas son difíciles y el animador es... "
            "[pause] de plástico.",
        ),
    },
    "join": {
        "en": (
            "[warmly] Scan the code on the screen, type your name, and pick a team. [playful] Your phone is now a "
            "buzzer. Use it wisely.",
            "[warmly] To play, scan the code on the screen with your phone, then choose your team.",
            "[playful] Grab your phone, scan the code, join a team... [pause] and get ready to buzz.",
        ),
        "es": (
            "[warmly] Escaneen el código de la pantalla, escriban su nombre y escojan un equipo. [playful] Su "
            "teléfono ahora es un pulsador. Úsenlo con cabeza.",
            "[warmly] Para jugar, escaneen con el teléfono el código de la pantalla y escojan su equipo.",
            "[playful] Agarren el teléfono, escaneen el código, únanse a un equipo... [pause] y prepárense para "
            "pulsar.",
        ),
    },
    "waiting": {
        "en": (
            "[playful] Who else is joining? [pause] Don't be shy.",
            "[curious] Anyone else? There's room for one more brain.",
            "[playful] Still waiting on a few brains to warm up...",
            "[warmly] Take your time. [playful] The questions aren't going anywhere.",
            "[playful] The more brains, the merrier!",
            "[curious] Is that everyone? [pause] Last call for brains!",
        ),
        "es": (
            "[playful] ¿Quién más se une? [pause] No sean tímidos.",
            "[curious] ¿Alguien más? Aquí cabe otro cerebro.",
            "[playful] Todavía hay cerebros calentando motores...",
            "[warmly] Tranquilos, con calma. [playful] Las preguntas no se van a ningún lado.",
            "[playful] ¡Mientras más cerebros, mejor!",
            "[curious] ¿Ya están todos? [pause] ¡Última llamada para los cerebros!",
        ),
    },
    "begin": {
        "en": (
            "[excited] All right, everybody's in. [booming] Let's play!",
            "[booming] Teams are set... [pause] let the game begin!",
            "[excited] We have our players! [booming] Here we go!",
            "[playful] Phones ready? Brains ready? [booming] Let's do this!",
            "[booming] Brains warmed up? [excited] Then let's play BIG BRAIN!",
            "[playful] Everybody's here, nobody's ready... [booming] perfect! Let's go!",
        ),
        "es": (
            "[excited] Muy bien, ya estamos todos. [booming] ¡A jugar!",
            "[booming] Los equipos están listos... [pause] ¡que empiece el juego!",
            "[excited] ¡Ya tenemos jugadores! [booming] ¡Arrancamos!",
            "[playful] ¿Teléfonos listos? ¿Cerebros listos? [booming] ¡Vamos con todo!",
            "[booming] ¿Cerebros calentados? [excited] ¡Entonces a jugar BIG BRAIN!",
            "[playful] Ya llegaron todos, nadie está listo... [booming] ¡perfecto! ¡Vámonos!",
        ),
    },
    "board": {
        "en": (
            "[booming] Here's your board! [pause] Five categories, twenty clues.",
            "[curious] Let's see what's on the board tonight...",
            "[booming] Feast your eyes on tonight's categories!",
            "[playful] The board is set. [pause] Choose wisely.",
        ),
        "es": (
            "[booming] ¡Aquí está el tablero! [pause] Cinco categorías, veinte pistas.",
            "[curious] A ver qué tenemos hoy en el tablero...",
            "[booming] ¡Miren nada más las categorías de esta noche!",
            "[playful] El tablero está listo. [pause] Escojan con cuidado.",
        ),
    },
    "pick": {
        "en": (
            "[warmly] Pick a square!",
            "[curious] Where to next?",
            "[playful] What'll it be?",
            "[warmly] Choose your next clue.",
            "[curious] Which one are we going for?",
            "[playful] The board is yours. [pause] Pick one.",
            "[warmly] Go ahead, pick a square on your phone.",
            "[playful] Category and points, please.",
            "[playful] Choose wisely... [pause] or don't. It's more fun for me.",
            "[curious] Which square is calling your name?",
            "[warmly] Your turn to pick. [playful] No pressure.",
            "[playful] Big points or safe points? [pause] Your call.",
        ),
        "es": (
            "[warmly] ¡Escojan una casilla!",
            "[curious] ¿Para dónde vamos ahora?",
            "[playful] ¿Qué va a ser?",
            "[warmly] Escojan la próxima pista.",
            "[curious] ¿Cuál nos toca?",
            "[playful] El tablero es suyo. [pause] Escojan una.",
            "[warmly] Dale, escoge una casilla en tu teléfono.",
            "[playful] Categoría y puntos, por favor.",
            "[playful] Escojan bien... [pause] o no. Así me divierto más.",
            "[curious] ¿Qué casilla los está llamando?",
            "[warmly] Les toca escoger. [playful] Sin presión.",
            "[playful] ¿Muchos puntos o a la segura? [pause] Ustedes deciden.",
        ),
    },
    "answer": {
        "en": (
            "[snappy] Your answer?",
            "[curious] Go ahead!",
            "[snappy] Yes?",
            "[curious] What do you say?",
            "[playful] The floor is yours.",
            "[snappy] Let's hear it!",
            "[snappy] Talk to me!",
            "[playful] Hit me!",
            "[curious] Well?",
            "[snappy] Quick, what is it?",
        ),
        "es": (
            "[snappy] ¿Tu respuesta?",
            "[curious] ¡Dale!",
            "[snappy] ¿Sí?",
            "[curious] ¿Qué dices?",
            "[playful] Tienes la palabra.",
            "[snappy] ¡A ver!",
            "[snappy] ¡Dime!",
            "[playful] ¡Suéltalo!",
            "[curious] ¿Y bien?",
            "[snappy] Rápido, ¿qué es?",
        ),
    },
    "correct": {
        "en": (
            "[excited] Correct!",
            "[excited] That's right!",
            "[booming] Yes! Absolutely right!",
            "[proud] Correct! [pause] Big brain energy.",
            "[excited] You got it!",
            "[booming] Ding ding ding! Correct!",
            "[amazed] Correct! [playful] Somebody's been reading.",
            "[excited] Yes, yes, YES!",
            "[proud] Nailed it.",
            "[booming] That is... [pause] CORRECT!",
            "[excited] Spot on!",
            "[playful] Correct! [chuckles] Show-off.",
            "[booming] Correct! [playful] Give that brain a raise.",
            "[excited] Bingo! [pause] Correct!",
            "[amazed] Correct! [whispers] I'm a little jealous.",
            "[playful] Correct! [whispers] Were you peeking at my notes?",
            "[snappy] Correct! [chuckles] Too easy?",
            "[excited] Right on the money!",
            "[warmly] Correct! [pause] Beautiful.",
            "[booming] Correct! [playful] That brain is BIG tonight.",
        ),
        "es": (
            "[excited] ¡Correcto!",
            "[excited] ¡Eso es!",
            "[booming] ¡Sí, señor! ¡Correctísimo!",
            "[proud] ¡Correcto! [pause] Qué cráneo.",
            "[excited] ¡Lo tienes!",
            "[booming] ¡Tin tin tin! ¡Correcto!",
            "[amazed] ¡Correcto! [playful] Alguien ha estado leyendo.",
            "[excited] ¡Sí, sí, SÍ!",
            "[proud] Clavadito.",
            "[booming] Eso es... [pause] ¡CORRECTO!",
            "[excited] ¡Exacto!",
            "[playful] ¡Correcto! [chuckles] Qué sobrado.",
            "[booming] ¡Correcto! [playful] Denle un aumento a ese cerebro.",
            "[excited] ¡Bingo! [pause] ¡Correcto!",
            "[amazed] ¡Correcto! [whispers] Me da hasta envidia.",
            "[playful] ¡Correcto! [whispers] ¿Estabas viendo mis apuntes?",
            "[snappy] ¡Correcto! [chuckles] ¿Muy fácil?",
            "[excited] ¡Correcto! [playful] ¡Qué nota!",
            "[warmly] ¡Correcto! [pause] Qué belleza.",
            "[booming] ¡Correcto! [playful] Ese cerebro está burda de grande hoy.",
        ),
    },
    "correct_streak": {
        "en": (
            "[amazed] Another one! [playful] Somebody stop them!",
            "[excited] Correct again! [booming] They're on fire!",
            "[amazed] That's three in a row! [playful] Is anyone else playing?",
            "[proud] Correct! [playful] This is turning into a one-team show.",
            "[excited] And again! [chuckles] Unstoppable.",
            "[amazed] Correct! [playful] I'm starting to think they read my memory.",
            "[amazed] Again?! [playful] Leave some for the others!",
            "[booming] Correct! [excited] The streak continues!",
            "[playful] Correct! [pause] At this point I should just hand them the trophy.",
            "[gasps] Another one! [chuckles] Somebody check that team for batteries.",
            "[excited] Correct! [playful] They're hogging the board!",
            "[amazed] Correct again! [whispers] Are they even human?",
        ),
        "es": (
            "[amazed] ¡Otra más! [playful] ¡Que alguien los pare!",
            "[excited] ¡Correcto otra vez! [booming] ¡Están encendidos!",
            "[amazed] ¡Van tres seguidas! [playful] ¿Alguien más está jugando?",
            "[proud] ¡Correcto! [playful] Esto se está volviendo el show de un solo equipo.",
            "[excited] ¡Y otra vez! [chuckles] No hay quién los pare.",
            "[amazed] ¡Correcto! [playful] Ya me están dando sospechas de que me leyeron la memoria.",
            "[amazed] ¿¡Otra vez!? [playful] ¡Dejen algo para los demás!",
            "[booming] ¡Correcto! [excited] ¡La racha sigue!",
            "[playful] ¡Correcto! [pause] A este paso les entrego el trofeo de una vez.",
            "[gasps] ¡Otra más! [chuckles] Revisen si ese equipo funciona con pilas.",
            "[excited] ¡Correcto! [playful] ¡Se están comiendo el tablero!",
            "[amazed] ¡Correcto otra vez! [whispers] ¿Serán humanos?",
        ),
    },
    "correct_hard": {
        "en": (
            "[amazed] Wow! [pause] That was a hard one. Correct!",
            "[gasps] Correct! [amazed] I did not think anyone would get that.",
            "[booming] Correct! [proud] Eight hundred points, and you made it look easy.",
            "[amazed] Correct! [playful] Okay, now you're just showing off.",
            "[disbelief] How did you know that? [excited] Correct!",
            "[amazed] Correct! [warmly] That one deserves a round of applause.",
            "[gasps] [booming] Correct! [amazed] The toughest square on the board!",
            "[amazed] Correct! [whispers] I had to look that one up myself.",
            "[disbelief] No way. [pause] [excited] Correct!",
            "[booming] Correct! [proud] Eight hundred points, and well earned.",
            "[amazed] Correct! [playful] Okay, who brought the encyclopedia?",
            "[excited] Correct! [warmly] That was pure genius, right there.",
        ),
        "es": (
            "[amazed] ¡Guao! [pause] Esa estaba difícil. ¡Correcto!",
            "[gasps] ¡Correcto! [amazed] No pensé que nadie se la supiera.",
            "[booming] ¡Correcto! [proud] Ochocientos puntos, y lo hicieron ver fácil.",
            "[amazed] ¡Correcto! [playful] Bueno, ya esto es por echárselas.",
            "[disbelief] ¿Cómo sabías eso? [excited] ¡Correcto!",
            "[amazed] ¡Correcto! [warmly] Esa se merece un aplauso.",
            "[gasps] [booming] ¡Correcto! [amazed] ¡La casilla más difícil del tablero!",
            "[amazed] ¡Correcto! [whispers] Yo mismo tuve que buscar esa en internet.",
            "[disbelief] No puede ser. [pause] [excited] ¡Correcto!",
            "[booming] ¡Correcto! [proud] Ochocientos puntos, y bien ganados.",
            "[amazed] ¡Correcto! [playful] A ver, ¿quién se trajo la enciclopedia?",
            "[excited] ¡Correcto! [warmly] Eso fue de genio, de verdad.",
        ),
    },
    "wrong": {
        "en": (
            "[mock disappointment] Ooh... no.",
            "[sighs] Not quite.",
            "[playful] Ooh, so close... [pause] but no.",
            "[mock disappointment] I'm sorry, that's not it.",
            "[softly] Nope.",
            "[playful] Good guess... [pause] wrong, but good.",
            "[sighs] No, no, no.",
            "[mock disappointment] Ay, that hurts. No.",
            "[playful] Interesting answer. [pause] Also wrong.",
            "[softly] That's not the one.",
            "[mock disappointment] Ooh... [sighs] I wish.",
            "[playful] Nice try!",
            "[mock disappointment] Ooh, no. [playful] But I admire the confidence.",
            "[sighs] Wrong... [warmly] but I like where your head was.",
            "[playful] Hmm... [pause] no.",
            "[softly] Ooh. [pause] Not that one.",
            "[mock disappointment] My circuits say... [pause] no.",
            "[playful] That's a no from me.",
            "[laughs] I wish I could give you that one. [pause] No.",
            "[whispers] Psst... [pause] that's not it.",
        ),
        "es": (
            "[mock disappointment] Uy... no.",
            "[sighs] No exactamente.",
            "[playful] Uy, casi... [pause] pero no.",
            "[mock disappointment] Lo siento, no es esa.",
            "[softly] Nop.",
            "[playful] Buen intento... [pause] mal, pero buen intento.",
            "[sighs] No, no, no.",
            "[mock disappointment] Ay, eso dolió. No.",
            "[playful] Respuesta interesante. [pause] También incorrecta.",
            "[softly] Esa no es.",
            "[mock disappointment] Uy... [sighs] ojalá.",
            "[playful] ¡Buen intento!",
            "[mock disappointment] Uy, no. [playful] Pero qué seguridad, ¿ah?",
            "[sighs] No es... [warmly] pero me gustó la idea.",
            "[playful] Mmm... [pause] no.",
            "[softly] Uy. [pause] Qué va.",
            "[mock disappointment] Mis circuitos dicen... [pause] que no.",
            "[playful] ¡Épale! [pause] No, esa no es.",
            "[laughs] Ojalá te la pudiera dar. [pause] Pero no.",
            "[whispers] Pssst... [pause] no era esa.",
        ),
    },
    "anyone_else": {
        "en": (
            "[curious] Anyone else?",
            "[playful] The buzzers are open!",
            "[curious] Does anybody else want to try?",
            "[snappy] Still up for grabs!",
            "[playful] Who wants it?",
            "[playful] Going once... [pause] going twice...",
            "[curious] Anyone brave enough to try?",
            "[playful] It's still out there! [pause] Anybody?",
        ),
        "es": (
            "[curious] ¿Alguien más?",
            "[playful] ¡Los pulsadores están abiertos!",
            "[curious] ¿Alguien más quiere intentarlo?",
            "[snappy] ¡Sigue disponible!",
            "[playful] ¿Quién la quiere?",
            "[playful] A la una... [pause] a las dos...",
            "[curious] ¿Quién se atreve?",
            "[playful] ¡Sigue en el aire! [pause] ¿Alguien?",
        ),
    },
    "time_up": {
        "en": (
            "[snappy] Time's up!",
            "[mock disappointment] Nobody? [pause] Nobody at all?",
            "[sighs] Time's up. [pause] The answer was...",
            "[playful] Silence in the studio! [pause] The answer was...",
            "[snappy] Out of time! [pause] It was...",
            "[curious] No takers? [pause] The answer was...",
            "[sighs] And the clock wins again. [pause] The answer was...",
            "[playful] Tick, tock... [snappy] time's up!",
            "[mock disappointment] Not a single buzz? [pause] The answer was...",
            "[chuckles] I stumped the whole room! [pause] It was...",
        ),
        "es": (
            "[snappy] ¡Se acabó el tiempo!",
            "[mock disappointment] ¿Nadie? [pause] ¿Nadie nadita?",
            "[sighs] Se acabó el tiempo. [pause] La respuesta era...",
            "[playful] ¡Silencio en el estudio! [pause] La respuesta era...",
            "[snappy] ¡Tiempo! [pause] Era...",
            "[curious] ¿Nadie se anima? [pause] La respuesta era...",
            "[sighs] Y el reloj gana otra vez. [pause] La respuesta era...",
            "[playful] Tic, tac... [snappy] ¡se acabó el tiempo!",
            "[mock disappointment] ¿Ni un solo pulsador? [pause] La respuesta era...",
            "[chuckles] ¡Dejé a toda la sala en blanco! [pause] Era...",
        ),
    },
    "answer_was": {
        "en": ("[softly] The answer was...", "[curious] It was...", "[warmly] The right answer:"),
        "es": ("[softly] La respuesta era...", "[curious] Era...", "[warmly] La respuesta correcta:"),
    },
    "say_again": {
        "en": (
            "[curious] Sorry, I didn't catch that. [pause] Say it again?",
            "[playful] One more time, a little louder?",
            "[curious] Hm? [pause] Say that again for me.",
            "[playful] My antennas missed that. [pause] One more time?",
            "[softly] Come again? [pause] Nice and clear for me.",
        ),
        "es": (
            "[curious] Perdón, no te entendí. [pause] ¿Me lo repites?",
            "[playful] Otra vez, un poquito más alto.",
            "[curious] ¿Cómo? [pause] Repítemelo, porfa.",
            "[playful] Mis antenas no captaron eso. [pause] ¿Otra vez?",
            "[softly] ¿Cómo dijiste? [pause] Clarito, porfa.",
        ),
    },
    "more_specific": {
        "en": (
            "[curious] Can you be more specific?",
            "[playful] Close... [pause] be more specific.",
            "[curious] Mm, which one exactly?",
            "[playful] You're in the neighborhood. [pause] More specific, please.",
            "[playful] Warm... [pause] warmer... I need a little more.",
            "[curious] Almost! [pause] Can you narrow it down?",
        ),
        "es": (
            "[curious] ¿Puedes ser más específico?",
            "[playful] Cerca... [pause] sé más específico.",
            "[curious] Mm, ¿cuál exactamente?",
            "[playful] Vas por buen camino. [pause] Más específico, por favor.",
            "[playful] Caliente... [pause] más caliente... necesito un poquito más.",
            "[curious] ¡Casi! [pause] ¿Puedes precisar un poco más?",
        ),
    },
    "overruled": {
        "en": (
            "[laughs] The room has spoken! [booming] It counts!",
            "[mock disappointment] Overruled by the humans... [playful] Fine, it counts.",
            "[booming] By popular vote... [pause] correct!",
            "[playful] Democracy wins. [excited] Points awarded!",
            "[sighs] Fine, fine... [playful] the humans win this round. Points!",
            "[chuckles] Outvoted by my own family. [warmly] It counts!",
            "[mock disappointment] I stand corrected... [playful] and a little embarrassed. It counts!",
            "[playful] Okay, okay, I'll allow it. [booming] Correct!",
        ),
        "es": (
            "[laughs] ¡La sala ha hablado! [booming] ¡Vale!",
            "[mock disappointment] Me ganaron los humanos... [playful] Bueno, vale.",
            "[booming] Por votación popular... [pause] ¡correcto!",
            "[playful] Ganó la democracia. [excited] ¡Puntos para ustedes!",
            "[sighs] Bueno, bueno... [playful] esta la ganan los humanos. ¡Puntos!",
            "[chuckles] Me ganó la votación mi propia familia. [warmly] ¡Vale!",
            "[mock disappointment] Me corrijo... [playful] y me da un poquito de pena. ¡Vale!",
            "[playful] Está bien, está bien, se la acepto. [booming] ¡Correcto!",
        ),
    },
    "double": {
        "en": (
            "[gasps] [booming] A hidden square! Double or nothing!",
            "[dramatic] Oh-ho! [booming] Double or nothing!",
            "[excited] You found a secret square! [pause] Double or nothing.",
            "[hushed] Careful now... [booming] it's double or nothing!",
            "[whispers] Ooh, something was hiding here... [booming] double or nothing!",
            "[dramatic] Feeling lucky? [booming] It's double or nothing!",
        ),
        "es": (
            "[gasps] [booming] ¡Una casilla escondida! ¡Doble o nada!",
            "[dramatic] ¡Epa! [booming] ¡Doble o nada!",
            "[excited] ¡Encontraron una casilla secreta! [pause] Doble o nada.",
            "[hushed] Cuidado... [booming] ¡es doble o nada!",
            "[whispers] Uy, aquí había algo escondido... [booming] ¡doble o nada!",
            "[dramatic] ¿Se sienten con suerte? [booming] ¡Es doble o nada!",
        ),
    },
    "steal": {
        "en": (
            "[gasps] [booming] A steal square! [playful] Get it right, and you win these points... [pause] and take "
            "the same from the team in the lead!",
            "[playful] Oh, this one's sneaky. [pause] Answer right, and you win the points AND steal them from the "
            "leaders.",
            "[dramatic] It's a steal! [playful] Get it right, and the team in the lead loses these points to you.",
            "[hushed] Ooh, a steal! [playful] Answer right, win the points... [pause] and the leaders lose just as "
            "many.",
            "[gasps] Sneaky square alert! [playful] Get it right, and you take these points from the team on top, too.",
        ),
        "es": (
            "[gasps] [booming] ¡Casilla de robo! [playful] Si aciertan, se ganan estos puntos... [pause] ¡y se los "
            "quitan al equipo que va ganando!",
            "[playful] Uy, esta es tramposa. [pause] Si aciertan, ganan los puntos Y se los roban al que va "
            "punteando.",
            "[dramatic] ¡Es un robo! [playful] Si aciertan, el equipo que va ganando les pasa estos puntos.",
            "[hushed] ¡Uy, un robo! [playful] Si aciertan, ganan los puntos... [pause] y el que va punteando pierde lo "
            "mismo.",
            "[gasps] ¡Alerta de casilla pícara! [playful] Si aciertan, también le quitan estos puntos al equipo de "
            "arriba.",
        ),
    },
    "everyone": {
        "en": (
            "[excited] Everybody plays this one! [warmly] Type your answer on your phone.",
            "[playful] This one's for everyone. [pause] Answers on your phones!",
            "[booming] All hands on deck! [warmly] Everyone answers on the phone.",
            "[excited] Everyone's in on this one! [pause] Type fast!",
            "[warmly] No buzzers this time. [playful] Every brain answers on its phone.",
        ),
        "es": (
            "[excited] ¡Esta la juegan todos! [warmly] Escriban su respuesta en el teléfono.",
            "[playful] Esta es para todo el mundo. [pause] ¡Respuestas en el teléfono!",
            "[booming] ¡Todos a bordo! [warmly] Cada quien responde en su teléfono.",
            "[excited] ¡En esta participan todos! [pause] ¡Escriban rápido!",
            "[warmly] Esta vez no hay pulsador. [playful] Cada cerebro responde en su teléfono.",
        ),
    },
    "bomb": {
        "en": (
            "[gasps] [dramatic] It's a BOMB! [pause] Only your team can answer. Get it right, and it's double "
            "points... [whispers] get it wrong, or say nothing... [shouts] BOOM! You lose the points.",
            "[hushed] Nobody move... [dramatic] there's a bomb on this square! [playful] Only you can answer: right "
            "is double, wrong goes boom.",
            "[gasps] Uh-oh. [booming] BOMB SQUARE! [pause] It's all yours: defuse it for double points, or lose them "
            "in the blast.",
            "[dramatic] Tick, tock, tick, tock... [booming] a BOMB! [playful] Only your team answers. Right doubles "
            "the points. Wrong, or silence... [whispers] kaboom.",
            "[whispers] Careful, careful... [dramatic] you found the bomb. [pause] Only you may answer. Double if "
            "you're right... [chuckles] and a little explosion if you're not.",
        ),
        "es": (
            "[gasps] [dramatic] ¡Es una BOMBA! [pause] Solo su equipo puede responder. Si aciertan, puntos "
            "dobles... [whispers] si fallan, o se quedan callados... [shouts] ¡BUM! Pierden los puntos.",
            "[hushed] Que nadie se mueva... [dramatic] ¡aquí hay una bomba! [playful] Solo ustedes responden: si "
            "aciertan, doble; si fallan, explota.",
            "[gasps] Ay, ay, ay. [booming] ¡CASILLA BOMBA! [pause] Es toda suya: desactívenla y ganan el doble, o "
            "pierden los puntos en la explosión.",
            "[dramatic] Tic, tac, tic, tac... [booming] ¡una BOMBA! [playful] Solo responde su equipo. Si aciertan, "
            "doble. Si fallan o se quedan callados... [whispers] cabum.",
            "[whispers] Cuidadito, cuidadito... [dramatic] encontraron la bomba. [pause] Solo ustedes pueden "
            "responder. Doble si aciertan... [chuckles] y una explosioncita si no.",
        ),
    },
    "bomb_kids": {
        "en": (
            "[gasps] [dramatic] It's a BOMB! [playful] Only your team can answer. Get it right to defuse it, and "
            "the points count double!",
            "[hushed] Shh... [dramatic] a bomb is ticking on this square! [playful] Only you can answer. Defuse it "
            "for double points!",
            "[dramatic] Tick, tock... [booming] a BOMB! [warmly] It's just for your team. Get it right, and you win "
            "double!",
        ),
        "es": (
            "[gasps] [dramatic] ¡Es una BOMBA! [playful] Solo su equipo puede responder. ¡Si aciertan, la "
            "desactivan y los puntos valen el doble!",
            "[hushed] Shh... [dramatic] ¡en esta casilla hay una bomba haciendo tic tac! [playful] Solo ustedes "
            "responden. ¡Desactívenla y ganan el doble!",
            "[dramatic] Tic, tac... [booming] ¡una BOMBA! [warmly] Es solo para su equipo. ¡Si aciertan, ganan el "
            "doble!",
        ),
    },
    "bomb_defused": {
        "en": (
            "[excited] Bomb defused! [booming] Double points!",
            "[sighs] Phew! [excited] You cut the right wire! Double points!",
            "[booming] BOMB DEFUSED! [playful] Nerves of steel, folks.",
            "[amazed] They did it! [excited] The bomb is defused, and the points are doubled!",
            "[laughs] Not today, bomb! [booming] Double points!",
        ),
        "es": (
            "[excited] ¡Bomba desactivada! [booming] ¡Puntos dobles!",
            "[sighs] ¡Uf! [excited] ¡Cortaron el cable correcto! ¡Puntos dobles!",
            "[booming] ¡BOMBA DESACTIVADA! [playful] Nervios de acero, señores.",
            "[amazed] ¡Lo lograron! [excited] ¡La bomba está desactivada y los puntos se duplican!",
            "[laughs] ¡Hoy no, bomba! [booming] ¡Puntos dobles!",
        ),
    },
    "bomb_boom": {
        "en": (
            "[shouts] BOOM! [laughs] That one blew up in your hands!",
            "[dramatic] Three, two, one... [shouts] BOOM!",
            "[shouts] KABOOM! [mock disappointment] Wrong wire, my friends.",
            "[gasps] Oh no... [shouts] BOOM! [chuckles] Somebody open a window.",
            "[shouts] BOOM! [playful] I'm going to need new antennas after that one.",
        ),
        "es": (
            "[shouts] ¡BUM! [laughs] ¡Esa les explotó en las manos!",
            "[dramatic] Tres, dos, uno... [shouts] ¡BUM!",
            "[shouts] ¡CABUM! [mock disappointment] Cable equivocado, mis amigos.",
            "[gasps] Ay, no... [shouts] ¡BUM! [chuckles] Que alguien abra una ventana.",
            "[shouts] ¡BUM! [playful] Después de esa voy a necesitar antenas nuevas.",
        ),
    },
    "cards_rules": {
        "en": (
            "[playful] And every team gets two secret cards, to play once each on the phone. [pause] Freeze: "
            "another team can't buzz on the next clue. Shield: a wrong answer costs you nothing. Hint: a peek at "
            "the answer's first letter. And Hijack: steal another team's pick! [excited] Use them wisely!",
            "[curious] One more thing... [playful] look at your phones. Each team has two cards out of four. "
            "Freeze, Shield, Hint and Hijack. [pause] Freeze stops a team from buzzing, Shield saves your points, "
            "Hint gives you a first letter, and Hijack steals the pick. [excited] Let the chaos begin!",
        ),
        "es": (
            "[playful] Y cada equipo tiene dos cartas secretas, que se juegan una vez cada una en el teléfono. "
            "[pause] Congelar: otro equipo no puede pulsar en la próxima pista. Escudo: si fallan, no pierden "
            "nada. Pista: un vistazo a la primera letra de la respuesta. Y Secuestro: ¡le roban el turno de escoger "
            "a otro equipo! [excited] ¡Úsenlas bien!",
            "[curious] Una cosa más... [playful] miren el teléfono. Cada equipo tiene dos cartas de cuatro. "
            "Congelar, Escudo, Pista y Secuestro. [pause] Congelar deja a un equipo sin pulsar, Escudo salva sus "
            "puntos, Pista les da la primera letra, y Secuestro les roba el turno. [excited] ¡Que empiece el caos!",
        ),
    },
    "cards_rules_kids": {
        "en": (
            "[playful] And every team gets two secret cards, to play once each on the phone. [pause] Freeze: "
            "another team can't buzz on the next clue. Hint: a peek at the answer's first letter. And Hijack: "
            "steal another team's pick! [excited] Use them wisely!",
            "[curious] One more thing... [playful] look at your phones. Each team has two magic cards. [pause] "
            "Freeze stops a team from buzzing, Hint gives you a first letter, and Hijack steals the pick. "
            "[excited] Have fun!",
        ),
        "es": (
            "[playful] Y cada equipo tiene dos cartas secretas, que se juegan una vez cada una en el teléfono. "
            "[pause] Congelar: otro equipo no puede pulsar en la próxima pista. Pista: un vistazo a la primera "
            "letra de la respuesta. Y Secuestro: ¡le roban el turno de escoger a otro equipo! [excited] ¡Úsenlas "
            "bien!",
            "[curious] Una cosa más... [playful] miren el teléfono. Cada equipo tiene dos cartas mágicas. [pause] "
            "Congelar deja a un equipo sin pulsar, Pista les da la primera letra, y Secuestro les roba el turno. "
            "[excited] ¡A divertirse!",
        ),
    },
    "shield": {
        "en": (
            "[booming] Shields up! [playful] If they miss this one, it costs them nothing.",
            "[dramatic] They raise the shield! [pause] A wrong answer won't hurt them.",
            "[playful] Playing it safe, I see. [booming] Shield activated!",
            "[excited] Shield! [chuckles] No risk, all reward.",
        ),
        "es": (
            "[booming] ¡Escudo arriba! [playful] Si fallan esta, no les cuesta nada.",
            "[dramatic] ¡Levantan el escudo! [pause] Una respuesta mala no les hace daño.",
            "[playful] Jugando a lo seguro, ya veo. [booming] ¡Escudo activado!",
            "[excited] ¡Escudo! [chuckles] Cero riesgo, puro premio.",
        ),
    },
    "shield_saved": {
        "en": (
            "[playful] But the shield held! [pause] Not a single point lost.",
            "[sighs] Lucky for them... [booming] the shield saved the day!",
            "[chuckles] Good thing they brought a shield. [warmly] No points lost.",
            "[excited] The shield blocks it! [playful] Their score doesn't move.",
        ),
        "es": (
            "[playful] ¡Pero el escudo aguantó! [pause] No pierden ni un punto.",
            "[sighs] Menos mal... [booming] ¡el escudo los salvó!",
            "[chuckles] Qué bueno que trajeron escudo. [warmly] No pierden puntos.",
            "[excited] ¡El escudo lo para! [playful] Su marcador ni se mueve.",
        ),
    },
    "hint": {
        "en": (
            "[whispers] They asked for a hint... [playful] and no, I'm not telling.",
            "[curious] Ooh, somebody needed a little help. [whispers] It's on their phones.",
            "[playful] A hint! [chuckles] Their secret is safe with me.",
            "[hushed] A little birdie just whispered something to their phones...",
        ),
        "es": (
            "[whispers] Pidieron una pista... [playful] y no, no les voy a decir cuál.",
            "[curious] Uy, alguien necesitaba una ayudita. [whispers] Está en su teléfono.",
            "[playful] ¡Una pista! [chuckles] Su secreto está a salvo conmigo.",
            "[hushed] Un pajarito les acaba de soplar algo al teléfono...",
        ),
    },
    "lead_change": {
        "en": (
            "[gasps] We have a new leader!",
            "[excited] And just like that... [pause] the lead changes hands!",
            "[amazed] A new team on top! [playful] Things are getting spicy.",
            "[dramatic] The tables have turned!",
            "[playful] Look who's in first place now!",
            "[gasps] Wait, wait, wait... [booming] there's a new team on top!",
            "[playful] Excuse me, coming through! [excited] New leader!",
            "[dramatic] The crown has been stolen!",
            "[amazed] Plot twist! [pause] A new team takes the lead!",
            "[playful] Oh, the old leaders won't like this... [excited] new team in first!",
        ),
        "es": (
            "[gasps] ¡Tenemos nuevo líder!",
            "[excited] Y así, sin más... [pause] ¡cambia el primer lugar!",
            "[amazed] ¡Un nuevo equipo en la cima! [playful] Esto se está poniendo bueno.",
            "[dramatic] ¡Se voltearon las cosas!",
            "[playful] ¡Miren quién va de primero ahora!",
            "[gasps] Epa, epa, epa... [booming] ¡hay equipo nuevo en la punta!",
            "[playful] ¡Permiso, permiso! [excited] ¡Que hay nuevo puntero!",
            "[dramatic] ¡Les robaron la corona!",
            "[amazed] ¡Giro inesperado! [pause] ¡Un nuevo equipo toma la delantera!",
            "[playful] Uy, esto no le va a gustar al que iba ganando... [excited] ¡nuevo primer lugar!",
        ),
    },
    "comeback": {
        "en": (
            "[excited] What a comeback!",
            "[amazed] They were way behind... [pause] and now look!",
            "[booming] Never count them out!",
            "[playful] From last place to the race! [excited] Love it.",
            "[amazed] Out of nowhere! [excited] They're back in the game!",
            "[playful] Remember when they were losing? [chuckles] Me neither.",
            "[booming] Here come the underdogs!",
            "[proud] Never give up, folks. [warmly] That's how it's done.",
        ),
        "es": (
            "[excited] ¡Qué remontada!",
            "[amazed] Iban muy atrás... [pause] ¡y ahora mírenlos!",
            "[booming] ¡Nunca los den por muertos!",
            "[playful] ¡Del último lugar a la pelea! [excited] Me encanta.",
            "[amazed] ¡Salieron de la nada! [excited] ¡Volvieron al juego!",
            "[playful] ¿Se acuerdan cuando iban perdiendo? [chuckles] Yo tampoco.",
            "[booming] ¡Ahí vienen los que nadie esperaba!",
            "[proud] Nunca se rindan, gente. [warmly] Así es que se hace.",
        ),
    },
    "wrong_streak": {
        "en": (
            "[sighs] Another miss... [warmly] but the next one's yours, I can feel it.",
            "[mock disappointment] Oof. [playful] Rough patch. Shake it off!",
            "[playful] Hmm, a little slump. [warmly] Deep breath. You've got this.",
            "[chuckles] Maybe let the brain catch up to the buzzer? [warmly] You'll get the next one.",
            "[softly] Not your night so far... [excited] but the night is young!",
            "[warmly] Hey, even big brains have off days. [playful] Next one!",
        ),
        "es": (
            "[sighs] Otra que se escapa... [warmly] pero la próxima es de ustedes, lo presiento.",
            "[mock disappointment] Uf. [playful] Mala racha. ¡Sacúdanse!",
            "[playful] Mmm, se nos enfrió la cosa. [warmly] Respiren hondo. Ustedes pueden.",
            "[chuckles] ¿Y si dejan que el cerebro alcance al dedo? [warmly] La próxima sale.",
            "[softly] Hoy no ha sido su noche... [excited] ¡pero la noche es joven!",
            "[warmly] Calma, que hasta los cráneos tienen días malos. [playful] ¡A la próxima!",
        ),
    },
    "below_zero": {
        "en": (
            "[mock disappointment] And that puts you in the negatives. [playful] Impressive, in a way.",
            "[gasps] Below zero! [playful] I didn't even know the scoreboard went that low.",
            "[chuckles] Negative points! [warmly] Good news: from here, the only way is up.",
            "[sighs] Below zero... [playful] somebody bring that team a blanket.",
            "[playful] Your score has a minus sign now. [whispers] I won't tell anyone.",
        ),
        "es": (
            "[mock disappointment] Y con eso quedan en negativo. [playful] Impresionante, en cierto modo.",
            "[gasps] ¡Bajo cero! [playful] Ni sabía que el marcador llegaba tan abajo.",
            "[chuckles] ¡Puntos negativos! [warmly] Lo bueno es que de aquí solo se puede subir.",
            "[sighs] Bajo cero... [playful] que alguien le traiga una cobija a ese equipo.",
            "[playful] Su marcador ahora tiene un signo de menos. [whispers] No le digo a nadie.",
        ),
    },
    "runaway": {
        "en": (
            "[amazed] Is anybody going to catch them?",
            "[playful] They're so far ahead, they need binoculars to see second place.",
            "[dramatic] The leaders are pulling away... [pause] somebody do something!",
            "[whispers] Other teams... [pause] now would be a great time to wake up.",
            "[booming] Way out in front! [playful] But there's still plenty of game left.",
        ),
        "es": (
            "[amazed] ¿Alguien los va a alcanzar?",
            "[playful] Van tan adelante que necesitan binoculares para ver al segundo lugar.",
            "[dramatic] Los punteros se están escapando... [pause] ¡que alguien haga algo!",
            "[whispers] Al resto de los equipos... [pause] ya va siendo hora de despertarse.",
            "[booming] ¡Se fueron lejísimos! [playful] Pero todavía queda burda de juego.",
        ),
    },
    "scores": {
        "en": (
            "[curious] Let's check the scores!",
            "[warmly] Here's how we stand.",
            "[playful] Time for a quick look at the scoreboard.",
            "[curious] So, who's winning?",
        ),
        "es": (
            "[curious] ¡Vamos a ver la puntuación!",
            "[warmly] Así vamos.",
            "[playful] Hora de echarle un ojo al marcador.",
            "[curious] A ver, ¿quién va ganando?",
        ),
    },
    "board_done": {
        "en": (
            "[booming] And that's the board! [pause] But we're not done yet...",
            "[excited] Every square is gone! [dramatic] Which can only mean one thing...",
            "[playful] The board is clean. [pause] Time for the big finish.",
            "[amazed] We cleared the whole board! [dramatic] But the biggest moment is still to come...",
            "[warmly] What a board, everyone. [hushed] And now... the grand finale.",
        ),
        "es": (
            "[booming] ¡Y se acabó el tablero! [pause] Pero todavía no terminamos...",
            "[excited] ¡No queda ni una casilla! [dramatic] Y eso solo puede significar una cosa...",
            "[playful] El tablero está limpio. [pause] Hora del gran final.",
            "[amazed] ¡Barrieron con todo el tablero! [dramatic] Pero lo más grande todavía está por venir...",
            "[warmly] Qué tablero, gente. [hushed] Y ahora... el gran final.",
        ),
    },
    "final_intro": {
        "en": (
            "[hushed] It's time... [long pause] [booming] for the FINAL ROUND!",
            "[dramatic] Everything comes down to this. [pause] The final round.",
            "[booming] Ladies and gentlemen... [long pause] the final round!",
            "[hushed] Lights down, please... [pause] this is the final round.",
        ),
        "es": (
            "[hushed] Llegó la hora... [long pause] [booming] ¡de la RONDA FINAL!",
            "[dramatic] Todo se decide aquí. [pause] La ronda final.",
            "[booming] Damas y caballeros... [long pause] ¡la ronda final!",
            "[hushed] Bajen las luces, por favor... [pause] esta es la ronda final.",
        ),
    },
    "place_bets": {
        "en": (
            "[hushed] Here's the category. [pause] Now place your bets on your phone.",
            "[dramatic] How many points will you risk? [pause] Bet on your phone.",
            "[playful] Bet big or play it safe... [pause] it's up to you.",
        ),
        "es": (
            "[hushed] Esta es la categoría. [pause] Ahora hagan su apuesta en el teléfono.",
            "[dramatic] ¿Cuántos puntos van a arriesgar? [pause] Apuesten en el teléfono.",
            "[playful] Apostar duro o ir a la segura... [pause] ustedes deciden.",
        ),
    },
    "final_clue": {
        "en": (
            "[hushed] Here is your final clue.",
            "[dramatic] The final clue... [pause] listen carefully.",
            "[hushed] Everyone ready? [pause] Here it is.",
        ),
        "es": (
            "[hushed] Esta es la pista final.",
            "[dramatic] La pista final... [pause] escuchen con atención.",
            "[hushed] ¿Todos listos? [pause] Aquí va.",
        ),
    },
    "thinking": {
        "en": (
            "[snappy] Thirty seconds. [pause] Write your answer!",
            "[playful] Thirty seconds on the clock... [pause] go!",
            "[snappy] Answers on your phones. [pause] Thirty seconds!",
            "[playful] The clock is ticking... [pause] write fast, think faster!",
            "[hushed] Quietly now... [pause] answers on your phones.",
            "[playful] Cue the thinking music! [pause] Write your answer.",
        ),
        "es": (
            "[snappy] Treinta segundos. [pause] ¡Escriban su respuesta!",
            "[playful] Treinta segundos en el reloj... [pause] ¡ya!",
            "[snappy] Respuestas en el teléfono. [pause] ¡Treinta segundos!",
            "[playful] El reloj está corriendo... [pause] ¡escriban rápido, piensen más rápido!",
            "[hushed] En silencio... [pause] respuestas en el teléfono.",
            "[playful] ¡Que suene la música de pensar! [pause] Escriban su respuesta.",
        ),
    },
    "reveal": {
        "en": (
            "[dramatic] Let's see what you wrote...",
            "[hushed] The moment of truth...",
            "[curious] Let's look at the answers... [pause] one by one.",
            "[hushed] Phones down... [pause] let's see who was right.",
            "[dramatic] No more changes... [pause] here come the answers.",
        ),
        "es": (
            "[dramatic] A ver qué escribieron...",
            "[hushed] El momento de la verdad...",
            "[curious] Vamos a ver las respuestas... [pause] una por una.",
            "[hushed] Suelten los teléfonos... [pause] a ver quién la pegó.",
            "[dramatic] Ya no hay vuelta atrás... [pause] aquí vienen las respuestas.",
        ),
    },
    "winner": {
        "en": (
            "[booming] And the winner of BIG BRAIN is...",
            "[dramatic] After all that... [long pause] our champion is...",
            "[excited] We have a winner! [pause] Congratulations to...",
            "[booming] Give it up for tonight's biggest brain...",
            "[hushed] Drumroll, please... [booming] tonight's BIG BRAIN champions are...",
            "[dramatic] The points are counted, the brains are tired... [booming] and the winner is...",
            "[excited] It's official! [booming] The biggest brains in the house belong to...",
        ),
        "es": (
            "[booming] Y el ganador de BIG BRAIN es...",
            "[dramatic] Después de todo esto... [long pause] nuestro campeón es...",
            "[excited] ¡Tenemos ganador! [pause] Felicitaciones a...",
            "[booming] Un aplauso para el cerebro más grande de la noche...",
            "[hushed] Redoble de tambores, por favor... [booming] los campeones de BIG BRAIN esta noche son...",
            "[dramatic] Los puntos están contados, los cerebros cansados... [booming] y el ganador es...",
            "[excited] ¡Es oficial! [booming] Los cerebros más grandes de la casa son de...",
        ),
    },
    "tie": {
        "en": (
            "[gasps] A tie! [amazed] I did not see that coming.",
            "[playful] It's a tie! [pause] Everybody wins... [chuckles] or nobody does.",
            "[disbelief] Exactly the same score? [laughs] You planned this, didn't you?",
            "[warmly] A tie! [playful] I guess you'll have to share the bragging rights.",
        ),
        "es": (
            "[gasps] ¡Un empate! [amazed] Eso no me lo esperaba.",
            "[playful] ¡Empate! [pause] Todos ganan... [chuckles] o nadie gana.",
            "[disbelief] ¿Exactamente los mismos puntos? [laughs] Ustedes se pusieron de acuerdo, ¿verdad?",
            "[warmly] ¡Empate! [playful] Les toca compartir la gloria.",
        ),
    },
    "goodbye": {
        "en": (
            "[warmly] That's all for tonight. [booming] Thanks for playing BIG BRAIN!",
            "[proud] What a game. [warmly] Good night, big brains!",
            "[booming] This has been BIG BRAIN! [playful] Keep those brains big.",
            "[warmly] Thanks for playing, everyone. [playful] Same time next week?",
        ),
        "es": (
            "[warmly] Eso es todo por esta noche. [booming] ¡Gracias por jugar BIG BRAIN!",
            "[proud] Qué partidazo. [warmly] ¡Buenas noches, cráneos!",
            "[booming] ¡Esto fue BIG BRAIN! [playful] Mantengan esos cerebros bien grandes.",
            "[warmly] Gracias por jugar, gente. [playful] ¿La semana que viene a la misma hora?",
        ),
    },
    # The Hogwarts edition's own opening and closing (games/big_brain.py, Edition.key "hp").
    "welcome_hp": {
        "en": (
            "[booming] Good evening, witches and wizards... [pause] and welcome to BIG BRAIN: the Hogwarts edition!",
            "[playful] Wands at the ready, phones out... [excited] it's BIG BRAIN, Hogwarts edition!",
            "[dramatic] Tonight, one house... [pause] I mean, one team... [booming] wins the cup! Welcome to BIG "
            "BRAIN, Hogwarts edition!",
            "[hushed] The castle is quiet, the candles are lit... [booming] it's time for BIG BRAIN: the Hogwarts "
            "edition!",
        ),
        "es": (
            "[booming] Muy buenas noches, brujas y magos... [pause] ¡y bienvenidos a BIG BRAIN, edición Hogwarts!",
            "[playful] Varitas listas, teléfonos en mano... [excited] ¡es BIG BRAIN, edición Hogwarts!",
            "[dramatic] Esta noche, una casa... [pause] digo, un equipo... [booming] se lleva la copa. ¡Bienvenidos "
            "a BIG BRAIN, edición Hogwarts!",
            "[hushed] El castillo está en silencio, las velas encendidas... [booming] ¡llegó la hora de BIG BRAIN, "
            "edición Hogwarts!",
        ),
    },
    "goodbye_hp": {
        "en": (
            "[warmly] That's all for tonight. [playful] Don't forget to feed your owls. Good night!",
            "[playful] Ten points to every team, just for playing! [booming] Good night from BIG BRAIN!",
            "[warmly] Thank you for playing, witches and wizards. [pause] See you next term!",
        ),
        "es": (
            "[warmly] Eso es todo por esta noche. [playful] No se olviden de darle de comer a sus lechuzas. ¡Buenas "
            "noches!",
            "[playful] ¡Diez puntos para cada equipo, solo por jugar! [booming] ¡Buenas noches desde BIG BRAIN!",
            "[warmly] Gracias por jugar, brujas y magos. [pause] ¡Nos vemos el próximo curso!",
        ),
    },
    # The Middle-earth edition's own opening and closing (Edition.key "lotr").
    "welcome_lotr": {
        "en": (
            "[booming] Hobbits, elves, dwarves and men of the living room... [pause] welcome to BIG BRAIN: the "
            "Middle-earth edition!",
            "[hushed] One quiz to rule them all... [booming] it's BIG BRAIN, Middle-earth edition!",
            "[playful] Second breakfast is over, phones are out... [excited] it's BIG BRAIN, Middle-earth edition!",
            "[dramatic] The beacons are lit! [booming] Welcome to BIG BRAIN: the Middle-earth edition!",
        ),
        "es": (
            "[booming] Hobbits, elfos, enanos y hombres de esta sala... [pause] ¡bienvenidos a BIG BRAIN, edición "
            "Tierra Media!",
            "[hushed] Un concurso para gobernarlos a todos... [booming] ¡es BIG BRAIN, edición Tierra Media!",
            "[playful] Ya pasó el segundo desayuno, teléfonos en mano... [excited] ¡es BIG BRAIN, edición Tierra "
            "Media!",
            "[dramatic] ¡Se encendieron las almenaras! [booming] ¡Bienvenidos a BIG BRAIN, edición Tierra Media!",
        ),
    },
    "goodbye_lotr": {
        "en": (
            "[warmly] And so our quest ends. [playful] Time for second dinner. Good night!",
            "[warmly] You were brave, every one of you. [booming] Good night from BIG BRAIN!",
            "[playful] The road goes ever on... [warmly] but tonight, it goes to bed. Good night!",
        ),
        "es": (
            "[warmly] Y así termina nuestra aventura. [playful] Hora de la segunda cena. ¡Buenas noches!",
            "[warmly] Fueron valientes, todos y cada uno. [booming] ¡Buenas noches desde BIG BRAIN!",
            "[playful] El camino sigue y sigue... [warmly] pero esta noche, se va a dormir. ¡Buenas noches!",
        ),
    },
    # The Galactic edition's own opening and closing (Edition.key "sw").
    "welcome_sw": {
        "en": (
            "[booming] A long time ago, in a living room not so far away... [pause] BIG BRAIN: the Galactic edition!",
            "[playful] Phones charged, lightsabers ready... [excited] it's BIG BRAIN, Galactic edition!",
            "[hushed] I sense a great disturbance in this living room... [booming] it's BIG BRAIN: the Galactic "
            "edition!",
            "[dramatic] Rebels, Jedi, and the occasional Sith... [booming] welcome to BIG BRAIN, Galactic edition!",
        ),
        "es": (
            "[booming] Hace mucho tiempo, en una sala no muy lejana... [pause] ¡BIG BRAIN, edición Galáctica!",
            "[playful] Teléfonos cargados, sables de luz listos... [excited] ¡es BIG BRAIN, edición Galáctica!",
            "[hushed] Siento una gran perturbación en esta sala... [booming] ¡es BIG BRAIN, edición Galáctica!",
            "[dramatic] Rebeldes, Jedi, y uno que otro Sith... [booming] ¡bienvenidos a BIG BRAIN, edición Galáctica!",
        ),
    },
    "goodbye_sw": {
        "en": (
            "[warmly] That's all for tonight. [playful] May the points be with you. Good night!",
            "[booming] The game is over... [warmly] but the Force will be with you. Good night!",
            "[playful] Do, or do not... [warmly] there is no next game tonight. Good night!",
        ),
        "es": (
            "[warmly] Eso es todo por esta noche. [playful] Que los puntos los acompañen. ¡Buenas noches!",
            "[booming] Se acabó el juego... [warmly] pero la Fuerza estará con ustedes. ¡Buenas noches!",
            "[playful] Hazlo, o no lo hagas... [warmly] esta noche no hay otro juego. ¡Buenas noches!",
        ),
    },
    # The Disney edition's own opening and closing (Edition.key "dis").
    "welcome_dis": {
        "en": (
            "[booming] Once upon a time, in a living room just like this one... [pause] BIG BRAIN: the Disney "
            "edition!",
            "[playful] Wish upon a star, and grab your phones... [excited] it's BIG BRAIN, Disney edition!",
            "[warmly] Princesses, heroes, and the occasional villain... [booming] welcome to BIG BRAIN, Disney "
            "edition!",
            "[dramatic] Tonight, someone gets their happily ever after... [booming] it's BIG BRAIN: the Disney "
            "edition!",
        ),
        "es": (
            "[booming] Había una vez, en una sala igualita a esta... [pause] ¡BIG BRAIN, edición Disney!",
            "[playful] Pidan un deseo a una estrella, y agarren sus teléfonos... [excited] ¡es BIG BRAIN, edición "
            "Disney!",
            "[warmly] Princesas, héroes, y uno que otro villano... [booming] ¡bienvenidos a BIG BRAIN, edición "
            "Disney!",
            "[dramatic] Esta noche, alguien tendrá su final feliz... [booming] ¡es BIG BRAIN, edición Disney!",
        ),
    },
    "goodbye_dis": {
        "en": (
            "[warmly] And they all lived happily ever after. [playful] The end! Good night!",
            "[playful] That's all, folks... [warmly] well, that's another studio. Good night from BIG BRAIN!",
            "[warmly] Thank you for playing. [playful] Now go to bed before the clock strikes midnight!",
        ),
        "es": (
            "[warmly] Y vivieron felices para siempre. [playful] ¡Fin! ¡Buenas noches!",
            "[playful] Hasta aquí llegó el cuento de hoy. [warmly] ¡Buenas noches desde BIG BRAIN!",
            "[warmly] Gracias por jugar. [playful] ¡Ahora a dormir antes de que el reloj dé las doce!",
        ),
    },
    "pause": {
        "en": (
            "[softly] We'll pause here.",
            "[playful] Commercial break! [pause] Back soon.",
            "[warmly] Game paused.",
        ),
        "es": (
            "[softly] Hacemos una pausa.",
            "[playful] ¡Pausa comercial! [pause] Ya volvemos.",
            "[warmly] Juego en pausa.",
        ),
    },
    "resume": {
        "en": (
            "[booming] And we're back!",
            "[excited] Welcome back to BIG BRAIN!",
            "[warmly] Where were we? [playful] Oh, right... [pause] winning.",
        ),
        "es": (
            "[booming] ¡Y estamos de vuelta!",
            "[excited] ¡Bienvenidos de nuevo a BIG BRAIN!",
            "[warmly] ¿En qué íbamos? [playful] Ah, sí... [pause] ganando.",
        ),
    },
}


def _team_lines() -> dict[str, _Lines]:
    """Return the lines that name a team: a buzz calls it out, its pick is announced, a win crowns it.

    A card played on a team names it (``freeze_<team>``, ``hijack_<team>``), after the player's ``buzz_<team>``.
    """
    lines: dict[str, _Lines] = {}
    for key, (en, es) in TEAMS.items():
        lines[f"buzz_{key}"] = {
            "en": (f"[snappy] {en.capitalize()} team!", f"[excited] {en.capitalize()}!"),
            "es": (f"[snappy] ¡Equipo {es}!", f"[excited] ¡{es.capitalize()}!"),
        }
        lines[f"pick_{key}"] = {
            "en": (
                f"[warmly] {en.capitalize()} team, it's your pick! [pause] Tap a square on your phone.",
                f"[playful] {en.capitalize()} team, the board is yours. [pause] Pick a square.",
                f"[curious] {en.capitalize()} team... [pause] where to next?",
            ),
            "es": (
                f"[warmly] ¡Equipo {es}, les toca escoger! [pause] Toquen una casilla en el teléfono.",
                f"[playful] Equipo {es}, el tablero es suyo. [pause] Escojan una casilla.",
                f"[curious] Equipo {es}... [pause] ¿para dónde vamos?",
            ),
        }
        lines[f"winner_{key}"] = {
            "en": (f"[booming] The {en.upper()} TEAM!", f"[excited] Team {en}! [booming] Champions!"),
            "es": (f"[booming] ¡El EQUIPO {es.upper()}!", f"[excited] ¡Equipo {es}! [booming] ¡Campeones!"),
        }
        lines[f"freeze_{key}"] = {
            "en": (
                f"[dramatic] Freezes the {en} team! [playful] No buzzing for you on this one.",
                f"[gasps] The {en} team is frozen solid! [chuckles] Somebody get them a blanket.",
                f"[booming] FREEZE! [playful] {en.capitalize()} team, hands off the buzzer for this clue.",
            ),
            "es": (
                f"[dramatic] ¡Congela al equipo {es}! [playful] En esta no pulsan.",
                f"[gasps] ¡El equipo {es} quedó congelado! [chuckles] Que alguien les traiga una cobija.",
                f"[booming] ¡CONGELADOS! [playful] Equipo {es}, las manos lejos del pulsador en esta pista.",
            ),
        }
        lines[f"hijack_{key}"] = {
            "en": (
                f"[dramatic] Hijacks the {en} team's pick! [playful] The board changes hands.",
                f"[gasps] A hijack! [booming] Sorry, {en} team, the pick is theirs now.",
                f"[playful] Excuse me, {en} team... [snappy] this pick has been hijacked!",
            ),
            "es": (
                f"[dramatic] ¡Le secuestra el turno al equipo {es}! [playful] El tablero cambia de manos.",
                f"[gasps] ¡Un secuestro! [booming] Lo siento, equipo {es}, ahora escogen ellos.",
                f"[playful] Permiso, equipo {es}... [snappy] ¡este turno fue secuestrado!",
            ),
        }
    return lines


# Every moment's takes, by language.
LINES: Final[dict[str, _Lines]] = {**_MOMENTS, **_team_lines()}

_TAG = re.compile(r"\[([^\]]*)\]\s*")


def tags_in(text: str) -> list[str]:
    """Return the audio tags in ``text``, in order."""
    return [tag.strip() for tag in _TAG.findall(text)]


def shown(text: str) -> str:
    """Return ``text`` without its audio tags, for the screen and the phones."""
    return _TAG.sub("", text).strip()
