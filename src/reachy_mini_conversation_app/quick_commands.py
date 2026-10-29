"""Answer everyday requests without a cloud conversation, the way a smart speaker does.

"Reachy, turn off the lights", "pause", "what time is it?", "qué tiempo hace?": the local transcript
of the request is matched against a small multilingual grammar (English, Spanish, Italian,
Portuguese). A match runs the existing tool (home_lights, spotify_control, tv) or answers from the
clock or Open-Meteo, and Reachy replies with a short sentence in the request's language. Anything
else, or anything the grammar is unsure of, returns ``None`` and goes to the Gemini conversation as
before. Measured 2026-10-18 before this module: about 8-9 s from the wake word to Reachy's answer,
most of it opening the Gemini session and its thinking.
"""

from __future__ import annotations
import re
import asyncio
import logging
import unicodedata
from typing import Any, Final, Callable
from datetime import datetime
from dataclasses import field, replace, dataclass
from collections.abc import Awaitable

from reachy_mini_conversation_app.maribel import RETRY_PAUSE_S, POST_TIMEOUT_S, FIRST_REPLY_WAIT_S
from reachy_mini_conversation_app.weather import Place, WeatherError, WeatherService, describe
from reachy_mini_conversation_app.whatsapp import SEND_DEADLINE_S, SendResult
from reachy_mini_conversation_app.voice_notes import encode_voice_note
from reachy_mini_conversation_app.photographer import FACES, SHARE_TIMEOUT_S, FaceFinder, take_photo, take_and_share
from reachy_mini_conversation_app.whatsapp_contacts import Contact, find_contact, load_contacts
from reachy_mini_conversation_app.tools.play_emotion import queue_intent_move


logger = logging.getLogger(__name__)

LANGUAGES: Final[tuple[str, ...]] = ("en", "es", "it", "pt")
# A request longer than this is a conversation, not a command.
MAX_COMMAND_WORDS: Final[int] = 14
SPOTIFY_VOLUME_STEP: Final[int] = 5


@dataclass(frozen=True)
class QuickCommand:
    """One recognized request."""

    kind: str
    language: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class QuickReply:
    """What Reachy says, and what runs after he says it (its own optional reply)."""

    text: str | None
    language: str
    kind: str
    sleep: bool = False
    after: Callable[[], Awaitable[str | None]] | None = None
    # How long ``after`` may take; None for the usual quick-command bound.
    after_timeout_s: float | None = None
    # Acts on the request word for word: a request clip cut off mid-sentence goes to the conversation instead.
    whole_request: bool = False
    # Spoken after ``text`` and before ``after``, one every ``beat_s`` (a countdown).
    beats: tuple[str, ...] = ()
    beat_s: float = 1.0


# ---------------------------------------------------------------------------------------------
# Text normalization


def normalize(text: str) -> str:
    """Lowercase, drop accents and punctuation ("¿Qué hora es?" -> "que hora es")."""
    folded = unicodedata.normalize("NFKD", text.casefold()).encode("ascii", "ignore").decode("ascii")
    folded = re.sub(r"['\u2019`]", "", folded).replace("%", " percent ")
    return " ".join(re.sub(r"[^\w\s]", " ", folded).split())


_PREFIX = re.compile(
    r"^(?:(?:hey|hi|ok|okay|so|and|please|now|can you|could you|would you|will you|i want you to|"
    r"oye|eh|vale|bueno|y|por favor|puedes|podrias|me puedes|quiero que|"
    r"ehi|allora|e|per favore|puoi|potresti|mi puoi|"
    r"olha|entao|podes|pode|voce pode|consegues)\s+)+"
)
_SUFFIX = re.compile(
    r"(?:\s+(?:please|now|for me|thanks|thank you|right now|"
    r"por favor|ahora|gracias|ya|porfa|"
    r"per favore|adesso|ora|grazie|"
    r"agora|obrigado|obrigada|ja))+$"
)


def strip_politeness(text: str) -> str:
    """Remove "please", "can you", "por favor"... around a normalized command."""
    return _SUFFIX.sub("", _PREFIX.sub("", text)).strip()


_SPANISH_MARKERS = frozenset({"el", "la", "los", "las", "del", "que", "es", "hace", "cancion", "luces", "y", "hoy"})
_PORTUGUESE_MARKERS = frozenset({"o", "a", "os", "as", "da", "do", "das", "dos", "na", "no", "luzes", "sao", "hoje"})
_ITALIAN_MARKERS = frozenset({"il", "lo", "le", "gli", "della", "delle", "che", "luci", "canzone", "sono"})


# ---------------------------------------------------------------------------------------------
# Grammar

_TIME = {
    "en": r"(?:what time is it|whats the time|what is the time|tell me the time|do you know what time it is|"
    r"what time is it now|whats the time now)",
    "es": r"(?:que hora es|que horas son|la hora|dime la hora|me dices la hora|sabes que hora es)",
    "it": r"(?:che ore sono|che ora e|lora|dimmi lora|sai che ore sono|mi dici lora)",
    "pt": r"(?:que horas sao|que horas e|as horas|diz me as horas|sabes que horas sao)",
}
_DATE = {
    "en": r"(?:what day is it(?: today)?|what day is today|whats the date(?: today)?|what is the date(?: today)?|"
    r"whats todays date|what is todays date|whats today|what is today)",
    "es": r"(?:que dia es(?: hoy)?|que fecha es(?: hoy)?|a que estamos(?: hoy)?|a cuanto estamos(?: hoy)?)",
    "it": r"(?:che giorno e(?: oggi)?|che data e(?: oggi)?|quanti ne abbiamo(?: oggi)?)",
    "pt": r"(?:que dia e(?: hoje)?|que data e(?: hoje)?|em que dia estamos)",
}
_WHEN = r"(?:\s+(?P<when>today|tomorrow|now|outside|hoy|manana|ahora|fuera|oggi|domani|fuori|hoje|amanha|la fora))?"
_WEATHER = {
    "en": [
        rf"(?:whats|what is|hows|how is|what s)?\s*the weather(?: like)?(?: going to be(?: like)?)?{_WHEN}"
        rf"(?:\s+in\s+(?P<place>[a-z ]+?))?{_WHEN.replace('when', 'when2')}",
        rf"(?:whats|what is)?\s*the forecast{_WHEN}(?:\s+for\s+(?P<place>[a-z ]+?))?",
        rf"what will the weather be(?: like)?{_WHEN}(?:\s+in\s+(?P<place>[a-z ]+?))?",
        r"(?:weather|forecast)(?:\s+(?P<when>today|tomorrow))?",
        r"how (?:hot|cold|warm) is it(?: outside)?(?: today)?",
        r"whats the temperature(?: outside)?|what is the temperature(?: outside)?",
    ],
    "es": [
        rf"(?:como esta|como va a estar|que tal) el tiempo{_WHEN}(?:\s+en\s+(?P<place>[a-z ]+?))?"
        rf"{_WHEN.replace('when', 'when2')}",
        rf"que tiempo (?:hace|hara|va a hacer){_WHEN}(?:\s+en\s+(?P<place>[a-z ]+?))?{_WHEN.replace('when', 'when2')}",
        r"(?:que temperatura hace|cuantos grados hace|hace frio|hace calor)(?: fuera)?(?: hoy)?",
        r"(?:el tiempo|el pronostico)(?: de)? (?:hoy|manana)|el pronostico",
        rf"(?:(?:como esta|que tal) el |el )?clima{_WHEN}|el tiempo",
    ],
    "it": [
        rf"che tempo (?:fa|fara){_WHEN}(?:\s+a\s+(?P<place>[a-z ]+?))?{_WHEN.replace('when', 'when2')}",
        rf"(?:come il tempo|il meteo|meteo){_WHEN}",
        r"(?:che temperatura ce|quanti gradi ci sono|fa freddo|fa caldo)(?: fuori)?(?: oggi)?",
    ],
    "pt": [
        rf"(?:como esta|como vai estar|como vai ficar) o tempo{_WHEN}(?:\s+em\s+(?P<place>[a-z ]+?))?"
        rf"{_WHEN.replace('when', 'when2')}",
        rf"que tempo (?:faz|vai fazer){_WHEN}(?:\s+em\s+(?P<place>[a-z ]+?))?{_WHEN.replace('when', 'when2')}",
        r"(?:qual e a temperatura|quantos graus estao|esta frio|esta calor)(?: la fora)?(?: hoje)?",
        r"(?:a previsao do tempo|a previsao)(?: para)?(?: hoje| amanha)?",
    ],
}
_RAIN = {
    "en": rf"(?:(?:is it|will it|is it going to|does it look like its going to) rain(?:ing)?|"
    rf"do i need an umbrella|any rain){_WHEN}",
    "es": rf"(?:va a llover|llueve|esta lloviendo|llovera|necesito paraguas){_WHEN}",
    "it": rf"(?:piove|piovera|sta piovendo|serve lombrello){_WHEN}",
    "pt": rf"(?:vai chover|chove|esta a chover|preciso de guarda chuva){_WHEN}",
}
_SLEEP = {
    "en": r"(?:go to sleep|go to bed|time to sleep|good night|goodnight|take a nap)",
    "es": r"(?:a dormir|vete a dormir|ve a dormir|duermete|buenas noches)",
    "it": r"(?:vai a dormire|a nanna|buonanotte|buona notte|vai a nanna)",
    "pt": r"(?:vai dormir|boa noite|vai para a cama)",
}
_CANCEL = {
    "en": r"(?:never mind|nevermind|nothing|cancel|forget it|no thanks|nothing thanks|not you|sorry|my bad|"
    r"i wasnt talking to you|wrong robot)",
    "es": r"(?:nada|olvidalo|da igual|dejalo|no importa|cancela|perdon|no es contigo|no te hablaba a ti)",
    "it": r"(?:niente|lascia stare|lascia perdere|annulla|non importa|scusa|non parlavo con te)",
    "pt": r"(?:nada|esquece|deixa|cancela|deixa estar|nao importa|desculpa|nao e contigo)",
}
_PAUSE = {
    "en": r"(?:pause|pause it|stop the music|stop music|pause the music|pause music|stop the song|pause the song)",
    "es": r"(?:pausa|pausala|pon pausa|para la musica|para musica|deten la musica|pausa la musica|para la cancion)",
    "it": r"(?:pausa|metti in pausa|ferma la musica|pausa la musica|ferma la canzone)",
    "pt": r"(?:pausa|pausar|para a musica|pausa a musica|pare a musica)",
}
_STOP = {"en": r"(?:stop|stop it|enough)", "es": r"(?:para|basta|alto|stop|parale)",
         "it": r"(?:basta|ferma|fermati|stop)", "pt": r"(?:para|pare|chega|basta|stop)"}  # fmt: skip
_RESUME = {
    "en": r"(?:resume|continue|unpause|keep playing|play|play music|play some music|put some music on|put on some music|"
    r"resume the music|continue the music|play the music)",
    "es": r"(?:continua|reanuda|sigue|quita la pausa|pon musica|pon la musica|pon algo de musica|continua la musica|"
    r"sigue la musica|ponme musica)",
    "it": r"(?:riprendi|continua|metti musica|metti la musica|metti un po di musica|riprendi la musica)",
    "pt": r"(?:continua|retoma|toca musica|poe musica|poe uma musica|continua a musica|retoma a musica)",
}
_NEXT = {
    "en": r"(?:next|skip|next song|next track|skip this|skip this song|skip song|skip it|play the next song|another song)",
    "es": r"(?:siguiente|la siguiente|siguiente cancion|pasa la cancion|salta la cancion|cambia la cancion|"
    r"otra cancion|pon la siguiente)",
    "it": r"(?:prossima|la prossima|prossima canzone|salta la canzone|cambia canzone|unaltra canzone|metti la prossima)",
    "pt": r"(?:proxima|a proxima|proxima musica|passa a musica|pula a musica|muda a musica|outra musica|poe a proxima)",
}
_VOLUME_TARGET = r"(?:\s+(?:of\s+)?(?:the\s+|el\s+|la\s+|il\s+|o\s+|a\s+|da\s+|de\s+la\s+|della\s+)?(?P<device>tv|tele|television|televisione|televisao|music|musica|spotify))?"
_VOLUME_UP = {
    "en": rf"(?:volume up|louder|a bit louder|little louder|turn it up|turn the volume up|turn up the volume|raise the volume|"
    rf"turn up the (?:music|tv)|turn the (?:music|tv) up|increase the volume){_VOLUME_TARGET}(?: a bit| a little)?",
    "es": rf"(?:sube(?:le)?(?: el)? volumen|mas alto|mas fuerte|sube la musica|sube la tele|subele el volumen|subele)"
    rf"{_VOLUME_TARGET}(?: un poco)?",
    "it": rf"(?:alza(?: il)? volume|piu forte|piu alto|aumenta(?: il)? volume|alza la musica){_VOLUME_TARGET}"
    rf"(?: un po)?",
    "pt": rf"(?:aumenta(?: o)? volume|sobe(?: o)? volume|mais alto|aumenta a musica){_VOLUME_TARGET}(?: um pouco)?",
}
_VOLUME_DOWN = {
    "en": rf"(?:volume down|lower|lower it|a bit lower|little lower|quieter|softer|turn it down|turn the volume down|turn down the volume|lower the volume|"
    rf"turn down the (?:music|tv)|turn the (?:music|tv) down|decrease the volume){_VOLUME_TARGET}(?: a bit| a little)?",
    "es": rf"(?:baja(?:le)?(?: el)? volumen|mas bajo|mas despacio|baja la musica|baja la tele|bajale el volumen|bajale)"
    rf"{_VOLUME_TARGET}(?: un poco)?",
    "it": rf"(?:abbassa(?: il)? volume|piu piano|piu basso|abbassa la musica){_VOLUME_TARGET}(?: un po)?",
    "pt": rf"(?:baixa(?: o)? volume|diminui(?: o)? volume|mais baixo|baixa a musica){_VOLUME_TARGET}(?: um pouco)?",
}
_VOLUME_SET = {
    "en": rf"(?:(?:set|said|sets|put|turn|make|change) (?:the )?)?volume{_VOLUME_TARGET} (?:to |at )?"
    rf"(?P<level>\d{{1,3}})(?: percent)?",
    "es": rf"(?:pon )?(?:el )?volumen{_VOLUME_TARGET} (?:al|a|en) (?P<level>\d{{1,3}})(?: por ciento| percent)?",
    "it": rf"(?:metti )?(?:il )?volume{_VOLUME_TARGET} (?:al|a) (?P<level>\d{{1,3}})(?: per cento| percent)?",
    "pt": rf"(?:poe )?(?:o )?volume{_VOLUME_TARGET} (?:a|em|no|nos) (?P<level>\d{{1,3}})(?: por cento| percent)?",
}
_ON_OFF = [
    ("en", re.compile(r"^(?:turn|switch|shut)\s+(?P<state>on|off)\s+(?P<target>.+)$")),
    ("en", re.compile(r"^(?:turn|switch|shut)\s+(?P<target>.+?)\s+(?P<state>on|off)$")),
    ("en", re.compile(r"^(?P<target>.+?)\s+(?P<state>on|off)$")),
    ("en", re.compile(r"^(?P<target>lights)\s+(?P<state>out)$")),
    ("es", re.compile(r"^(?P<verb>enciend\w*|encend\w*|prend\w*|apag\w*)\s+(?P<target>.+)$")),
    ("it", re.compile(r"^(?P<verb>accend\w*|speg\w*|spent\w*)\s+(?P<target>.+)$")),
    ("pt", re.compile(r"^(?P<verb>deslig\w*|lig\w*|acend\w*)\s+(?P<target>.+)$")),
]
_DIM_WORDS = r"(?:dim|lower|dim down)"
_BRIGHT_WORDS = r"(?:brighten|brighten up|raise)"
_PERCENT = r"(?:\s*(?:percent|por ciento|per cento|por cento))?"
_BRIGHTNESS = [
    ("en", re.compile(rf"^{_DIM_WORDS}\s+(?P<target>.+?)(?:\s+to\s+(?P<level>\d{{1,3}}){_PERCENT})?$"), "dim"),
    ("en", re.compile(rf"^{_BRIGHT_WORDS}\s+(?P<target>.+?)(?:\s+to\s+(?P<level>\d{{1,3}}){_PERCENT})?$"), "bright"),
    ("en", re.compile(rf"^(?:set|put|turn)\s+(?P<target>.+?)\s+(?:to|at)\s+(?P<level>\d{{1,3}}){_PERCENT}$"), "level"),
    ("en", re.compile(rf"^(?P<target>.+?)\s+(?:to|at)\s+(?P<level>\d{{1,3}}){_PERCENT}$"), "level"),
    ("en", re.compile(r"^(?:make\s+)?(?P<target>.+?)\s+(?:dimmer|darker|less bright)$"), "dim"),
    ("en", re.compile(r"^(?:make\s+)?(?P<target>.+?)\s+brighter$"), "bright"),
    (
        "es",
        re.compile(rf"^(?:pon|deja|ajusta)\s+(?P<target>.+?)\s+(?:al|a|en)\s+(?P<level>\d{{1,3}}){_PERCENT}$"),
        "level",
    ),
    ("es", re.compile(rf"^(?P<target>.+?)\s+al\s+(?P<level>\d{{1,3}}){_PERCENT}$"), "level"),
    ("es", re.compile(r"^(?:baja|bajale|atenua)\s+(?P<target>.+)$"), "dim"),
    ("es", re.compile(r"^(?:sube|subele)\s+(?P<target>.+)$"), "bright"),
    ("it", re.compile(rf"^(?:metti|imposta)\s+(?P<target>.+?)\s+(?:al|a)\s+(?P<level>\d{{1,3}}){_PERCENT}$"), "level"),
    ("it", re.compile(r"^(?:abbassa|attenua)\s+(?P<target>.+)$"), "dim"),
    ("it", re.compile(r"^(?:alza)\s+(?P<target>.+)$"), "bright"),
    (
        "pt",
        re.compile(rf"^(?:poe|deixa|mete)\s+(?P<target>.+?)\s+(?:a|em|no|nos)\s+(?P<level>\d{{1,3}}){_PERCENT}$"),
        "level",
    ),
    ("pt", re.compile(r"^(?:baixa|diminui|reduz)\s+(?P<target>.+)$"), "dim"),
    ("pt", re.compile(r"^(?:aumenta|sobe)\s+(?P<target>.+)$"), "bright"),
]
DIM_LEVEL: Final[int] = 30
BRIGHT_LEVEL: Final[int] = 100
_COLOR_WORDS: Final[dict[str, str]] = {
    **dict.fromkeys(("red", "rojo", "roja", "rojas", "rojos", "rosso", "rossa", "rossi", "rosse", "vermelho",
                     "vermelha", "vermelhos", "vermelhas"), "red"),
    **dict.fromkeys(("orange", "naranja", "naranjas", "arancione", "arancioni", "laranja", "laranjas"), "orange"),
    **dict.fromkeys(("yellow", "amarillo", "amarilla", "amarillos", "amarillas", "giallo", "gialla", "gialli",
                     "gialle", "amarelo", "amarela", "amarelos", "amarelas"), "yellow"),
    **dict.fromkeys(("green", "verde", "verdes", "verdi"), "green"),
    **dict.fromkeys(("cyan", "turquoise", "celeste", "turquesa", "azzurro", "azzurra", "azzurri", "azzurre",
                     "ciano"), "cyan"),
    **dict.fromkeys(("blue", "azul", "azules", "blu", "azuis"), "blue"),
    **dict.fromkeys(("purple", "violet", "morado", "morada", "morados", "moradas", "violeta", "viola", "lila",
                     "roxo", "roxa", "roxos", "roxas"), "purple"),
    **dict.fromkeys(("pink", "rosa", "rosado", "rosada", "rosas", "rosados"), "pink"),
    **dict.fromkeys(("white", "blanco", "blanca", "blancos", "blancas", "bianco", "bianca", "bianchi", "bianche",
                     "branco", "branca", "brancos", "brancas"), "white"),
}  # fmt: skip
_COLOR = [
    ("en", re.compile(r"^(?:make|turn|set|change|put)\s+(?P<target>.+?)\s+(?:to\s+|in\s+)?(?P<color>\w+)$")),
    ("es", re.compile(r"^(?:pon|cambia|haz|deja)(?:me)?\s+(?P<target>.+?)\s+(?:en\s+|de\s+(?:color\s+)?|a\s+|color\s+)?(?P<color>\w+)$")),
    ("it", re.compile(r"^(?:metti|fai|cambia|rendi)\s+(?P<target>.+?)\s+(?:in\s+|di\s+(?:colore\s+)?|a\s+)?(?P<color>\w+)$")),
    ("pt", re.compile(r"^(?:poe|muda|coloca|mete|deixa)\s+(?P<target>.+?)\s+(?:em\s+|de\s+(?:cor\s+)?|a\s+|para\s+)?(?P<color>\w+)$")),
    # Last: no verb at all ("TV lights red").
    ("en", re.compile(r"^(?P<target>.+?)\s+(?:to\s+)?(?P<color>\w+)$")),
]  # fmt: skip
_YOUTUBE = [
    ("en", re.compile(r"^(?:play|put on|put|show|search for|search|find)\s+(?P<q>.+?)\s+on\s+(?:youtube|the tv|tv|the television)$")),
    ("en", re.compile(r"^(?:search youtube for|youtube)\s+(?P<q>.+)$")),
    ("es", re.compile(r"^(?:pon|ponme|busca|reproduce)\s+(?P<q>.+?)\s+en\s+(?:youtube|la tele|la tv|la television)$")),
    ("it", re.compile(r"^(?:metti|mettimi|cerca|fammi vedere)\s+(?P<q>.+?)\s+(?:su youtube|sulla tv|sulla televisione|in tv|in televisione)$")),
    ("pt", re.compile(r"^(?:poe|coloca|procura|mete)\s+(?P<q>.+?)\s+(?:no youtube|na tv|na televisao)$")),
]  # fmt: skip
_OPEN_APP = [
    ("en", re.compile(r"^(?:open|launch|start|put on|put)\s+(?P<app>.+?)(?:\s+on the tv)?$")),
    ("es", re.compile(r"^(?:abre|abreme|pon|ponme)\s+(?P<app>.+?)(?:\s+en la tele| en la tv)?$")),
    ("it", re.compile(r"^(?:apri|metti|avvia)\s+(?P<app>.+?)(?:\s+sulla tv| sulla televisione)?$")),
    ("pt", re.compile(r"^(?:abre|poe|coloca|mete)\s+(?P<app>.+?)(?:\s+na tv| na televisao)?$")),
]
_PLAY = [
    ("en", re.compile(r"^(?:play|put on)\s+(?P<q>.+?)(?:\s+on spotify)?$")),
    ("es", re.compile(r"^(?:pon|ponme|reproduce|toca|tocame)\s+(?P<q>.+?)(?:\s+en spotify)?$")),
    ("it", re.compile(r"^(?:metti|mettimi|suona|riproduci)\s+(?P<q>.+?)(?:\s+su spotify)?$")),
    ("pt", re.compile(r"^(?:toca|poe|coloca|reproduz|mete)\s+(?P<q>.+?)(?:\s+no spotify)?$")),
]
# "Play with me", "play hide and seek": a game, not music.
_NOT_MUSIC = re.compile(
    r"^(?:with|a game|game|games|hide|tag|catch|pretend|together|outside|dead|con|com)\b|"
    r"\b(?:game|games|juego|gioco|jogo|chess|ajedrez|scacchi|xadrez|scissors|tijera|tijeras|story|stories|cuento|"
    r"storia|historia|timer|temporizador|alarm|alarma|allarme|alarme|reminder|recordatorio|mesa|table|tavola|"
    r"atencion|attention|cuidado|movie|movies|film|peli|pelicula|cartoons|cartoon|dibujos|cartoni|desenhos|news|"
    r"noticias|notizie|telediario|shoes|zapatos|scarpe|sapatos|coat|abrigo|chaqueta|pijama|pyjamas|pajamas|guitar|"
    r"guitarra|chitarra|piano|piano|drums|bateria|tambor|ball|pelota|palla|bola|dress|vestido|ropa|clothes)\b"
)
# "Play that song again", "my favorite": needs the conversation or memory, not a literal search.
_NEEDS_CONTEXT = re.compile(
    r"\b(?:that|this|it|again|same|favorite|favourite|eso|esa|ese|esto|esta|otra vez|favorita|favorito|"
    r"quella|quello|questa|questo|ancora|preferita|preferito|isso|essa|esse|isto|outra vez|preferida)\b"
)
_MUSIC_ONLY = frozenset(
    {"music", "some music", "the music", "musica", "la musica", "algo de musica", "un poco de musica",
     "un po di musica", "a musica", "uma musica", "something", "algo", "qualcosa", "alguma coisa"}
)  # fmt: skip
# "Take a picture (of us)", "let's take a photo": polite Spanish and Italian requests leave the infinitive
# ("puedes sacar una foto"). 2026-10-29: "Richie, tomemos una foto" went to the conversation.
_PHOTO = {
    "en": r"(?:lets )?(?:take|snap) (?:a |our |my )?(?:picture|photo|pic)(?: of (?:us|me|us all|all of us|everyone))?",
    "es": r"(?:toma|tomar|tomanos|tomame|tomarnos|tomemos|saca|sacar|sacanos|sacame|sacarnos|saquemos|haz|hacer|"
    r"haznos|hazme|hacernos|hagamos|vamos a (?:tomar|sacar|hacer)|(?:me |nos )(?:tomas|sacas|haces)) una foto"
    r"(?: (?:de|a) (?:nosotros|todos))?",
    "it": r"(?:fai|fare|facci|farci|fammi|facciamo|scatta|scattare|scattaci|scattarci|scattami|scattiamo) una foto"
    r"(?: a (?:noi|tutti))?",
    "pt": r"(?:tira|tirar|tira nos|tirar nos|tira me|tire|tiremos|vamos tirar|bate|bater|vamos bater) uma "
    r"(?:foto|fotografia)(?: (?:de|a) (?:nos|todos))?",
}
# "Send a message / a picture (to x)", "vamos a mandar un mensaje a x": a voice message or a photo for a family
# group in the WhatsApp contacts file; without x, Reachy asks who. Polite forms leave the infinitive.
_TO = {
    "en": r"(?: (?:to|for) (?P<to>.+))?",
    "es": r"(?: (?:a|al|para) (?P<to>.+))?",
    "it": r"(?: (?:a|al|ai|allo|alla|agli|alle|per) (?P<to>.+))?",
    "pt": r"(?: (?:a|ao|aos|as|para) (?P<to>.+))?",
}
_SEND_VERB = {
    "en": r"(?:lets |i want to |i wanna )?send",
    "es": r"(?:manda|mandale|mandales|mandar|mandarle|mandarles|envia|enviale|enviales|enviar|enviarle|enviarles|"
    r"(?:vamos a|quiero) (?:mandar|enviar)(?:le|les)?)",
    "it": r"(?:manda|mandagli|mandiamo|mandare|invia|inviagli|inviamo|inviare)",
    "pt": r"(?:manda|mandar|envia|enviar|vamos (?:mandar|enviar))",
}
_MESSAGE_OBJECT = {
    "en": r"(?:a )?(?:voice )?message",
    "es": r"(?:un (?:mensaje(?: de voz)?|audio)|una nota de voz)",
    "it": r"un (?:messaggio(?: vocale)?|vocale|audio)",
    "pt": r"(?:uma mensagem(?: de voz)?|um audio)",
}
_PICTURE_OBJECT = {
    "en": r"(?:a )?(?:picture|photo|pic)",
    "es": r"una (?:foto|fotografia)",
    "it": r"una foto",
    "pt": r"uma (?:foto|fotografia)",
}
_SEND_TO = {
    kind: {
        language: [
            rf"{_SEND_VERB[language]} {objects[language]}{_TO[language]}",
            *([rf"{_SEND_VERB[language]} (?P<to>.+?) {objects[language]}"] if language == "en" else []),
        ]
        for language in _SEND_VERB
    }
    for kind, objects in (("message", _MESSAGE_OBJECT), ("send_photo", _PICTURE_OBJECT))
}
_TV_WORDS = frozenset({"tv", "tele", "television", "televisor", "televisione", "televisore", "televisao"})


def _full(pattern: str) -> re.Pattern[str]:
    return re.compile(rf"^(?:{pattern})$")


_TIME_RE = {language: _full(pattern) for language, pattern in _TIME.items()}
_DATE_RE = {language: _full(pattern) for language, pattern in _DATE.items()}
_WEATHER_RE = {language: [_full(pattern) for pattern in patterns] for language, patterns in _WEATHER.items()}
_RAIN_RE = {language: _full(pattern) for language, pattern in _RAIN.items()}
_SLEEP_RE = {language: _full(pattern) for language, pattern in _SLEEP.items()}
_CANCEL_RE = {language: _full(pattern) for language, pattern in _CANCEL.items()}
_PAUSE_RE = {language: _full(pattern) for language, pattern in _PAUSE.items()}
_STOP_RE = {language: _full(pattern) for language, pattern in _STOP.items()}
_RESUME_RE = {language: _full(pattern) for language, pattern in _RESUME.items()}
_NEXT_RE = {language: _full(pattern) for language, pattern in _NEXT.items()}
_VOLUME_UP_RE = {language: _full(pattern) for language, pattern in _VOLUME_UP.items()}
_VOLUME_DOWN_RE = {language: _full(pattern) for language, pattern in _VOLUME_DOWN.items()}
_VOLUME_SET_RE = {language: _full(pattern) for language, pattern in _VOLUME_SET.items()}
_PHOTO_RE = {language: _full(pattern) for language, pattern in _PHOTO.items()}
_SEND_TO_RE = {
    kind: {language: [_full(pattern) for pattern in patterns] for language, patterns in table.items()}
    for kind, table in _SEND_TO.items()
}
_TOMORROW = frozenset({"tomorrow", "manana", "domani", "amanha"})


def _first_match(table: dict[str, re.Pattern[str]], text: str) -> tuple[str, re.Match[str]] | None:
    for language, pattern in table.items():
        match = pattern.match(text)
        if match:
            return language, match
    return None


# First words shared by Spanish and Portuguese (and some Italian): the rest of the sentence decides.
_SHARED_FIRST_WORDS = frozenset(
    {"apaga", "apagar", "pausa", "pausar", "para", "parar", "nada", "continua", "toca", "basta", "stop", "cancela",
     "que", "aumenta", "salta", "sube", "baja"}
)  # fmt: skip


def _language(language: str, text: str) -> str:
    """Refine a Romance-language guess: "apaga la luz" is Spanish, "apaga a luz" Portuguese."""
    words = text.split()
    if language == "en" or not words or words[0] not in _SHARED_FIRST_WORDS:
        return language
    counts = {
        "es": sum(word in _SPANISH_MARKERS for word in words[1:]),
        "pt": sum(word in _PORTUGUESE_MARKERS for word in words[1:]),
        "it": sum(word in _ITALIAN_MARKERS for word in words[1:]),
    }
    best = max(counts, key=lambda candidate: counts[candidate])
    return best if counts[best] > counts.get(language, 0) else language


def _log_routine_failure(task: asyncio.Task[None]) -> None:
    if not task.cancelled() and task.exception() is not None:
        logger.warning("Routine step failed: %s", type(task.exception()).__name__)


_SPANISH_ROUTINE_WORDS = _SPANISH_MARKERS | {"modo", "cine", "pelicula", "oscuridad", "buenas", "noches", "luz"}


def _routine_language(phrase: str) -> str:
    """Guess the language of a routine phrase ("modo cine" is Spanish); English otherwise."""
    return "es" if any(word in _SPANISH_ROUTINE_WORDS for word in phrase.split()) else "en"


def _volume_device(match: re.Match[str]) -> str | None:
    device = match.groupdict().get("device")
    if device in _TV_WORDS:
        return "tv"
    if device:
        return "music"
    text = match.group(0)
    if any(word in text.split() for word in _TV_WORDS):
        return "tv"
    if "music" in text or "musica" in text:
        return "music"
    return None


# "Don't turn the lights off", "no apagues la luz": never act on a negation.
_NEGATIONS = frozenset(
    {"dont", "not", "never", "didnt", "doesnt", "cant", "wont", "shouldnt", "nunca", "non", "mai", "nao",
     "ni", "nem", "jamas"}
)  # fmt: skip
# A device command starts with its verb or target, not a question or a statement about someone
# ("is the TV on?", "who turned the lights off?", "we should turn off the lights").
_NOT_A_COMMAND_START = frozenset(
    {"is", "are", "was", "were", "did", "do", "does", "who", "why", "what", "when", "where", "how", "which", "we",
     "i", "you", "they", "he", "she", "it", "mom", "mommy", "dad", "daddy", "papa", "mama", "leave", "keep",
     "should", "if", "maybe", "quien", "por", "porque", "cuando", "donde", "como", "yo", "tu", "nosotros", "el",
     "ella", "chi", "perche", "quando", "dove", "come", "io", "noi", "lui", "lei", "quem", "onde", "eu", "nos",
     "ele", "ela", "se", "si", "que", "cual"}
)  # fmt: skip


def parse(text: str) -> QuickCommand | None:
    """Recognize one everyday command in ``text`` (the request after the wake word), or return None."""
    command = strip_politeness(normalize(text))
    if not command or len(command.split()) > MAX_COMMAND_WORDS:
        return None
    found = _first_match(_CANCEL_RE, command)
    if found:
        return QuickCommand("cancel", _language(found[0], command))
    words = command.split()
    # Portuguese "no" also means "in the" ("no YouTube"), so "no" counts only as the first word.
    if words[0] == "no" or any(word in _NEGATIONS for word in words):
        return None
    for table, kind in ((_SLEEP_RE, "sleep"), (_TIME_RE, "time"), (_DATE_RE, "date")):
        found = _first_match(table, command)
        if found:
            return QuickCommand(kind, _language(found[0], command))
    for language, patterns in _WEATHER_RE.items():
        for pattern in patterns:
            match = pattern.match(command)
            if match:
                groups = match.groupdict()
                when = groups.get("when") or groups.get("when2")
                place = (groups.get("place") or "").strip() or None
                return QuickCommand(
                    "weather", language, {"tomorrow": when in _TOMORROW, "place": place, "rain_only": False}
                )
    found = _first_match(_RAIN_RE, command)
    if found:
        when = found[1].groupdict().get("when")
        return QuickCommand("weather", found[0], {"tomorrow": when in _TOMORROW, "place": None, "rain_only": True})
    found = _first_match(_PHOTO_RE, command)
    if found:
        return QuickCommand("photo", found[0])
    for kind, send_table in _SEND_TO_RE.items():
        for language, send_patterns in send_table.items():
            for pattern in send_patterns:
                match = pattern.match(command)
                if match:
                    to = (match.groupdict().get("to") or "").strip() or None
                    if to is not None and re.search(rf"\b{_MARIBEL}\b", to):
                        return None  # her hand-off owns messages for Maribel
                    return QuickCommand(kind, language, {"to": to})
    if words[0] in _NOT_A_COMMAND_START:
        return None
    for table, kind in ((_PAUSE_RE, "pause"), (_NEXT_RE, "next"), (_RESUME_RE, "resume"), (_STOP_RE, "stop")):
        found = _first_match(table, command)
        if found:
            return QuickCommand(kind, _language(found[0], command))
    for table, direction in ((_VOLUME_UP_RE, "up"), (_VOLUME_DOWN_RE, "down")):
        found = _first_match(table, command)
        if found:
            return QuickCommand(
                "volume", _language(found[0], command), {"direction": direction, "device": _volume_device(found[1])}
            )
    found = _first_match(_VOLUME_SET_RE, command)
    if found:
        level = int(found[1].group("level"))
        if 0 <= level <= 100:
            return QuickCommand(
                "volume", _language(found[0], command), {"level": level, "device": _volume_device(found[1])}
            )
    for language, pattern in _ON_OFF:
        match = pattern.match(command)
        if not match:
            continue
        groups = match.groupdict()
        verb = groups.get("verb")
        if verb is None:
            on = groups["state"] == "on"
        else:
            on = not verb.startswith(("apag", "speg", "spent", "deslig"))
        return QuickCommand("power", _language(language, command), {"on": on, "target": groups["target"]})
    for language, pattern, mode in _BRIGHTNESS:
        match = pattern.match(command)
        if match:
            raw_level = match.groupdict().get("level")
            value = int(raw_level) if raw_level else (DIM_LEVEL if mode == "dim" else BRIGHT_LEVEL)
            if 1 <= value <= 100:
                return QuickCommand(
                    "brightness", _language(language, command), {"level": value, "target": match.group("target")}
                )
    for language, pattern in _COLOR:
        match = pattern.match(command)
        if match and match.group("color") in _COLOR_WORDS:
            return QuickCommand(
                "color", _language(language, command),
                {"color": _COLOR_WORDS[match.group("color")], "target": match.group("target")},
            )  # fmt: skip
    for language, pattern in _YOUTUBE:
        match = pattern.match(command)
        if match:
            return QuickCommand("youtube", _language(language, command), {"query": match.group("q")})
    for language, pattern in _OPEN_APP:
        match = pattern.match(command)
        if match:
            from reachy_mini_conversation_app.android_tv import resolve_app

            app = match.group("app")
            if app in _TV_WORDS or app in {"the tv", "la tele", "la tv", "a tv", "la television", "a televisao"}:
                return QuickCommand("power", _language(language, command), {"on": True, "target": "tv"})
            if resolve_app(app) is not None:
                return QuickCommand("open_app", _language(language, command), {"app": app})
    for language, pattern in _PLAY:
        match = pattern.match(command)
        if match:
            query = match.group("q").strip()
            if query in _MUSIC_ONLY:
                return QuickCommand("resume", _language(language, command))
            if (
                _NOT_MUSIC.search(query)
                or _NEEDS_CONTEXT.search(query)
                or "podcast" in query
                or target_tokens(query)[1]
            ):
                return None
            if language == "es" and query.startswith("a "):
                query = query[2:]  # "pon a Bad Bunny": the personal "a"
            return QuickCommand("play", _language(language, command), {"query": query})
    return None


# ---------------------------------------------------------------------------------------------
# Light targets

_LIGHT_WORDS = frozenset(
    {"light", "lights", "luz", "luces", "luce", "luci", "luzes", "lampadina", "lampadine", "bulb", "bulbs"}
)
_FILLERS = frozenset(
    {"the", "a", "an", "of", "in", "on", "my", "el", "la", "los", "las", "de", "del", "en", "mi", "mis", "il", "lo",
     "le", "gli", "i", "di", "della", "delle", "dello", "nel", "nella", "o", "os", "as", "da", "do", "das", "dos",
     "no", "na", "em", "meu", "minha", "area", "zone", "zona", "parte", "part", "side", "back", "again",
     "otra", "vez", "di", "nuovo", "novo", "outra"}
)  # fmt: skip
_PHRASES = (
    ("sala de estar", "living room"),
    ("sala de jantar", "dining"),
    ("sala da pranzo", "dining"),
    ("living room", "living room"),
    ("dining room", "dining"),
)
_TRANSLATIONS: Final[dict[str, str]] = {
    **dict.fromkeys(("tele", "tv", "television", "televisor", "televisione", "televisore", "televisao"), "tv"),
    **dict.fromkeys(("comedor", "jantar", "pranzo", "dining"), "dining"),
    **dict.fromkeys(("salon", "sala", "soggiorno", "salotto"), "living room"),
    **dict.fromkeys(("lampara", "lamparas", "lampada", "lampade", "candeeiro", "candeeiros", "abajur", "lamps", "lamp"), "lamp"),
    **dict.fromkeys(("todo", "todas", "todos", "tutte", "tutti", "tutto", "tudo", "everything", "every", "all",
                     "casa", "house"), "all"),
    **dict.fromkeys(("plug", "plugs", "enchufe", "presa", "tomada"), "plug"),
}  # fmt: skip


def target_tokens(phrase: str) -> tuple[frozenset[str], bool]:
    """Return the meaningful words of a light target in English, and whether it said "light(s)"."""
    text = f" {normalize(phrase)} "
    for source, replacement in _PHRASES:
        text = text.replace(f" {source} ", f" {replacement} ")
    words: list[str] = []
    for word in text.split():
        words.extend(_TRANSLATIONS.get(word, word).split())
    light_word = any(word in _LIGHT_WORDS for word in words)
    return frozenset(word for word in words if word not in _FILLERS and word not in _LIGHT_WORDS), light_word


@dataclass(frozen=True)
class LightTarget:
    """Where a light change goes: "all", a room or area name, or device names."""

    name: str | None = None
    devices: tuple[str, ...] = ()


def resolve_light_target(phrase: str, devices: list[Any]) -> LightTarget | None:
    """Match a spoken target to the home's rooms, areas and devices (``HubDevice`` list)."""
    tokens, light_word = target_tokens(phrase)
    if not tokens or tokens <= {"all"}:
        return LightTarget(name="all") if light_word or tokens else None
    tokens = tokens - {"all"}

    def core(name: str) -> frozenset[str]:
        return target_tokens(name)[0] - {"all"}

    # A device's own name wins over a room or area that shares its first word ("Living lamp").
    exact = tuple(device.name for device in devices if core(device.name) == tokens)
    if exact:
        return LightTarget(devices=exact)
    rooms = {device.room for device in devices if device.room}
    for room in rooms:
        if core(room) == tokens:
            return LightTarget(name=room)
    areas = {area for device in devices for area in device.areas}
    for area in areas:
        if core(area) == tokens:
            return LightTarget(name=area)
    partial = tuple(device.name for device in devices if tokens <= core(device.name))
    if partial:
        return LightTarget(devices=tuple(dict.fromkeys(partial)))
    # Every word of a device's name was said, among others: "the living room lamp" -> "Living lamp".
    named = tuple(device.name for device in devices if core(device.name) and core(device.name) <= tokens)
    if named:
        return LightTarget(devices=tuple(dict.fromkeys(named)))
    return None


# ---------------------------------------------------------------------------------------------
# Maribel (Grok Bot): only a request that names her goes to her ("ask Maribel to...", "dile a Maribel que...")

_MARIBEL = r"(?:mari|mary|marie) ?bel(?:l|le|el)?"
_ASK_MARIBEL = {
    "en": rf"(?:ask|tell|message|text|let) {_MARIBEL}",
    # "le puedes preguntar a Maribel" (2026-10-28): "puedes" is politeness, "le" is not stripped.
    "es": rf"(?:le (?:puedes |podrias |quieres )?)?(?:preguntarle|preguntar|preguntale|pregunta|pedirle|pedir|"
    rf"pidele|pide|decirle|decir|dile|avisarle|avisar|avisale|escribirle|escribir|escribele|"
    rf"mandarle un mensaje|mandale un mensaje) a {_MARIBEL}",
    "it": rf"(?:chiedere|chiedi|domandare|domanda|dire|di|dici|scrivere|scrivi) a {_MARIBEL}",
    "pt": rf"(?:perguntar|pergunta|pergunte|pedir|pede|peca|dizer|diz|diga|avisar|avisa|"
    rf"mandar uma mensagem|manda uma mensagem) (?:a )?{_MARIBEL}",
}
# Something must follow her name: "ask Maribel" alone is left to the conversation.
_ASK_MARIBEL_RE = {language: re.compile(rf"^{pattern}\s+\S") for language, pattern in _ASK_MARIBEL.items()}
# Saying "Asking Maribel", sending (two tries) and waiting for her first reply.
MARIBEL_STEP_TIMEOUT_S: Final[float] = 2 * POST_TIMEOUT_S + RETRY_PAUSE_S + FIRST_REPLY_WAIT_S + 1.0


def maribel_request(text: str) -> str | None:
    """Return the language of a request explicitly for Maribel, or None."""
    command = strip_politeness(normalize(text))
    for language, pattern in _ASK_MARIBEL_RE.items():
        if pattern.match(command):
            return language
    return None


# ---------------------------------------------------------------------------------------------
# Photos for the WhatsApp group

# After the last beat: speech wobble stops and head tracking re-anchors; let the head settle.
PHOTO_SETTLE_S: Final[float] = 0.4
# Long enough that the runtime never cuts the send short (silence instead of "not sure it went through").
PHOTO_STEP_TIMEOUT_S: Final[float] = PHOTO_SETTLE_S + SHARE_TIMEOUT_S
COUNTDOWN: Final[dict[str, tuple[str, ...]]] = {
    "en": ("Three!", "Two!", "One!"),
    "es": ("¡Tres!", "¡Dos!", "¡Uno!"),
    "it": ("Tre!", "Due!", "Uno!"),
    "pt": ("Três!", "Dois!", "Um!"),
}
PHOTO_SENT: Final[dict[str, str]] = {"en": "Sent!", "es": "¡Enviada!", "it": "Inviata!", "pt": "Enviada!"}
PHOTO_NOT_SENT: Final[dict[str, str]] = {
    "en": "I couldn't send the photo.",
    "es": "No he podido enviar la foto.",
    "it": "Non sono riuscito a inviare la foto.",
    "pt": "Não consegui enviar a foto.",
}
PHOTO_UNSURE: Final[dict[str, str]] = {
    "en": "I'm not sure the photo went through.",
    "es": "No sé si la foto ha llegado.",
    "it": "Non so se la foto è arrivata.",
    "pt": "Não sei se a foto chegou.",
}
PHOTO_NO_PICTURE: Final[dict[str, str]] = {
    "en": "Sorry, the camera didn't give me a picture.",
    "es": "Lo siento, la cámara no me ha dado ninguna foto.",
    "it": "Scusa, la fotocamera non mi ha dato nessuna foto.",
    "pt": "Desculpa, a câmara não me deu nenhuma foto.",
}
MESSAGE_PROMPT: Final[dict[str, str]] = {
    "en": "Okay! Tell me your message for {name} after the beep.",
    "es": "¡Vale! Dime tu mensaje para {name} después del pitido.",
    "it": "Va bene! Dimmi il tuo messaggio per {name} dopo il bip.",
    "pt": "Está bem! Diz a tua mensagem para {name} depois do bip.",
}
SENT_TO: Final[dict[str, str]] = {
    "en": "Sent to {name}!",
    "es": "¡Ya se lo he mandado a {name}!",
    "it": "Fatto, inviato a {name}!",
    "pt": "Já enviei para {name}!",
}
SEND_TO_WHO: Final[dict[str, str]] = {
    "en": "Who should I send it to? I can send to {names}.",
    "es": "¿A quién se lo mando? Puedo mandárselo a {names}.",
    "it": "A chi lo mando? Posso mandarlo a {names}.",
    "pt": "Para quem envio? Posso enviar para {names}.",
}
SEND_TO_ONLY: Final[dict[str, str]] = {
    "en": "Sorry, I can only send to {names}.",
    "es": "Lo siento, solo puedo mandárselo a {names}.",
    "it": "Scusa, posso mandarlo solo a {names}.",
    "pt": "Desculpa, só posso enviar para {names}.",
}
MESSAGE_NOTHING: Final[dict[str, str]] = {
    "en": "I didn't hear a message, so I didn't send anything.",
    "es": "No he oído ningún mensaje, así que no he mandado nada.",
    "it": "Non ho sentito nessun messaggio, quindi non ho mandato niente.",
    "pt": "Não ouvi nenhuma mensagem, por isso não enviei nada.",
}
MESSAGE_NOT_SENT: Final[dict[str, str]] = {
    "en": "I couldn't send the message.",
    "es": "No he podido mandar el mensaje.",
    "it": "Non sono riuscito a mandare il messaggio.",
    "pt": "Não consegui enviar a mensagem.",
}
MESSAGE_UNSURE: Final[dict[str, str]] = {
    "en": "I'm not sure the message went through.",
    "es": "No sé si el mensaje ha llegado.",
    "it": "Non so se il messaggio è arrivato.",
    "pt": "Não sei se a mensagem chegou.",
}
# The beep, up to HybridRuntime._MESSAGE_START_S (8 s) of waiting and _MESSAGE_MAX_S (60 s) of message,
# encoding, then the send's own deadline.
MESSAGE_STEP_TIMEOUT_S: Final[float] = 75.0 + SEND_DEADLINE_S + 5.0
PHOTO_PHRASES: Final[tuple[tuple[str, str], ...]] = tuple(
    (text, language)
    for language in LANGUAGES
    for text in (
        *COUNTDOWN[language],
        PHOTO_SENT[language],
        PHOTO_NOT_SENT[language],
        PHOTO_UNSURE[language],
        PHOTO_NO_PICTURE[language],
        MESSAGE_NOTHING[language],
        MESSAGE_NOT_SENT[language],
        MESSAGE_UNSURE[language],
    )
)


# ---------------------------------------------------------------------------------------------
# Replies

_OK = {"en": "Okay.", "es": "Listo.", "it": "Fatto.", "pt": "Pronto."}
_FAILED = {
    "en": "Sorry, that didn't work.",
    "es": "Lo siento, no ha funcionado.",
    "it": "Scusa, non ha funzionato.",
    "pt": "Desculpa, não funcionou.",
}
_PARTLY = {
    "en": "Done, but {names} didn't respond.",
    "es": "Hecho, pero {names} no respondió.",
    "it": "Fatto, ma {names} non ha risposto.",
    "pt": "Feito, mas {names} não respondeu.",
}
_GOOD_NIGHT = {"en": "Good night!", "es": "¡Buenas noches!", "it": "Buonanotte!", "pt": "Boa noite!"}
MARIBEL_ASKING: Final[dict[str, str]] = {
    "en": "Asking Maribel.",
    "es": "Le pregunto a Maribel.",
    "it": "Chiedo a Maribel.",
    "pt": "Vou perguntar à Maribel.",
}
MARIBEL_PASSED_ON: Final[dict[str, str]] = {
    "en": "I've passed that to Maribel. She'll get back to you.",
    "es": "Se lo he pasado a Maribel. Ya te dirá algo.",
    "it": "L'ho passato a Maribel. Ti farà sapere.",
    "pt": "Passei isso à Maribel. Ela depois diz-te alguma coisa.",
}
MARIBEL_UNREACHABLE: Final[dict[str, str]] = {
    "en": "I couldn't reach Maribel right now.",
    "es": "Ahora no he podido hablar con Maribel.",
    "it": "Ora non sono riuscito a contattare Maribel.",
    "pt": "Agora não consegui falar com a Maribel.",
}
_PLAYING_TRACK = {"en": "Playing {name} by {artist}.", "es": "Pongo {name}, de {artist}.",
                  "it": "Metto {name}, di {artist}.", "pt": "A tocar {name}, de {artist}."}  # fmt: skip
_SPEAKER_UNAVAILABLE = {
    "en": "I can't reach the Spotify speaker right now. Start Spotify on it from your phone once, then ask me again.",
    "es": "Ahora no llego al altavoz de Spotify. Ponlo una vez desde el móvil y vuelve a pedírmelo.",
    "it": "Ora non raggiungo la cassa di Spotify. Avviala una volta dal telefono e chiedimelo di nuovo.",
    "pt": "Agora não chego à coluna do Spotify. Põe-na a tocar uma vez pelo telemóvel e pede-me outra vez.",
}
_PLAYING = {"en": "Playing {name}.", "es": "Pongo {name}.", "it": "Metto {name}.", "pt": "A tocar {name}."}
_NOT_FOUND = {
    "en": "I couldn't find that on Spotify.",
    "es": "No lo encontré en Spotify.",
    "it": "Non l'ho trovato su Spotify.",
    "pt": "Não encontrei isso no Spotify.",
}
_WEEKDAYS = {
    "en": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
    "es": ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"),
    "it": ("lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"),
    "pt": ("segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"),
}
_MONTHS = {
    "en": ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
           "November", "December"),
    "es": ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
           "noviembre", "diciembre"),
    "it": ("gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre", "ottobre",
           "novembre", "dicembre"),
    "pt": ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
           "novembro", "dezembro"),
}  # fmt: skip


def say_time(now: datetime, language: str) -> str:
    """Return the time as a short sentence."""
    if language == "en":
        hour = now.hour % 12 or 12
        return f"It's {hour}:{now.minute:02d} {'AM' if now.hour < 12 else 'PM'}."
    clock = f"{now.hour}:{now.minute:02d}"
    if language == "es":
        return f"Es la {clock}." if now.hour == 1 else f"Son las {clock}."
    if language == "it":
        return f"È l'{clock}." if now.hour == 1 else f"Sono le {clock}."
    return f"É {clock}." if now.hour == 1 else f"São {clock}."


def say_date(now: datetime, language: str) -> str:
    """Return today's date as a short sentence."""
    weekday = _WEEKDAYS[language][now.weekday()]
    month = _MONTHS[language][now.month - 1]
    if language == "en":
        return f"Today is {weekday}, {month} {now.day}."
    if language == "es":
        return f"Hoy es {weekday}, {now.day} de {month}."
    if language == "it":
        return f"Oggi è {weekday} {now.day} {month}."
    return f"Hoje é {weekday}, {now.day} de {month}."


def _names(names: list[str], language: str) -> str:
    if len(names) <= 1:
        return "".join(names)
    joiner = {"en": " and ", "es": " y ", "it": " e ", "pt": " e "}[language]
    return ", ".join(names[:-1]) + joiner + names[-1]


# ---------------------------------------------------------------------------------------------
# Execution


class QuickCommands:
    """Run recognized commands through the existing tools."""

    def __init__(
        self,
        deps: Any,
        *,
        weather: WeatherService | None = None,
        clock: Callable[[], datetime] | None = None,
        lights: Any = None,
        spotify: Any = None,
        tv: Any = None,
        spotify_configured: Callable[[], bool] | None = None,
        faces: FaceFinder | None = None,
        photographer: Callable[[Any, FaceFinder], bytes | None] = take_photo,
    ) -> None:
        """Use tool dependencies ``deps``; the tool instances can be replaced in tests."""
        from reachy_mini_conversation_app.tools.tv import TV
        from reachy_mini_conversation_app.tools.home_lights import HomeLights
        from reachy_mini_conversation_app.tools.spotify_control import SpotifyControl

        self._deps = deps
        self._weather = weather
        self._clock = clock or (lambda: datetime.now().astimezone())
        self._lights = lights or HomeLights()
        self._spotify = spotify or SpotifyControl()
        self._tv = tv or TV()
        self._spotify_configured = spotify_configured or _spotify_configured
        self._faces = faces or FACES
        self._photographer = photographer
        self._background: set[asyncio.Task[object]] = set()
        # The language of the last request to Maribel, for what Reachy says around her answers.
        self._maribel_language = "en"

    async def handle(self, text: str) -> QuickReply | None:
        """Run ``text`` if it is an everyday command; return None to leave it to the conversation."""
        maribel_language = maribel_request(text) if getattr(self._deps, "maribel", None) is not None else None
        if maribel_language is not None:
            self._maribel_language = maribel_language
            return self._ask_maribel(text, MARIBEL_ASKING[maribel_language])
        routine = self._routine_for(text)
        if routine is not None:
            try:
                return await self._run_routine(routine, text)
            except Exception as error:
                logger.warning("Quick routine failed: %s", type(error).__name__)
                return None
        command = parse(text)
        if command is None:
            return None
        try:
            return await self.run(command)
        except Exception as error:  # a tool bug must not break the wake; the conversation can try
            logger.warning("Quick command %s failed: %s", command.kind, type(error).__name__)
            return None

    async def answer_maribel(self, text: str) -> QuickReply:
        """Pass ``text``, heard just after Maribel's question, to her as the answer."""
        return self._ask_maribel(text, None)

    def _ask_maribel(self, text: str, said: str | None) -> QuickReply:
        """Say ``said``, send ``text`` to Maribel and speak her first reply (or that she will follow up)."""
        language = self._maribel_language
        link = self._deps.maribel

        async def hand_over() -> str | None:
            request_id = await link.send(text.strip())
            if request_id is None:
                return MARIBEL_UNREACHABLE[language]
            thinking = asyncio.create_task(
                asyncio.to_thread(queue_intent_move, self._deps.movement_manager, "thinking"),
                name="maribel-thinking",
            )
            self._background.add(thinking)
            thinking.add_done_callback(self._background.discard)
            reply = await link.first_reply(request_id)
            if reply is None:
                return MARIBEL_PASSED_ON[language]
            expect_answer = getattr(self._deps, "expect_answer", None)
            if reply.type == "question" and expect_answer is not None:
                expect_answer(self.answer_maribel)
            return reply.speak or None

        return QuickReply(
            said, language, "maribel", after=hand_over, after_timeout_s=MARIBEL_STEP_TIMEOUT_S, whole_request=True
        )

    def _routine_for(self, text: str) -> Any:
        """Return the household routine (from the home layout) whose phrase is the whole request."""
        layout = getattr(getattr(self._deps, "home_hub", None), "layout", None)
        phrase = strip_politeness(normalize(text))
        if not phrase:
            return None
        for routine in getattr(layout, "routines", ()):
            if any(strip_politeness(normalize(said)) == phrase for said in routine.say):
                return routine
        return None

    async def _run_routine(self, routine: Any, text: str) -> QuickReply:
        """Run a lights routine ("movie mode"); silent when it works, like any light change."""
        phrase = strip_politeness(normalize(text))
        parsed = parse(text)
        language = parsed.language if parsed is not None else _routine_language(phrase)

        async def lights() -> dict[str, Any]:
            if not routine.on and not routine.off:
                return {"ok": True, "changed": ["nothing"]}
            return dict(await self._lights(self._deps, action="scene", scene=routine.name))

        async def tv_off() -> None:
            if routine.tv_off and getattr(self._deps, "tv", None) is not None:
                result = await self._tv(self._deps, action="power", on=False)  # no key if it is already off
                if "error" in result:
                    logger.info("Routine %r: TV not turned off (%s)", routine.name, result["error"])

        async def pause_music() -> None:
            if routine.pause_music and self._spotify_configured():
                status = await self._spotify_status()
                if status and status.get("status") == "playing":
                    await self._spotify(self._deps, action="pause")

        if routine.tv_off:
            # The TV can take seconds to confirm it is off; the reply should not wait for it.
            task = asyncio.create_task(tv_off(), name="routine-tv-off")
            self._background.add(task)
            task.add_done_callback(self._background.discard)
            task.add_done_callback(_log_routine_failure)
        result, *others = await asyncio.gather(lights(), pause_music(), return_exceptions=True)
        for other in others:
            if isinstance(other, Exception):
                logger.warning("Routine %r step failed: %s", routine.name, type(other).__name__)
        if isinstance(result, BaseException):
            raise result
        problems = list(result.get("failed", []))
        if "error" in result or not result.get("changed") and not routine.sleep:
            text_out: str | None = _FAILED[language] if "error" in result or problems else None
        elif problems:
            text_out = _PARTLY[language].format(names=_names(problems, language))
        else:
            text_out = None
        if routine.sleep:

            async def fall_asleep() -> str | None:
                self._deps.movement_manager.go_to_sleep()
                return None

            return QuickReply(text_out or _GOOD_NIGHT[language], language, "routine", sleep=True, after=fall_asleep)
        return QuickReply(text_out, language, "routine")

    async def run(self, command: QuickCommand) -> QuickReply | None:
        """Execute one command; None when it needs the conversation after all."""
        language = command.language
        kind = command.kind
        if kind == "cancel":
            return QuickReply(None, language, kind)
        if kind == "sleep":

            async def fall_asleep() -> str | None:
                self._deps.movement_manager.go_to_sleep()
                return None

            return QuickReply(_GOOD_NIGHT[language], language, kind, sleep=True, after=fall_asleep)
        if kind == "time":
            return QuickReply(say_time(self._clock(), language), language, kind)
        if kind == "date":
            return QuickReply(say_date(self._clock(), language), language, kind)
        if kind == "weather":
            return await self._weather_reply(command)
        if kind in ("pause", "stop", "resume", "next"):
            return await self._media(command)
        if kind == "volume":
            return await self._volume(command)
        if kind in ("power", "brightness", "color"):
            return await self._lights_or_tv(command)
        if kind == "open_app":
            return await self._tv_action(command, action="open", app=command.args["app"])
        if kind == "youtube":
            query = command.args["query"]
            reply = await self._tv_action(command, action="youtube", query=query)
            return reply
        if kind == "play":
            return await self._play(command)
        if kind == "photo":
            return self._photo(command.language)
        if kind in ("message", "send_photo"):
            return self._send_to(kind, command.language, command.args.get("to"))
        return None

    def _send_to(self, kind: str, language: str, to: str | None) -> QuickReply | None:
        """Send a voice message or a photo to a family group; ask who when ``to`` names none of them."""
        if self._deps.whatsapp is None or (kind == "send_photo" and not self._deps.camera_enabled):
            return None
        contacts = self._contacts()
        if not contacts:
            return None
        contact = find_contact(normalize(to), contacts) if to else None
        if contact is not None:
            return self._send_reply(kind, language, contact)
        expect_answer = self._deps.expect_answer
        if expect_answer is None:
            return None
        names = _names([contact.name for contact in contacts], language)

        async def answer(text: str) -> QuickReply:
            command = parse(text)
            if command is not None and command.kind == "cancel":
                return QuickReply(None, language, kind)
            named = find_contact(strip_politeness(normalize(text)), self._contacts())
            reply = self._send_reply(kind, language, named) if named is not None else None
            return reply or QuickReply(SEND_TO_ONLY[language].format(names=names), language, kind)

        async def ask() -> str | None:
            expect_answer(answer)
            return None

        return QuickReply(SEND_TO_WHO[language].format(names=names), language, kind, after=ask)

    def _send_reply(self, kind: str, language: str, contact: Contact) -> QuickReply | None:
        if kind == "send_photo":
            return self._photo(language, contact)
        record = self._deps.record_message
        if record is None:
            return None
        whatsapp = self._deps.whatsapp
        movement = self._deps.movement_manager

        async def record_and_send() -> str:
            movement.set_listening(True)  # antennas perked and no idle breathing while he records
            try:
                recorded = await record()
            finally:
                movement.set_listening(False)
            if recorded is None:
                return MESSAGE_NOTHING[language]
            ogg = await asyncio.to_thread(encode_voice_note, *recorded)
            if ogg is None:
                return MESSAGE_NOT_SENT[language]
            result = await whatsapp.send_audio(contact.chat, ogg)
            if result is SendResult.SENT:
                return SENT_TO[language].format(name=contact.name)
            return MESSAGE_UNSURE[language] if result is SendResult.UNSURE else MESSAGE_NOT_SENT[language]

        return QuickReply(
            MESSAGE_PROMPT[language].format(name=contact.name),
            language,
            "message",
            after=record_and_send,
            after_timeout_s=MESSAGE_STEP_TIMEOUT_S,
            whole_request=True,
        )

    def _contacts(self) -> list[Contact]:
        from reachy_mini_conversation_app.config import config

        return load_contacts(config.WHATSAPP_CONTACTS_FILE, normalize)

    def _photo(self, language: str, contact: Contact | None = None) -> QuickReply | None:
        """Count down, take the photo, show it on the screen and send it to the photos group or ``contact``."""
        from reachy_mini_conversation_app.config import config

        chat = contact.chat if contact is not None else config.WHATSAPP_PHOTOS_CHAT
        sent = SENT_TO[language].format(name=contact.name) if contact is not None else PHOTO_SENT[language]
        if self._deps.whatsapp is None or not chat or not self._deps.camera_enabled:
            return None  # the conversation still has the camera and the screen

        async def take_and_send() -> str:
            await asyncio.sleep(PHOTO_SETTLE_S)
            result = await take_and_share(self._deps, chat, faces=self._faces, take=self._photographer)
            if result is None:
                return PHOTO_NO_PICTURE[language]
            if result is SendResult.SENT:
                return sent
            return PHOTO_UNSURE[language] if result is SendResult.UNSURE else PHOTO_NOT_SENT[language]

        return QuickReply(
            None,
            language,
            "photo",
            after=take_and_send,
            after_timeout_s=PHOTO_STEP_TIMEOUT_S,
            whole_request=True,
            beats=COUNTDOWN[language],
        )

    async def _weather_reply(self, command: QuickCommand) -> QuickReply | None:
        weather = self._weather
        if weather is None:
            return None
        language = command.language
        try:
            place: Place | None = None
            if command.args.get("place"):
                place = await weather.geocode(command.args["place"], language)
            forecast = await weather.forecast(place)
            if place is None:
                local_name = await weather.home_name(language)
                if local_name:
                    forecast = replace(forecast, place=local_name)
        except WeatherError as error:
            logger.info("Quick weather unavailable (%s); leaving it to the conversation", error)
            return None
        text = describe(
            forecast,
            language,
            tomorrow=bool(command.args.get("tomorrow")),
            rain_only=bool(command.args.get("rain_only")),
        )
        return QuickReply(text, language, command.kind)

    # Media ---------------------------------------------------------------------------------------

    async def _prepare_music(self) -> None:
        """Open Spotify and connect the music speaker before checking whether music can play."""
        if self._spotify_configured():
            from reachy_mini_conversation_app.music_speaker import prepare_for_playback

            await asyncio.to_thread(prepare_for_playback)

    async def _spotify_status(self) -> dict[str, Any] | None:
        """Spotify's status, ``{"error": ...}`` when it could not be read, or None when Spotify is not set up."""
        if not self._spotify_configured():
            return None
        result = await self._spotify(self._deps, action="status")
        return result if isinstance(result, dict) else {"error": "invalid status"}

    async def _tv_state(self) -> Any:
        tv = getattr(self._deps, "tv", None)
        if tv is None:
            return None
        try:
            return await tv.state()
        except Exception:
            return None

    async def _media(self, command: QuickCommand) -> QuickReply | None:
        kind, language = command.kind, command.language
        status = await self._spotify_status()
        playing = bool(status and status.get("status") == "playing")
        spotify_failed = status is not None and "error" in status
        if kind in ("pause", "stop"):
            if playing:
                result = await self._spotify(self._deps, action="pause")
                return QuickReply(None if "error" not in result else _FAILED[language], language, kind)
            tv_state = await self._tv_state()
            if (
                tv_state is not None
                and tv_state.is_on
                and tv_state.app_name not in (None, "com.google.android.tvlauncher")
            ):
                result = await self._tv(self._deps, action="media", key="pause")
                return QuickReply(None if result.get("ok") else _FAILED[language], language, kind)
            if spotify_failed:
                return QuickReply(_FAILED[language], language, kind)  # music may be playing out of reach
            if kind == "stop":
                self._deps.movement_manager.clear_move_queue()
            return QuickReply(None, language, kind)  # nothing is playing: nothing to pause
        if status is None:
            return None
        if spotify_failed:
            return QuickReply(_FAILED[language], language, kind)
        if kind == "resume":
            result = await self._spotify(self._deps, action="resume")  # the tool readies the speaker
            # Nothing to resume (no recent playback): the conversation can pick something to play.
            return None if "error" in result else QuickReply(None, language, kind)
        if kind == "next":
            result = await self._spotify(self._deps, action="next")
            return QuickReply(None if "error" not in result else _FAILED[language], language, kind)
        return None

    async def _volume(self, command: QuickCommand) -> QuickReply | None:
        language = command.language
        device = command.args.get("device")
        level = command.args.get("level")
        direction = command.args.get("direction")
        status = None
        if device != "tv":
            status = await self._spotify_status()
            if device is None:
                playing = bool(status and status.get("status") == "playing")
                if not playing:
                    tv_state = await self._tv_state()
                    device = "tv" if tv_state is not None and tv_state.is_on else "music"
                else:
                    device = "music"
        if device == "tv":
            if getattr(self._deps, "tv", None) is None:
                return None
            if level is not None:
                result = await self._tv(self._deps, action="volume", level=level)
            else:
                result = await self._tv(self._deps, action="volume", change=direction)
            return QuickReply(None if result.get("ok") else _FAILED[language], language, command.kind)
        if status is None:
            return None
        if "error" in status:
            return QuickReply(_FAILED[language], language, command.kind)
        if level is None:
            current = status.get("volume_percent")
            if not isinstance(current, int):
                return None
            # While Reachy listens the music is lowered; step from the level the person was hearing.
            from reachy_mini_conversation_app.tools.spotify_control import SPOTIFY_DUCKER

            base = SPOTIFY_DUCKER.level_before_ducking
            step = SPOTIFY_VOLUME_STEP if direction == "up" else -SPOTIFY_VOLUME_STEP
            level = max(0, min(100, (base if base is not None else current) + step))
        result = await self._spotify(self._deps, action="volume", volume_percent=int(level))
        return QuickReply(None if "error" not in result else _FAILED[language], language, command.kind)

    async def _play(self, command: QuickCommand) -> QuickReply | None:
        if not self._spotify_configured():
            return None
        language = command.language
        await self._prepare_music()
        status = await self._spotify_status()
        if status is not None and "unavailable" in str(status.get("error", "")):
            # Cast speakers drop off Spotify's device list when idle; say so before announcing a song.
            return QuickReply(_SPEAKER_UNAVAILABLE[language], language, command.kind)
        query = command.args["query"]
        search_type, query = _music_search_type(query)
        if search_type == "auto":
            artists, tracks = await self._search(query, "artist"), await self._search(query, "track")
            if artists is None or tracks is None:
                return QuickReply(_FAILED[language], language, command.kind)
            choice = _choose(query, artists, tracks)
        else:
            results = await self._search(query, search_type)
            if results is None:
                return QuickReply(_FAILED[language], language, command.kind)
            choice = results[0] if results else None
        if choice is None:
            return QuickReply(_NOT_FOUND[language], language, command.kind)
        uri = choice["uri"]
        creators = choice.get("creators") or []
        if creators:
            text = _PLAYING_TRACK[language].format(name=choice["name"], artist=creators[0])
        else:
            text = _PLAYING[language].format(name=choice["name"])

        async def play() -> str | None:
            result = await self._spotify(self._deps, action="play", uri=uri)
            return _FAILED[language] if "error" in result else None

        return QuickReply(text, language, command.kind, after=play)

    async def _search(self, query: str, search_type: str) -> list[dict[str, Any]] | None:
        """Search results, or None when Spotify could not be searched."""
        result = await self._spotify(self._deps, action="search", query=query, search_type=search_type)
        if not isinstance(result, dict) or "error" in result:
            return None
        items: Any = result.get("results")
        return [item for item in items or [] if isinstance(item, dict) and item.get("uri")]

    # Lights and TV -------------------------------------------------------------------------------

    async def _lights_or_tv(self, command: QuickCommand) -> QuickReply | None:
        language = command.language
        target = str(command.args["target"])
        tokens, light_word = target_tokens(target)
        if target == "tv" or (tokens == {"tv"} and not light_word):
            if getattr(self._deps, "tv", None) is None:
                return None
            if command.kind == "power":
                return await self._tv_action(command, action="power", on=command.args["on"])
            if command.kind == "brightness":  # "set the TV to 20" is its volume
                return await self._tv_action(command, action="volume", level=int(command.args["level"]))
            return None
        home = getattr(self._deps, "home_hub", None)
        if home is None:
            return None
        devices = await home.devices()
        resolved = resolve_light_target(target, devices)
        if resolved is None:
            return None
        change: dict[str, Any] = {}
        if command.kind == "power":
            change["on"] = bool(command.args["on"])
        elif command.kind == "brightness":
            change["brightness"] = int(command.args["level"])
        else:
            change["color"] = command.args["color"]
        targets = [resolved.name] if resolved.name else list(resolved.devices)
        results = await asyncio.gather(
            *(self._lights(self._deps, action="set", target=name, **change) for name in targets)
        )
        changed = [name for result in results for name in result.get("changed", [])]
        # Lights already offline are not news when the target is a group ("all", a room); a named light is.
        problems = [name for result in results for name in result.get("failed", [])]
        if resolved.devices:
            problems += [name for result in results for name in result.get("unreachable", [])]
        if not changed and all(str(result.get("error", "")).startswith("Nothing called") for result in results):
            return None  # the names did not match after all: let the conversation sort it out
        if not changed:
            return QuickReply(_FAILED[language], language, command.kind)
        if problems:
            return QuickReply(_PARTLY[language].format(names=_names(problems, language)), language, command.kind)
        return QuickReply(None, language, command.kind)  # the lights changing is the answer

    async def _tv_action(self, command: QuickCommand, **kwargs: Any) -> QuickReply | None:
        if getattr(self._deps, "tv", None) is None:
            return None
        result = await self._tv(self._deps, **kwargs)
        language = command.language
        return QuickReply(None if result.get("ok") else _FAILED[language], language, command.kind)


_ARTIST_OF = re.compile(
    r"^(?:(?:some\s+)?(?:music|songs)\s+(?:by|from)|(?:musica|canciones|temas)\s+de|(?:musica|canzoni)\s+di|"
    r"(?:musica|musicas|cancoes)\s+(?:de|da|do))\s+(?P<artist>.+)$"
)
_TRACK_OF = re.compile(r"^(?:the song|la cancion|el tema|la canzone|a musica|a cancao)\s+(?P<q>.+)$")
_ALBUM_OF = re.compile(r"^(?:the album|el album|l album|o album|o disco|el disco)\s+(?P<q>.+)$")
_PLAYLIST_OF = re.compile(
    r"^(?:the playlist|my playlist|la playlist|a playlist|mi playlist|la lista)\s+(?P<q>.+)$|^(?P<q2>.+?)\s+playlist$"
)
_GENRES = frozenset(
    {"jazz", "rock", "pop", "classical", "classical music", "reggaeton", "salsa", "lofi", "lo fi", "hip hop", "rap",
     "blues", "country", "metal", "disco", "techno", "house", "ambient", "piano", "relaxing", "chill", "kids",
     "lullabies", "bachata", "merengue", "flamenco", "fado", "opera", "bossa nova", "samba", "kpop", "k pop",
     "christmas", "navidad", "natale", "natal", "cumbia", "funk", "soul", "reggae", "indie", "dance", "electronic",
     "musica clasica", "musica classica", "infantil", "canciones infantiles", "canzoni per bambini", "baby"}
)  # fmt: skip
_GENERIC_MUSIC = re.compile(r"\b(?:music|songs|musica|canciones|canzoni|musicas|cancoes|hits)\b")


def _music_search_type(query: str) -> tuple[str, str]:
    """Return (search type or "auto", search text) for a "play ..." request."""
    query = re.sub(r"^(?:some|a bit of|a little|un poco de|algo de|un po di|um pouco de)\s+", "", query).strip()
    for pattern, kind in (
        (_ARTIST_OF, "artist"),
        (_TRACK_OF, "track"),
        (_ALBUM_OF, "album"),
        (_PLAYLIST_OF, "playlist"),
    ):
        match = pattern.match(query)
        if match:
            groups = match.groupdict()
            return kind, (groups.get("artist") or groups.get("q") or groups.get("q2") or query).strip()
    if _GENERIC_MUSIC.search(query) or query in _GENRES:
        return "playlist", query
    return "auto", query


def _choose(query: str, artists: list[dict[str, Any]], tracks: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Pick the artist when the request names one, a track when it names a song."""
    wanted = normalize(query)
    track = tracks[0] if tracks else None
    artist = artists[0] if artists else None
    if track is not None and normalize(str(track["name"])) and normalize(str(track["name"])) in wanted:
        return track
    if artist is not None and normalize(str(artist["name"])) == wanted:
        return artist
    if artist is not None and normalize(str(artist["name"])) and normalize(str(artist["name"])) in wanted:
        return artist
    return track or artist


def _spotify_configured() -> bool:
    import os

    return bool(os.getenv("SPOTIFY_CREDENTIALS_FILE", "").strip() and os.getenv("SPOTIFY_DEVICE_ID", "").strip())


# Said when Reachy cannot reach Gemini for a conversation (no credits, no internet); the Mac's own voice
# speaks it if Gemini's voice is unreachable too.
CANNOT_CONNECT: Final[dict[str, str]] = {
    "en": "Sorry, I can't connect right now. Try again in a little while.",
    "es": "Lo siento, ahora no puedo conectarme. Inténtalo dentro de un rato.",
    "it": "Scusa, ora non riesco a collegarmi. Riprova tra un po'.",
    "pt": "Desculpa, agora não consigo ligar-me. Tenta daqui a pouco.",
}
_GUESS_MARKERS: Final[dict[str, frozenset[str]]] = {
    "en": frozenset({"the", "you", "can", "what", "please", "show", "how", "my", "is", "are", "do", "i", "me", "and"}),
    "es": _SPANISH_MARKERS | {"puedes", "por", "favor", "como", "un", "una", "haz", "eso", "esta", "de", "me", "mi"},
    "pt": _PORTUGUESE_MARKERS | {"podes", "por", "favor", "como", "um", "uma", "faz", "isso", "esta", "de", "me"},
    "it": _ITALIAN_MARKERS | {"puoi", "per", "favore", "come", "un", "una", "fai", "questo", "di", "mi"},
}


def guess_language(text: str, default: str = "en") -> str:
    """Guess which household language ``text`` is in from common words; ``default`` when unsure."""
    words = normalize(text).split()
    counts = {language: sum(word in markers for word in words) for language, markers in _GUESS_MARKERS.items()}
    best = max(counts, key=lambda language: counts[language])
    return best if counts[best] else default


# Replies that never change, worth keeping on disk for instant playback (local_speech cache).
FIXED_PHRASES: Final[frozenset[str]] = frozenset(
    {
        *_OK.values(),
        *_FAILED.values(),
        *_GOOD_NIGHT.values(),
        *_NOT_FOUND.values(),
        *CANNOT_CONNECT.values(),
        *MARIBEL_ASKING.values(),
        *MARIBEL_PASSED_ON.values(),
        *MARIBEL_UNREACHABLE.values(),
        *(text for text, _ in PHOTO_PHRASES),
    }
)
