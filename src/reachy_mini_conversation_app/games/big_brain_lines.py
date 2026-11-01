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
        ),
        "es": (
            "[playful] ¿Quién más se une? [pause] No sean tímidos.",
            "[curious] ¿Alguien más? Aquí cabe otro cerebro.",
            "[playful] Todavía hay cerebros calentando motores...",
            "[warmly] Tranquilos, con calma. [playful] Las preguntas no se van a ningún lado.",
        ),
    },
    "begin": {
        "en": (
            "[excited] All right, everybody's in. [booming] Let's play!",
            "[booming] Teams are set... [pause] let the game begin!",
            "[excited] We have our players! [booming] Here we go!",
            "[playful] Phones ready? Brains ready? [booming] Let's do this!",
        ),
        "es": (
            "[excited] Muy bien, ya estamos todos. [booming] ¡A jugar!",
            "[booming] Los equipos están listos... [pause] ¡que empiece el juego!",
            "[excited] ¡Ya tenemos jugadores! [booming] ¡Arrancamos!",
            "[playful] ¿Teléfonos listos? ¿Cerebros listos? [booming] ¡Vamos con todo!",
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
        ),
        "es": (
            "[snappy] ¿Tu respuesta?",
            "[curious] ¡Dale!",
            "[snappy] ¿Sí?",
            "[curious] ¿Qué dices?",
            "[playful] Tienes la palabra.",
            "[snappy] ¡A ver!",
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
        ),
        "es": (
            "[amazed] ¡Otra más! [playful] ¡Que alguien los pare!",
            "[excited] ¡Correcto otra vez! [booming] ¡Están encendidos!",
            "[amazed] ¡Van tres seguidas! [playful] ¿Alguien más está jugando?",
            "[proud] ¡Correcto! [playful] Esto se está volviendo el show de un solo equipo.",
            "[excited] ¡Y otra vez! [chuckles] No hay quién los pare.",
            "[amazed] ¡Correcto! [playful] Ya me están dando sospechas de que me leyeron la memoria.",
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
        ),
        "es": (
            "[amazed] ¡Guao! [pause] Esa estaba difícil. ¡Correcto!",
            "[gasps] ¡Correcto! [amazed] No pensé que nadie se la supiera.",
            "[booming] ¡Correcto! [proud] Ochocientos puntos, y lo hicieron ver fácil.",
            "[amazed] ¡Correcto! [playful] Bueno, ya esto es por echárselas.",
            "[disbelief] ¿Cómo sabías eso? [excited] ¡Correcto!",
            "[amazed] ¡Correcto! [warmly] Esa se merece un aplauso.",
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
        ),
    },
    "anyone_else": {
        "en": (
            "[curious] Anyone else?",
            "[playful] The buzzers are open!",
            "[curious] Does anybody else want to try?",
            "[snappy] Still up for grabs!",
            "[playful] Who wants it?",
        ),
        "es": (
            "[curious] ¿Alguien más?",
            "[playful] ¡Los pulsadores están abiertos!",
            "[curious] ¿Alguien más quiere intentarlo?",
            "[snappy] ¡Sigue disponible!",
            "[playful] ¿Quién la quiere?",
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
        ),
        "es": (
            "[snappy] ¡Se acabó el tiempo!",
            "[mock disappointment] ¿Nadie? [pause] ¿Nadie nadita?",
            "[sighs] Se acabó el tiempo. [pause] La respuesta era...",
            "[playful] ¡Silencio en el estudio! [pause] La respuesta era...",
            "[snappy] ¡Tiempo! [pause] Era...",
            "[curious] ¿Nadie se anima? [pause] La respuesta era...",
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
        ),
        "es": (
            "[curious] Perdón, no te entendí. [pause] ¿Me lo repites?",
            "[playful] Otra vez, un poquito más alto.",
            "[curious] ¿Cómo? [pause] Repítemelo, porfa.",
        ),
    },
    "more_specific": {
        "en": (
            "[curious] Can you be more specific?",
            "[playful] Close... [pause] be more specific.",
            "[curious] Mm, which one exactly?",
            "[playful] You're in the neighborhood. [pause] More specific, please.",
        ),
        "es": (
            "[curious] ¿Puedes ser más específico?",
            "[playful] Cerca... [pause] sé más específico.",
            "[curious] Mm, ¿cuál exactamente?",
            "[playful] Vas por buen camino. [pause] Más específico, por favor.",
        ),
    },
    "overruled": {
        "en": (
            "[laughs] The room has spoken! [booming] It counts!",
            "[mock disappointment] Overruled by the humans... [playful] Fine, it counts.",
            "[booming] By popular vote... [pause] correct!",
            "[playful] Democracy wins. [excited] Points awarded!",
        ),
        "es": (
            "[laughs] ¡La sala ha hablado! [booming] ¡Vale!",
            "[mock disappointment] Me ganaron los humanos... [playful] Bueno, vale.",
            "[booming] Por votación popular... [pause] ¡correcto!",
            "[playful] Ganó la democracia. [excited] ¡Puntos para ustedes!",
        ),
    },
    "double": {
        "en": (
            "[gasps] [booming] A hidden square! Double or nothing!",
            "[dramatic] Oh-ho! [booming] Double or nothing!",
            "[excited] You found a secret square! [pause] Double or nothing.",
            "[hushed] Careful now... [booming] it's double or nothing!",
        ),
        "es": (
            "[gasps] [booming] ¡Una casilla escondida! ¡Doble o nada!",
            "[dramatic] ¡Epa! [booming] ¡Doble o nada!",
            "[excited] ¡Encontraron una casilla secreta! [pause] Doble o nada.",
            "[hushed] Cuidado... [booming] ¡es doble o nada!",
        ),
    },
    "steal": {
        "en": (
            "[gasps] [booming] A steal square! [playful] Get it right, and you win these points... [pause] and take "
            "the same from the team in the lead!",
            "[playful] Oh, this one's sneaky. [pause] Answer right, and you win the points AND steal them from the "
            "leaders.",
            "[dramatic] It's a steal! [playful] Get it right, and the team in the lead loses these points to you.",
        ),
        "es": (
            "[gasps] [booming] ¡Casilla de robo! [playful] Si aciertan, se ganan estos puntos... [pause] ¡y se los "
            "quitan al equipo que va ganando!",
            "[playful] Uy, esta es tramposa. [pause] Si aciertan, ganan los puntos Y se los roban al que va "
            "punteando.",
            "[dramatic] ¡Es un robo! [playful] Si aciertan, el equipo que va ganando les pasa estos puntos.",
        ),
    },
    "everyone": {
        "en": (
            "[excited] Everybody plays this one! [warmly] Type your answer on your phone.",
            "[playful] This one's for everyone. [pause] Answers on your phones!",
            "[booming] All hands on deck! [warmly] Everyone answers on the phone.",
        ),
        "es": (
            "[excited] ¡Esta la juegan todos! [warmly] Escriban su respuesta en el teléfono.",
            "[playful] Esta es para todo el mundo. [pause] ¡Respuestas en el teléfono!",
            "[booming] ¡Todos a bordo! [warmly] Cada quien responde en su teléfono.",
        ),
    },
    "lead_change": {
        "en": (
            "[gasps] We have a new leader!",
            "[excited] And just like that... [pause] the lead changes hands!",
            "[amazed] A new team on top! [playful] Things are getting spicy.",
            "[dramatic] The tables have turned!",
            "[playful] Look who's in first place now!",
        ),
        "es": (
            "[gasps] ¡Tenemos nuevo líder!",
            "[excited] Y así, sin más... [pause] ¡cambia el primer lugar!",
            "[amazed] ¡Un nuevo equipo en la cima! [playful] Esto se está poniendo bueno.",
            "[dramatic] ¡Se voltearon las cosas!",
            "[playful] ¡Miren quién va de primero ahora!",
        ),
    },
    "comeback": {
        "en": (
            "[excited] What a comeback!",
            "[amazed] They were way behind... [pause] and now look!",
            "[booming] Never count them out!",
            "[playful] From last place to the race! [excited] Love it.",
        ),
        "es": (
            "[excited] ¡Qué remontada!",
            "[amazed] Iban muy atrás... [pause] ¡y ahora mírenlos!",
            "[booming] ¡Nunca los den por muertos!",
            "[playful] ¡Del último lugar a la pelea! [excited] Me encanta.",
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
        ),
        "es": (
            "[booming] ¡Y se acabó el tablero! [pause] Pero todavía no terminamos...",
            "[excited] ¡No queda ni una casilla! [dramatic] Y eso solo puede significar una cosa...",
            "[playful] El tablero está limpio. [pause] Hora del gran final.",
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
        ),
        "es": (
            "[snappy] Treinta segundos. [pause] ¡Escriban su respuesta!",
            "[playful] Treinta segundos en el reloj... [pause] ¡ya!",
            "[snappy] Respuestas en el teléfono. [pause] ¡Treinta segundos!",
        ),
    },
    "reveal": {
        "en": (
            "[dramatic] Let's see what you wrote...",
            "[hushed] The moment of truth...",
            "[curious] Let's look at the answers... [pause] one by one.",
        ),
        "es": (
            "[dramatic] A ver qué escribieron...",
            "[hushed] El momento de la verdad...",
            "[curious] Vamos a ver las respuestas... [pause] una por una.",
        ),
    },
    "winner": {
        "en": (
            "[booming] And the winner of BIG BRAIN is...",
            "[dramatic] After all that... [long pause] our champion is...",
            "[excited] We have a winner! [pause] Congratulations to...",
            "[booming] Give it up for tonight's biggest brain...",
        ),
        "es": (
            "[booming] Y el ganador de BIG BRAIN es...",
            "[dramatic] Después de todo esto... [long pause] nuestro campeón es...",
            "[excited] ¡Tenemos ganador! [pause] Felicitaciones a...",
            "[booming] Un aplauso para el cerebro más grande de la noche...",
        ),
    },
    "tie": {
        "en": (
            "[gasps] A tie! [amazed] I did not see that coming.",
            "[playful] It's a tie! [pause] Everybody wins... [chuckles] or nobody does.",
        ),
        "es": (
            "[gasps] ¡Un empate! [amazed] Eso no me lo esperaba.",
            "[playful] ¡Empate! [pause] Todos ganan... [chuckles] o nadie gana.",
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
    """Return the lines that name a team: a buzz calls it out, its pick is announced, a win crowns it."""
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
