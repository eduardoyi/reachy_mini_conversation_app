"""What Sheeple's host says at fixed moments, in English and Venezuelan Spanish (docs/features/sheeple.md).

Sheeple's Richie is a survey-show host: he riffs on the flock's answers with disbelief, teasing, mock outrage and
suspense, always warm and kid-safe. Each moment has several takes so he rarely repeats himself, and they are short
because most play between reveals. Lines carry ElevenLabs audio tags in square brackets ("[playful]", "[pause]"),
from Big Brain's list (``TAGS``); ``shown`` strips them for the e-ink screen and the phones. Spanish is Venezuelan,
speaks to the room as "ustedes", calls the survey "cien gringos" and avoids words that change with a player's
gender. Clips are made once by ``scripts/big_brain_voice.py --set sheeple`` into
``game-data/sheeple/voice/<moment>/<language>-NN.wav``. Names, guesses, answers and counts are said live in the
same host voice, so no take here names a player or an answer: each one works after any of them.
"""

from typing import Final

from reachy_mini_conversation_app.games.big_brain_lines import TAGS, TEAMS, shown


__all__ = ["LINES", "TAGS", "TEAMS", "shown"]

_Lines = dict[str, tuple[str, ...]]

_MOMENTS: Final[dict[str, _Lines]] = {
    # The lobby: the game opens, the room joins, someone taps Start.
    "welcome": {
        "en": (
            "[booming] Welcome to SHEEPLE! [playful] Where the smartest one doesn't win... [pause] whoever thinks "
            "like the flock does. Scan the code on the screen to join!",
            "[booming] Ladies and gentlemen, lambs and rams... [excited] it's SHEEPLE! [playful] The smartest doesn't "
            "win here: whoever thinks like the flock does. Scan the code on the screen to play.",
            "[booming] Welcome to SHEEPLE! [playful] Tonight, being smart won't save you. [pause] Thinking like the "
            "flock will. Scan the code on the screen to join!",
            "[clears throat] [booming] Live from the living room... it's SHEEPLE! [playful] The smartest doesn't win: "
            "whoever thinks like the flock does. Scan the code on the screen to join.",
        ),
        "es": (
            "[booming] ¡Bienvenidos a SHEEPLE! [playful] Aquí no gana el más listo... [pause] gana el que piensa como "
            "el rebaño. ¡Escaneen el código de la pantalla para jugar!",
            "[booming] Damas y caballeros, ovejitas y carneros... [excited] ¡llegó SHEEPLE! [playful] Aquí no gana el "
            "más listo: gana el que piensa como el rebaño. Escaneen el código de la pantalla para jugar.",
            "[booming] ¡Bienvenidos a SHEEPLE! [playful] Esta noche ser un cráneo no les sirve de nada. [pause] Pensar "
            "como el rebaño, sí. ¡Escaneen el código de la pantalla para entrar!",
            "[clears throat] [booming] Desde la sala de la casa... ¡esto es SHEEPLE! [playful] Aquí no gana el más "
            "listo: gana el que piensa como el rebaño. Escaneen el código de la pantalla para jugar.",
        ),
    },
    "waiting": {
        "en": (
            "[warmly] When everyone's in, tap Start on your phone. [playful] The flock can wait... [pause] a little.",
            "[playful] Still rounding up the sheep... [pause] When everyone's in, tap Start on your phone.",
            "[curious] Is the whole flock here? [warmly] When everyone's in, tap Start on your phone.",
        ),
        "es": (
            "[warmly] Cuando estén todos, toquen Empezar en el teléfono. [playful] El rebaño espera... [pause] pero "
            "no mucho.",
            "[playful] Todavía estamos arreando ovejas... [pause] Cuando estén todos, toquen Empezar en el teléfono.",
            "[curious] ¿Llegó todo el rebaño? [warmly] Cuando estén todos, toquen Empezar en el teléfono.",
        ),
    },
    "begin": {
        "en": (
            "[excited] Here we go! [warmly] We asked a hundred people some questions, and you guess what they said. "
            "[playful] Some rounds everyone answers at once on the phone... [pause] others go team by team, out loud.",
            "[booming] Let's play! [warmly] A hundred people answered our questions. Your job: guess what they said. "
            "[playful] Sometimes you all type at once on your phones, sometimes teams take turns out loud.",
            "[excited] The flock is ready! [warmly] We surveyed a hundred people, and you guess their answers. "
            "[playful] Some rounds, everybody types on the phone at once. Others, team by team, out loud.",
        ),
        "es": (
            "[excited] ¡Arrancamos! [warmly] Les preguntamos cosas a cien gringos, y ustedes adivinan qué contestaron. "
            "[playful] Unas rondas las juegan todos a la vez en el teléfono... [pause] y otras por equipos, en voz "
            "alta.",
            "[booming] ¡A jugar! [warmly] Cien gringos respondieron nuestras preguntas, y ustedes adivinan qué "
            "dijeron. [playful] A veces escriben todos a la vez en el teléfono, a veces los equipos se turnan en voz "
            "alta.",
            "[excited] ¡El rebaño está listo! [warmly] Encuestamos a cien gringos, y ustedes adivinan sus respuestas. "
            "[playful] En unas rondas, todos escriben en el teléfono a la vez. En otras, equipo por equipo, en voz "
            "alta.",
        ),
    },
    # A board opens: what kind of round, the double, the question's lead-in.
    "flock_round": {
        "en": (
            "[excited] Flock round! [warmly] Everybody answers on the phone.",
            "[playful] This one's for the whole flock. [pause] Phones out, everybody types!",
            "[booming] Everybody plays! [playful] Type your guess on your phone.",
            "[playful] Heads down, phones up... [pause] everybody answers this one!",
        ),
        "es": (
            "[excited] ¡Ronda del rebaño! [warmly] Todos responden en el teléfono.",
            "[playful] Esta la juega todo el rebaño. [pause] ¡Teléfono en mano, todos escriben!",
            "[booming] ¡Juegan todos! [playful] Escriban su respuesta en el teléfono.",
            "[playful] Cabeza abajo, teléfono arriba... [pause] ¡esta la responden todos!",
        ),
    },
    "team_round": {
        "en": (
            "[booming] Team round! [warmly] Teams take turns, out loud.",
            "[playful] Now it's team against team. [pause] Say it loud, say it proud.",
            "[excited] Team by team, out loud! [playful] Three misses and the board closes.",
            "[dramatic] Teams, huddle up... [pause] this round goes team by team, out loud.",
        ),
        "es": (
            "[booming] ¡Ronda por equipos! [warmly] Se turnan, en voz alta.",
            "[playful] Ahora es equipo contra equipo. [pause] Bien duro y sin pena.",
            "[excited] ¡Equipo por equipo, en voz alta! [playful] Tres fallos y se cierra el tablero.",
            "[dramatic] Equipos, júntense... [pause] esta ronda va por turnos, en voz alta.",
        ),
    },
    "double": {
        "en": (
            "[dramatic] Last round... [pause] [booming] and it counts DOUBLE!",
            "[hushed] This is it, the final board. [excited] Every point counts double!",
            "[booming] Final round, double points! [playful] Anybody can still win this.",
            "[gasps] The last board! [booming] Double points... [pause] [playful] no pressure.",
        ),
        "es": (
            "[dramatic] Última ronda... [pause] [booming] ¡y vale DOBLE!",
            "[hushed] Llegamos al último tablero. [excited] ¡Cada punto vale doble!",
            "[booming] ¡Ronda final, puntos dobles! [playful] Aquí todavía gana cualquiera.",
            "[gasps] ¡El último tablero! [booming] Puntos dobles... [pause] [playful] sin presión, ¿eh?",
        ),
    },
    "asked": {
        "en": (
            "[curious] We asked a hundred people:",
            "[warmly] A hundred people answered this one:",
            "[playful] We went out and asked a hundred people...",
            "[dramatic] One hundred people. [pause] One question:",
            "[curious] Here's what we asked a hundred people:",
            "[playful] A hundred brave strangers were asked...",
        ),
        "es": (
            "[curious] Le preguntamos a cien gringos:",
            "[warmly] Cien gringos respondieron esto:",
            "[playful] Salimos a la calle y le preguntamos a cien gringos...",
            "[dramatic] Cien gringos. [pause] Una pregunta:",
            "[curious] Esto fue lo que le preguntamos a cien gringos:",
            "[playful] A cien gringos valientes les preguntamos...",
        ),
    },
    # Flock rounds: everyone types, then the reveal from least to most popular.
    "type_it": {
        "en": (
            "[warmly] Type on your phone what you think most people said.",
            "[playful] Don't be original! [pause] Type what MOST people said.",
            "[curious] What did the flock say? [warmly] Type it on your phone.",
            "[playful] Think like a sheep... [pause] and type the most popular answer on your phone.",
        ),
        "es": (
            "[warmly] Escriban en el teléfono lo que creen que dijo la mayoría.",
            "[playful] ¡Nada de ser originales! [pause] Escriban lo que dijo la MAYORÍA.",
            "[curious] ¿Qué dijo el rebaño? [warmly] Escríbanlo en el teléfono.",
            "[playful] Piensen como oveja... [pause] y escriban en el teléfono la respuesta más popular.",
        ),
    },
    "time_up": {
        "en": (
            "[snappy] Time's up! [curious] Let's see what the flock said.",
            "[snappy] Phones down! [playful] Let's see who thinks like a sheep.",
            "[booming] Time! [dramatic] Let's see what the hundred said...",
            "[snappy] That's time! [hushed] Let's hear from the flock.",
            "[playful] Pencils down... [pause] well, phones. [curious] Let's see the answers!",
        ),
        "es": (
            "[snappy] ¡Tiempo! [curious] A ver qué dijo el rebaño.",
            "[snappy] ¡Suelten los teléfonos! [playful] A ver quién piensa como oveja.",
            "[booming] ¡Tiempo! [dramatic] A ver qué dijeron los cien gringos...",
            "[snappy] ¡Se acabó! [hushed] Que hable el rebaño.",
            "[playful] Lápices abajo... [pause] bueno, teléfonos. [curious] ¡Vamos a ver!",
        ),
    },
    "top_suspense": {
        "en": (
            "[whispers] And now... [pause] the number one answer.",
            "[hushed] The most popular answer... [long pause] is...",
            "[whispers] Lean in. [pause] This is the big one.",
            "[hushed] What did most of the flock say? [pause] Here it is...",
            "[whispers] The top answer... [pause] did anybody get it?",
            "[hushed] Drumroll, please... [long pause] number one...",
        ),
        "es": (
            "[whispers] Y ahora... [pause] la respuesta número uno.",
            "[hushed] La respuesta más popular... [long pause] es...",
            "[whispers] Acérquense. [pause] Esta es la grande.",
            "[hushed] ¿Qué dijo la mayoría del rebaño? [pause] Aquí está...",
            "[whispers] La respuesta reina... [pause] ¿alguien la pegó?",
            "[hushed] Redoble de tambores... [long pause] la número uno...",
        ),
    },
    # After the live "{names} said it!".
    "flock_hit": {
        "en": (
            "[playful] Baa! Good sheep.",
            "[proud] A true member of the flock.",
            "[excited] Right there with the crowd!",
            "[chuckles] Baa-rilliant.",
            "[playful] Great minds think alike... [pause] and so do sheep.",
            "[warmly] The flock approves.",
            "[playful] Thinking like a hundred strangers... [pause] a little worrying.",
            "[excited] Points for following the crowd!",
        ),
        "es": (
            "[playful] ¡Bee! Qué buena oveja.",
            "[proud] Eso es pensar como rebaño.",
            "[excited] ¡Igualito que los gringos!",
            "[chuckles] ¡Qué nota! [playful] Bee.",
            "[playful] Las mentes brillantes piensan igual... [pause] y las ovejas también.",
            "[warmly] El rebaño aprueba.",
            "[playful] Piensa igualito que cien gringos... [pause] eso preocupa un poquito.",
            "[excited] ¡Puntos por seguir a la manada!",
        ),
    },
    "lone_top": {
        "en": (
            "[amazed] The only one who found it! [playful] Lead sheep.",
            "[proud] All alone at the top. [playful] Everyone else, take notes.",
            "[gasps] Just one of you found the big one!",
            "[playful] The only one here who speaks fluent flock.",
            "[amazed] Alone at number one. [chuckles] The shepherd's favorite.",
        ),
        "es": (
            "[amazed] ¡La única persona que la pegó! [playful] Oveja líder.",
            "[proud] Sin compañía en la cima. [playful] Los demás, tomen nota.",
            "[gasps] ¡Una sola persona encontró la grande!",
            "[playful] La única persona aquí que habla rebaño fluido.",
            "[amazed] Número uno, y sin compañía. [chuckles] La oveja del pastor.",
        ),
    },
    "low_hit": {
        "en": (
            "[playful] Barely... [pause] but it counts!",
            "[chuckles] Just a handful of people. [playful] Still counts!",
            "[curious] Only a few said that... [pause] but a few is enough.",
            "[playful] A small flock, but a flock.",
            "[chuckles] Scraping by! [warmly] Points are points.",
        ),
        "es": (
            "[playful] Por un pelito... [pause] ¡pero cuenta!",
            "[chuckles] Poquitos gringos dijeron eso. [playful] ¡Pero cuenta!",
            "[curious] Muy pocos dijeron eso... [pause] pero alcanza.",
            "[playful] Un rebaño chiquitico, pero rebaño.",
            "[chuckles] ¡Raspando! [warmly] Puntos son puntos.",
        ),
    },
    # The number-one answer, just revealed, and nobody typed it.
    "top_missed": {
        "en": (
            "[disbelief] The most popular answer... [pause] and not one of you!",
            "[gasps] Nobody? [mock disappointment] Most of America said it!",
            "[disbelief] The whole flock said that... [pause] and this room said nothing.",
            "[mock disappointment] The number one answer, and you all walked right past it.",
            "[sighs] It was right there. [pause] The biggest answer on the board.",
            "[disbelief] Not one sheep in this living room? [chuckles] Rebels, all of you.",
        ),
        "es": (
            "[disbelief] La respuesta más popular... [pause] ¡y ni uno de ustedes!",
            "[gasps] ¿Nadie? [mock disappointment] ¡Si eso lo dijo medio mundo!",
            "[disbelief] Todo el rebaño dijo eso... [pause] y en esta sala, nada.",
            "[mock disappointment] La número uno, y le pasaron por al lado.",
            "[sighs] Estaba ahí mismito. [pause] La más grande del tablero.",
            "[disbelief] ¿Ni una oveja en esta sala? [chuckles] Puros rebeldes.",
        ),
    },
    "whole_flock": {
        "en": (
            "[amazed] Everyone said the same thing! [booming] A perfect flock!",
            "[laughs] Look at you, all thinking the same thing. [proud] Perfect flock.",
            "[amazed] Every single one of you! [playful] Baa, baa, baa.",
            "[proud] Total agreement. [playful] I've never been prouder of a herd.",
        ),
        "es": (
            "[amazed] ¡Todos dijeron lo mismo! [booming] ¡Rebaño perfecto!",
            "[laughs] Mírense, todos pensando igualito. [proud] Rebaño perfecto.",
            "[amazed] ¡Todos, todos, todos! [playful] Bee, bee, bee.",
            "[proud] Acuerdo total. [playful] Nunca había estado tan orgulloso de un rebaño.",
        ),
    },
    # After the live "{name} typed: {guess}." for a guess that matched nothing on the board.
    "black_sheep": {
        "en": (
            "[disbelief] Not one person out of a hundred said that.",
            "[chuckles] I'm going to need you to explain that one later.",
            "[curious] Interesting... [pause] the flock did not agree.",
            "[playful] A true black sheep. [pause] I respect it.",
            "[disbelief] Where did that come from? [laughs] I love it.",
            "[mock disappointment] Nobody. [pause] Not a single person.",
            "[playful] Bold. Brave. [pause] Completely alone.",
            "[laughs] The hundred people are very confused right now.",
        ),
        "es": (
            "[disbelief] Ni uno de los cien gringos dijo eso.",
            "[chuckles] Eso me lo vas a tener que explicar después.",
            "[curious] Interesante... [pause] el rebaño no estuvo de acuerdo.",
            "[playful] Una oveja negra de verdad. [pause] La respeto.",
            "[disbelief] ¿Y eso de dónde salió? [laughs] Me encanta.",
            "[mock disappointment] Nadie. [pause] Ni un solo gringo.",
            "[playful] Valiente. Original. [pause] Y sin compañía.",
            "[laughs] Los cien gringos están burda de confundidos ahorita.",
        ),
    },
    "nobody_hit": {
        "en": (
            "[mock disappointment] Nobody thought like the flock this time.",
            "[sighs] Not one sheep in the house. [playful] A room full of rebels.",
            "[disbelief] Nobody? [pause] The flock is very disappointed.",
            "[playful] Nothing for anyone! [chuckles] Very original... too original.",
        ),
        "es": (
            "[mock disappointment] Nadie pensó como el rebaño esta vez.",
            "[sighs] Ni una oveja en esta casa. [playful] Pura gente original.",
            "[disbelief] ¿Nadie? [pause] El rebaño está muy decepcionado.",
            "[playful] ¡Nada para nadie! [chuckles] Qué originales... demasiado originales.",
        ),
    },
    # Team rounds: before the live "{answer}, with {count}.".
    "team_hit": {
        "en": (
            "[excited] It's up there!",
            "[booming] Survey says... [pause] yes!",
            "[excited] On the board!",
            "[playful] The flock agrees!",
            "[booming] Yes! It's there!",
            "[proud] Nice! The flock said it too!",
            "[amazed] Ooh, it's there!",
            "[booming] Ding! [excited] Right on the board!",
            "[playful] The hundred people back you up!",
            "[excited] Yes, yes! It's there!",
        ),
        "es": (
            "[excited] ¡Está!",
            "[booming] La encuesta dice... [pause] ¡sí!",
            "[excited] ¡En el tablero!",
            "[playful] ¡El rebaño está de acuerdo!",
            "[booming] ¡Sí, señor! ¡Está!",
            "[proud] ¡Épale! Los gringos también lo dijeron.",
            "[amazed] ¡Uy, sí está!",
            "[booming] ¡Tin! [excited] ¡Directo al tablero!",
            "[playful] ¡Los cien gringos los respaldan!",
            "[excited] ¡Sí, sí! ¡Está!",
        ),
    },
    "team_top": {
        "en": (
            "[gasps] [booming] The number one answer!",
            "[booming] Top of the board!",
            "[amazed] That's the big one! [excited] Number one!",
            "[excited] Straight to the top! [booming] Number one!",
            "[proud] The flock's favorite!",
        ),
        "es": (
            "[gasps] [booming] ¡La respuesta número uno!",
            "[booming] ¡Lo más alto del tablero!",
            "[amazed] ¡Esa es la grande! [excited] ¡Número uno!",
            "[excited] ¡Directo a la cima! [booming] ¡La número uno!",
            "[proud] ¡La favorita del rebaño!",
        ),
    },
    # After the live answer, when only a handful of people said it.
    "team_low": {
        "en": (
            "[chuckles] Not many people, but they count!",
            "[playful] Tiny, but it's on the board.",
            "[curious] Only a handful said it. [warmly] We'll take it.",
            "[chuckles] Barely made the list! [playful] Still counts.",
            "[playful] A few brave souls agree with you.",
        ),
        "es": (
            "[chuckles] Poquitos, ¡pero cuentan!",
            "[playful] Chiquitica, pero está en el tablero.",
            "[curious] Solo un puñadito lo dijo. [warmly] Igual sirve.",
            "[chuckles] ¡Entró raspando! [playful] Pero cuenta.",
            "[playful] Unos cuantos gringos valientes piensan como ustedes.",
        ),
    },
    "already": {
        "en": (
            "[playful] Were you listening? [pause] That one's already up there.",
            "[chuckles] Already on the board! [playful] Try another.",
            "[curious] Hmm... [pause] that one's already there.",
            "[mock disappointment] We already have that one. [playful] Keep up!",
            "[playful] Déjà vu! [pause] That's already on the board.",
        ),
        "es": (
            "[playful] ¿Estaban oyendo? [pause] Esa ya está en el tablero.",
            "[chuckles] ¡Esa ya salió! [playful] Prueben con otra.",
            "[curious] Mmm... [pause] esa ya está.",
            "[mock disappointment] Esa ya la tenemos. [playful] ¡Pilas!",
            "[playful] ¡Esa ya la dijeron! [pause] Ya está en el tablero.",
        ),
    },
    "not_there": {
        "en": (
            "[mock disappointment] Not there!",
            "[disbelief] Nobody said that. [pause] Nobody!",
            "[sighs] Ooh... not on the board.",
            "[playful] I like it. [pause] The flock didn't.",
            "[disbelief] Really? [mock disappointment] Not there.",
            "[chuckles] The hundred people are shaking their heads.",
            "[mock disappointment] Nope! [playful] Bold choice, though.",
            "[curious] Hmm... [pause] the survey says... no.",
            "[playful] Nice try, but the flock went another way.",
            "[disbelief] Not even one person? [sighs] Not there.",
        ),
        "es": (
            "[mock disappointment] ¡No está!",
            "[disbelief] Nadie dijo eso. [pause] ¡Nadie!",
            "[sighs] Uy... no está en el tablero.",
            "[playful] A mí me gusta. [pause] Al rebaño, no.",
            "[disbelief] ¿En serio? [mock disappointment] No está.",
            "[chuckles] Los cien gringos están negando con la cabeza.",
            "[mock disappointment] ¡Nop! [playful] Pero qué valentía.",
            "[curious] Mmm... [pause] la encuesta dice... no.",
            "[playful] Buen intento, pero el rebaño se fue por otro lado.",
            "[disbelief] ¿Ni un solo gringo? [sighs] No está.",
        ),
    },
    "miss_1": {
        "en": (
            "[softly] That's one miss.",
            "[playful] Strike one. [pause] Careful now.",
            "[warmly] One miss. [playful] Shake it off.",
        ),
        "es": (
            "[softly] Ese es un fallo.",
            "[playful] Primer fallo. [pause] Cuidadito.",
            "[warmly] Un fallo. [playful] Tranquilos, sacúdanselo.",
        ),
    },
    "miss_2": {
        "en": (
            "[hushed] That's two misses. [pause] One more and the board is over.",
            "[dramatic] Two misses! [playful] Careful... one more and it's over.",
            "[sighs] Two down. [hushed] One more miss ends the board.",
        ),
        "es": (
            "[hushed] Van dos fallos. [pause] Uno más y se acaba el tablero.",
            "[dramatic] ¡Dos fallos! [playful] Cuidado... uno más y se acabó.",
            "[sighs] Van dos. [hushed] Un fallo más y se cierra el tablero.",
        ),
    },
    "miss_3": {
        "en": (
            "[mock disappointment] Three misses! [pause] That's the board.",
            "[sighs] And that's three. [softly] This board is closed.",
            "[dramatic] Third miss! [pause] The flock wins this one.",
        ),
        "es": (
            "[mock disappointment] ¡Tres fallos! [pause] Se acabó el tablero.",
            "[sighs] Y van tres. [softly] Este tablero se cerró.",
            "[dramatic] ¡Tercer fallo! [pause] Esta se la llevó el rebaño.",
        ),
    },
    "no_answer": {
        "en": (
            "[curious] I didn't hear you... [mock disappointment] that's a miss.",
            "[sighs] Silence! [pause] That counts as a miss.",
            "[playful] Speak up next time! [softly] That's a miss.",
            "[curious] Hello? [pause] Nothing? [mock disappointment] Miss.",
        ),
        "es": (
            "[curious] No te oí... [mock disappointment] cuenta como fallo.",
            "[sighs] ¡Silencio total! [pause] Eso cuenta como fallo.",
            "[playful] ¡La próxima, más duro! [softly] Es un fallo.",
            "[curious] ¿Aló? [pause] ¿Nada? [mock disappointment] Fallo.",
        ),
    },
    "too_slow": {
        "en": (
            "[snappy] Time's up! [mock disappointment] The team ran out of time.",
            "[playful] Too slow! [pause] The flock waits for no one.",
            "[sighs] Tick tock... [pause] that's time.",
            "[mock disappointment] Nobody tapped Answer! [pause] Time's up.",
        ),
        "es": (
            "[snappy] ¡Tiempo! [mock disappointment] Al equipo se le acabó el tiempo.",
            "[playful] ¡Muy lentos! [pause] El rebaño no espera a nadie.",
            "[sighs] Tic tac... [pause] se acabó el tiempo.",
            "[mock disappointment] ¡Nadie tocó Responder! [pause] Se acabó el tiempo.",
        ),
    },
    # A board closes.
    "left": {
        "en": (
            "[curious] Let's see what got away...",
            "[playful] Here's what the flock said and you didn't...",
            "[hushed] The ones nobody found...",
            "[curious] Still hiding on the board...",
        ),
        "es": (
            "[curious] A ver qué se nos escapó...",
            "[playful] Esto dijo el rebaño y ustedes no...",
            "[hushed] Las que nadie encontró...",
            "[curious] Todavía escondidas en el tablero...",
        ),
    },
    "cleared": {
        "en": (
            "[booming] Board cleared! [excited] You found every single answer!",
            "[amazed] Every answer! [proud] The flock bows to you.",
            "[excited] Clean sweep! [playful] Not one answer got away.",
            "[booming] They're all up there! [laughs] You read the flock's mind.",
        ),
        "es": (
            "[booming] ¡Tablero limpio! [excited] ¡Encontraron todas las respuestas!",
            "[amazed] ¡Todas! [proud] El rebaño se rinde ante ustedes.",
            "[excited] ¡Barrida total! [playful] No se escapó ni una.",
            "[booming] ¡Están todas! [laughs] Le leyeron la mente al rebaño.",
        ),
    },
    # The scores after a board.
    "lead_change": {
        "en": (
            "[gasps] We have a new leader!",
            "[excited] And just like that... [pause] the lead changes hands!",
            "[dramatic] A new team leads the flock!",
            "[playful] Look who's out in front now!",
            "[amazed] The lead has changed! [playful] Things are getting woolly.",
            "[playful] There's a new shepherd in town!",
        ),
        "es": (
            "[gasps] ¡Tenemos nuevo líder!",
            "[excited] Y así, sin más... [pause] ¡cambia el primer lugar!",
            "[dramatic] ¡Un nuevo equipo guía al rebaño!",
            "[playful] ¡Miren quién va de primero ahora!",
            "[amazed] ¡Cambió el liderato! [playful] Esto se está poniendo bueno.",
            "[playful] ¡Hay nuevo pastor en el pueblo!",
        ),
    },
    "comeback": {
        "en": (
            "[excited] What a comeback!",
            "[amazed] They were way behind... [pause] and now look!",
            "[booming] Never count out the underdog... [pause] [playful] or the under-sheep!",
            "[playful] From the back of the flock to the front! [excited] Love it.",
        ),
        "es": (
            "[excited] ¡Qué remontada!",
            "[amazed] Iban burda de atrás... [pause] ¡y ahora mírenlos!",
            "[booming] ¡Nunca den a nadie por perdido!",
            "[playful] ¡Del final del rebaño a la cabeza! [excited] Qué nota.",
        ),
    },
    "runaway": {
        "en": (
            "[amazed] One team is running away with this!",
            "[playful] Somebody catch them! [pause] They're way out in front.",
            "[chuckles] Is anyone else playing? [playful] Asking for the other teams.",
            "[dramatic] A huge lead... [pause] [playful] the rest of the flock had better wake up!",
        ),
        "es": (
            "[amazed] ¡Un equipo se está escapando!",
            "[playful] ¡Que alguien los alcance! [pause] Van burda de adelante.",
            "[chuckles] ¿Alguien más está jugando? [playful] Pregunto por los otros equipos.",
            "[dramatic] Una ventaja enorme... [pause] [playful] ¡el resto del rebaño, despierten!",
        ),
    },
    "close_game": {
        "en": (
            "[hushed] It's close... [pause] really close.",
            "[excited] Just a few points between them! [playful] Anybody's game.",
            "[dramatic] Neck and neck! [pause] Every answer counts now.",
            "[playful] Too close to call! [chuckles] My circuits are sweating.",
        ),
        "es": (
            "[hushed] Está pegado... [pause] pegadísimo.",
            "[excited] ¡Unos poquitos puntos los separan! [playful] Aquí gana cualquiera.",
            "[dramatic] ¡Cabeza a cabeza! [pause] Ahora cada respuesta cuenta.",
            "[playful] ¡Está demasiado parejo! [chuckles] Me están sudando los circuitos.",
        ),
    },
    # The end: the winning team (its clip follows), a tie, the top sheep, goodbye.
    "winner": {
        "en": (
            "[booming] And the winner of SHEEPLE is...",
            "[dramatic] The team that thinks most like the flock... [long pause] is...",
            "[excited] We have a winner! [pause] Congratulations to...",
            "[hushed] After all that baa-ing... [booming] the champions are...",
        ),
        "es": (
            "[booming] Y el ganador de SHEEPLE es...",
            "[dramatic] El equipo que más piensa como el rebaño... [long pause] es...",
            "[excited] ¡Tenemos ganador! [pause] Felicitaciones a...",
            "[hushed] Después de tanto bee-bee... [booming] los campeones son...",
        ),
    },
    "tie": {
        "en": (
            "[gasps] A tie! [amazed] The flock couldn't decide.",
            "[playful] It's a tie! [pause] Everybody wins... [chuckles] or nobody does.",
            "[amazed] Dead even! [warmly] Share the glory, teams.",
        ),
        "es": (
            "[gasps] ¡Empate! [amazed] El rebaño no se pudo decidir.",
            "[playful] ¡Empate! [pause] Todos ganan... [chuckles] o nadie gana.",
            "[amazed] ¡Igualitos! [warmly] Compartan la gloria, equipos.",
        ),
    },
    "top_sheep": {
        "en": (
            "[proud] And the top sheep, who thought most like the flock:",
            "[playful] And tonight's most sheep-like player...",
            "[booming] A special award for the most flock-minded player in the room...",
            "[warmly] And the one who thought most like a hundred strangers...",
        ),
        "es": (
            "[proud] Y la oveja mayor, la que más pensó como el rebaño:",
            "[playful] Y la oveja más oveja de la noche...",
            "[booming] Un premio especial para la mente más rebaño de esta sala...",
            "[warmly] Y quien más pensó como cien gringos...",
        ),
    },
    "goodbye": {
        "en": (
            "[warmly] Thanks for playing SHEEPLE! [playful] Baa!",
            "[proud] What a flock. [warmly] Good night, everybody! [playful] Baa.",
            "[booming] This has been SHEEPLE! [playful] Stay original... [pause] just not during the game. Baa!",
            "[warmly] Thanks for playing, everyone. [playful] The flock will be back. Baa!",
        ),
        "es": (
            "[warmly] ¡Gracias por jugar SHEEPLE! [playful] ¡Bee!",
            "[proud] Qué rebaño tan bueno. [warmly] ¡Buenas noches a todos! [playful] Bee.",
            "[booming] ¡Esto fue SHEEPLE! [playful] Sean originales... [pause] pero no durante el juego. ¡Bee!",
            "[warmly] Gracias por jugar, panas. [playful] El rebaño volverá. ¡Bee!",
        ),
    },
}


def _team_lines() -> dict[str, _Lines]:
    """Return the lines that name a team: its turn in a team round, and a win that crowns it."""
    lines: dict[str, _Lines] = {}
    for key, (en, es) in TEAMS.items():
        lines[f"turn_{key}"] = {
            "en": (
                f"[warmly] {en.capitalize()} team, your turn. [pause] Tap Answer on your phone and tell me.",
                f"[playful] {en.capitalize()} team, you're up! [pause] Tap Answer and say it loud.",
                f"[curious] {en.capitalize()} team... [pause] what did the flock say? Tap Answer on your phone.",
            ),
            "es": (
                f"[warmly] Equipo {es}, les toca. [pause] Toquen Responder en el teléfono y díganmelo.",
                f"[playful] ¡Equipo {es}, van ustedes! [pause] Toquen Responder y díganlo duro.",
                f"[curious] Equipo {es}... [pause] ¿qué dijo el rebaño? Toquen Responder en el teléfono.",
            ),
        }
        lines[f"winner_{key}"] = {
            "en": (f"[booming] The {en.upper()} TEAM!", f"[excited] Team {en}! [booming] Champions of the flock!"),
            "es": (f"[booming] ¡El EQUIPO {es.upper()}!", f"[excited] ¡Equipo {es}! [booming] ¡Campeones del rebaño!"),
        }
    return lines


# Every moment's takes, by language.
LINES: Final[dict[str, _Lines]] = {**_MOMENTS, **_team_lines()}
