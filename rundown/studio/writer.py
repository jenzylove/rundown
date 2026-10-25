"""Rules Writer: titles, stat cards and presenter lines from facts, in English,
Spanish, French and Portuguese, for an analyst or a casual fan. Every number comes
from a Fact; each line carries its fact keys. The agent Writer replaces these lines;
this is its fallback.

Templates are str.format strings with named fields (see `_nums`). Numbers are only
ever a fact's value or a detail value, written as stored (comma decimals in es, fr, pt).
Where a pair of values can move either way the template uses neutral wording."""

from __future__ import annotations

from ..agents.words import COMMA_DECIMAL, fmt
from ..engine import stats as S
from ..engine.data import MatchData
from ..engine.detect import Candidate

T = {
    "en": dict(
        press_up=("The press is on", "{team} are winning it back higher"),
        flank_overload=("Down the {side}", "{team} keep finding the extra player"),
        fatigue=("Running on empty", "{player} has lost his legs"),
        deep_block=("Backs to the wall", "{team} are sitting deeper"),
        tempo_up=("Faster, faster", "{team} are moving it quicker"),
        goal=("The goal", "{player} finishes it"),
        big_chance=("The big chance", "{player} should score"),
        player_focus=("Eyes on {player}", "How {player} is playing"),
        card_before="Before", card_now="Last 10 min",
        left="left", right="right",
    ),
    "es": dict(
        press_up=("La presión aprieta", "{team} recuperan más arriba"),
        flank_overload=("Por la {side}", "{team} encuentran siempre al hombre libre"),
        fatigue=("Sin piernas", "{player} ya no llega"),
        deep_block=("Contra las cuerdas", "{team} se repliegan más atrás"),
        tempo_up=("Más rápido", "{team} mueven el balón más rápido"),
        goal=("El gol", "{player} la define"),
        big_chance=("La gran ocasión", "{player} tuvo que marcar"),
        player_focus=("Foco en {player}", "Así está jugando {player}"),
        card_before="Antes", card_now="Últimos 10 min",
        left="izquierda", right="derecha",
    ),
    "fr": dict(
        press_up=("Le pressing monte", "{team} récupère plus haut"),
        flank_overload=("Par la {side}", "{team} trouve toujours l'homme libre"),
        fatigue=("Plus de jambes", "{player} ne suit plus"),
        deep_block=("Dos au mur", "{team} se replie plus bas"),
        tempo_up=("Plus vite", "{team} fait circuler le ballon plus vite"),
        goal=("Le but", "{player} conclut"),
        big_chance=("La grosse occasion", "{player} devait marquer"),
        player_focus=("Focus sur {player}", "Comment joue {player}"),
        card_before="Avant", card_now="10 dernières min",
        left="gauche", right="droite",
    ),
    "pt": dict(
        press_up=("A pressão aperta", "{team} recupera a bola mais alto"),
        flank_overload=("Pela {side}", "{team} sempre acha o homem livre"),
        fatigue=("Sem pernas", "{player} não aguenta mais"),
        deep_block=("Contra a parede", "{team} recua o bloco"),
        tempo_up=("Mais rápido", "{team} toca a bola mais rápido"),
        goal=("O gol", "{player} finaliza"),
        big_chance=("A grande chance", "{player} tinha que marcar"),
        player_focus=("Olho em {player}", "Como {player} está jogando"),
        card_before="Antes", card_now="Últimos 10 min",
        left="esquerda", right="direita",
    ),
}

# Stat card labels. Analyst: the metric names. Casual: plain words, never PPDA or field tilt.
LABELS = {
    "analyst": {
        "en": dict(ppda="PPDA", press="Pressures/min", flank="Attacks that side", over="Overloads",
                   speed="Top speed", sprints="Sprints", line="Line height", tilt="Opponent field tilt",
                   tempo="Passes per minute on the ball", passes="Pass completion", dist="Distance covered",
                   minute="Minute"),
        "es": dict(ppda="PPDA", press="Presiones/min", flank="Ataques por ese lado", over="Superioridades",
                   speed="Velocidad punta", sprints="Sprints", line="Altura de la línea",
                   tilt="Dominio territorial rival", tempo="Pases por minuto con balón",
                   passes="Pases completados", dist="Distancia recorrida", minute="Minuto"),
        "fr": dict(ppda="PPDA", press="Pressions/min", flank="Attaques de ce côté", over="Supériorités",
                   speed="Vitesse de pointe", sprints="Sprints", line="Hauteur de la ligne",
                   tilt="Domination territoriale adverse", tempo="Passes par minute avec le ballon",
                   passes="Passes réussies", dist="Distance parcourue", minute="Minute"),
        "pt": dict(ppda="PPDA", press="Pressões/min", flank="Ataques por esse lado", over="Superioridades",
                   speed="Velocidade máxima", sprints="Sprints", line="Altura da linha",
                   tilt="Domínio territorial do rival", tempo="Passes por minuto com a bola",
                   passes="Passes certos", dist="Distância percorrida", minute="Minuto"),
    },
    "casual": {
        "en": dict(press="Pressing, per minute", high="Presses high up the pitch", flank="Attacks down that side",
                   over="Times with an extra man", speed="Fastest run", sprints="Sprints",
                   line="How deep they defend", tilt="Rivals' share of passes near goal",
                   tempo="Passes a minute with the ball", passes="Passes that find a teammate",
                   dist="Distance run", minute="Minute", chance="Quality of the chance"),
        "es": dict(press="Presión por minuto", high="Presiones arriba en el campo", flank="Ataques por ese lado",
                   over="Veces con un hombre más", speed="Carrera más rápida", sprints="Sprints",
                   line="Lo atrás que defienden", tilt="Pases del rival cerca del área",
                   tempo="Pases por minuto con el balón", passes="Pases que llegan a un compañero",
                   dist="Distancia recorrida", minute="Minuto", chance="Calidad de la ocasión"),
        "fr": dict(press="Pressing par minute", high="Pressings haut sur le terrain", flank="Attaques de ce côté",
                   over="Fois en supériorité", speed="Course la plus rapide", sprints="Sprints",
                   line="Hauteur de la défense", tilt="Part des passes adverses près du but",
                   tempo="Passes par minute avec le ballon", passes="Passes qui trouvent un coéquipier",
                   dist="Distance parcourue", minute="Minute", chance="Qualité de l'occasion"),
        "pt": dict(press="Pressão por minuto", high="Pressões no alto do campo", flank="Ataques por esse lado",
                   over="Vezes com um a mais", speed="Arrancada mais rápida", sprints="Sprints",
                   line="Quão atrás defendem", tilt="Parte dos passes do rival perto do gol",
                   tempo="Passes por minuto com a bola", passes="Passes que acham um companheiro",
                   dist="Distância percorrida", minute="Minuto", chance="Qualidade da chance"),
    },
}

# Which facts each spoken line cites (indexes into the candidate's facts); the same in every language.
KEYS = {
    "analyst": dict(press_up=[[], [0, 1], [2, 3]], flank_overload=[[], [0, 1], [3]],
                    fatigue=[[], [0, 1], [0, 1]], deep_block=[[], [0, 1], [2, 3]],
                    tempo_up=[[], [0, 1]], goal=[[], [0]], big_chance=[[], [0]]),
    "casual": dict(press_up=[[], [], [3]], flank_overload=[[], [1], [3]],
                   fatigue=[[], [1], []], deep_block=[[], [3], [1]],
                   tempo_up=[[], [1]], goal=[[], []], big_chance=[[], []]),
}

# Fields: team, opp, player, side, n0 n1 (first before/now pair), m0 m1 (second pair),
# s0 s1 (top speed, fatigue), pct dist top (player focus), xg, minute.
SCRIPTS = {
    "analyst": {
        "en": dict(
            press_up=["Look at how {team} have changed in recent minutes.",
                      "They were allowing {n0} passes per defensive action. Now it's {n1}.",
                      "They're pressing higher, and {opp} have no time to play out."],
            flank_overload=["{team} have made their plan obvious. Everything goes down the {side}.",
                            "{n1}% of their final-third attacks start there, up from {n0}%.",
                            "And they arrive with the extra man: {m1} times lately."],
            fatigue=["Keep an eye on {player}.",
                     "His top speed has dropped from {s0} to {s1} kilometres an hour.",
                     "His sprints went from {n0} to {n1}. {opp} will go at him."],
            deep_block=["{team} have taken a step back.",
                        "Their back line has dropped from {n0} to {n1} metres from goal.",
                        "{opp} now have {m1}% of the passes in the final third."],
            tempo_up=["{team} have turned up the tempo.",
                      "From {n0} to {n1} passes for every minute on the ball."],
            goal=["{player}'s goal, in the {minute} minute.", "A chance worth {xg} expected goals."],
            big_chance=["{team}'s best chance fell to {player}.", "It was worth {xg} expected goals."],
            player_focus=dict(intro="Keep your eyes on {player}.", passes="He has completed {pct}% of his passes.",
                              run="He has covered {dist} metres, with a top speed of {top} kilometres an hour.",
                              shot="His best chance was worth {xg} expected goals."),
        ),
        "es": dict(
            press_up=["Mira cómo ha cambiado {team} en los últimos minutos.",
                      "Dejaban {n0} pases por acción defensiva. Ahora solo {n1}.",
                      "Presionan más alto y {opp} no tiene tiempo para salir."],
            flank_overload=["{team} lo tienen claro: todo por la {side}.",
                            "El {n1}% de sus ataques en el último tercio nace por ahí, antes era el {n0}%.",
                            "Y llegan con superioridad: {m1} veces en los últimos minutos."],
            fatigue=["Atención a {player}.",
                     "Su velocidad punta ha bajado de {s0} a {s1} km/h.",
                     "Sus sprints pasaron de {n0} a {n1}. {opp} lo va a buscar."],
            deep_block=["{team} han dado un paso atrás.",
                        "Su línea defensiva ha bajado de {n0} a {n1} metros.",
                        "{opp} ya tiene el {m1}% de los pases en el último tercio."],
            tempo_up=["{team} han subido el ritmo.", "De {n0} a {n1} pases por minuto con el balón."],
            goal=["El gol de {player}, minuto {minute}.", "Una ocasión de {xg} goles esperados."],
            big_chance=["La mejor ocasión de {team}: {player}.", "Valía {xg} goles esperados."],
            player_focus=dict(intro="Atención a {player}.", passes="Ha completado el {pct}% de sus pases.",
                              run="Ha recorrido {dist} metros, con una punta de {top} km/h.",
                              shot="Su mejor ocasión valía {xg} goles esperados."),
        ),
        "fr": dict(
            press_up=["Regardez comme {team} a changé ces dernières minutes.",
                      "Ils laissaient {n0} passes par action défensive. Désormais, seulement {n1}.",
                      "Ils pressent plus haut et {opp} n'a plus le temps de relancer."],
            flank_overload=["{team} ne cache plus son plan : tout passe par la {side}.",
                            "{n1} % de leurs attaques dans le dernier tiers naissent de ce côté, contre {n0} % avant.",
                            "Et ils arrivent en supériorité : {m1} fois ces dernières minutes."],
            fatigue=["Gardez un œil sur {player}.",
                     "Sa vitesse de pointe a baissé de {s0} à {s1} km/h.",
                     "Ses sprints sont passés de {n0} à {n1}. {opp} va le chercher."],
            deep_block=["{team} a fait un pas en arrière.",
                        "Sa ligne défensive recule de {n0} à {n1} mètres.",
                        "{opp} a désormais {m1} % des passes dans le dernier tiers."],
            tempo_up=["{team} a haussé le rythme.", "De {n0} à {n1} passes par minute avec le ballon."],
            goal=["But de {player}, minute {minute}.", "Une occasion de {xg} en buts attendus."],
            big_chance=["La plus grosse occasion de {team} : {player}.", "Elle valait {xg} en buts attendus."],
            player_focus=dict(intro="Gardez les yeux sur {player}.", passes="Il a réussi {pct} % de ses passes.",
                              run="Il a parcouru {dist} mètres, avec une pointe à {top} km/h.",
                              shot="Sa meilleure occasion valait {xg} en buts attendus."),
        ),
        "pt": dict(
            press_up=["Veja como {team} mudou nos últimos minutos.",
                      "Eles deixavam {n0} passes por ação defensiva. Agora, só {n1}.",
                      "Pressionam mais alto e {opp} não tem tempo para sair jogando."],
            flank_overload=["{team} deixou o plano claro: tudo pela {side}.",
                            "{n1}% dos ataques no terço final nascem ali, contra {n0}% antes.",
                            "E chegam em superioridade: {m1} vezes nos últimos minutos."],
            fatigue=["Atenção em {player}.",
                     "A velocidade máxima dele caiu de {s0} para {s1} km/h.",
                     "Os sprints foram de {n0} para {n1}. {opp} vai pra cima dele."],
            deep_block=["{team} deu um passo atrás.",
                        "A linha defensiva recuou de {n0} para {n1} metros.",
                        "{opp} já tem {m1}% dos passes no terço final."],
            tempo_up=["{team} aumentou o ritmo.", "De {n0} para {n1} passes por minuto com a bola."],
            goal=["Gol de {player}, aos {minute} minutos.", "Uma chance de {xg} gols esperados."],
            big_chance=["A melhor chance de {team}: {player}.", "Valia {xg} gols esperados."],
            player_focus=dict(intro="Atenção em {player}.", passes="Ele acertou {pct}% dos passes.",
                              run="Percorreu {dist} metros, com pico de {top} km/h.",
                              shot="A melhor chance dele valia {xg} gols esperados."),
        ),
    },
    # casual: what it means for the match, one striking number a line at most
    "casual": {
        "en": dict(
            press_up=["{team} are hunting the ball high up the pitch.",
                      "They are winning it back close to the other goal, so {opp} can't get out.",
                      "Right now that's {m1} presses every minute."],
            flank_overload=["{team} keep attacking down the {side}, again and again.",
                            "{n1}% of their attacks now come from that side.",
                            "They keep getting an extra man there: {m1} times lately."],
            fatigue=["Keep an eye on {player}.",
                     "He is running out of steam, and his top speed is down to {s1} km/h.",
                     "{opp} will go at him."],
            deep_block=["{team} have stepped back and are defending close to their own goal.",
                        "In the final third, {m1}% of the passes are now {opp}'s.",
                        "Their back line is just {n1} metres from goal."],
            tempo_up=["{team} have picked up the pace.",
                      "It is quick, sharp passing now: {n1} passes every minute they have the ball."],
            goal=["{player} scores, in the {minute} minute.", "A proper chance, and he took it."],
            big_chance=["{team}'s best chance fell to {player}.", "It was the kind of chance you expect to score."],
            player_focus=dict(intro="Keep your eyes on {player}.", passes="He finds a teammate with {pct}% of his passes.",
                              run="He has run {dist} metres already.", top="At full speed he reaches {top} km/h.",
                              shot="He had a clear chance to score too."),
        ),
        "es": dict(
            press_up=["{team} presionan muy arriba, casi en la puerta del rival.",
                      "Recuperan el balón cerca de la otra portería y {opp} no puede salir.",
                      "Ahora presionan {m1} veces cada minuto."],
            flank_overload=["{team} insisten una y otra vez por la {side}.",
                            "Ya nace por ahí el {n1}% de sus ataques.",
                            "Y allí suelen tener un hombre más: {m1} veces en los últimos minutos."],
            fatigue=["Atención a {player}.",
                     "Se le acaba la gasolina: su velocidad punta ya es de solo {s1} km/h.",
                     "{opp} lo va a buscar."],
            deep_block=["{team} se han metido atrás y defienden cerca de su portería.",
                        "En el último tercio, el {m1}% de los pases ya son de {opp}.",
                        "Su línea defensiva está a {n1} metros de la portería."],
            tempo_up=["{team} han subido el ritmo.",
                      "Toque rápido y preciso: {n1} pases cada minuto con el balón."],
            goal=["Gol de {player}, en el minuto {minute}.", "Una ocasión clara y no la perdonó."],
            big_chance=["La mejor ocasión de {team} fue para {player}.", "De esas que casi siempre acaban en gol."],
            player_focus=dict(intro="Atención a {player}.", passes="Acierta el {pct}% de sus pases.",
                              run="Ya ha corrido {dist} metros.", top="Y llegó a {top} km/h en su carrera más rápida.",
                              shot="También tuvo una ocasión clara de gol."),
        ),
        "fr": dict(
            press_up=["{team} presse très haut sur le terrain.",
                      "Il récupère le ballon près de l'autre but et {opp} ne peut plus sortir.",
                      "En ce moment, cela fait {m1} pressings chaque minute."],
            flank_overload=["{team} insiste encore et encore par la {side}.",
                            "{n1} % de ses attaques passent maintenant par là.",
                            "Et il y a souvent un joueur de plus : {m1} fois récemment."],
            fatigue=["Gardez un œil sur {player}.",
                     "Il n'a plus de jus : sa vitesse de pointe n'est plus que de {s1} km/h.",
                     "{opp} va le chercher."],
            deep_block=["{team} s'est replié et défend près de son but.",
                        "Dans le dernier tiers, {m1} % des passes sont désormais pour {opp}.",
                        "Sa défense se tient à {n1} mètres du but."],
            tempo_up=["{team} a accéléré le jeu.",
                      "Jeu rapide et précis : {n1} passes chaque minute avec le ballon."],
            goal=["But de {player}, à la minute {minute}.", "Une vraie occasion, et il ne l'a pas ratée."],
            big_chance=["La plus belle occasion de {team} est pour {player}.",
                        "Le genre d'occasion qu'on doit mettre au fond."],
            player_focus=dict(intro="Gardez les yeux sur {player}.", passes="Il trouve un coéquipier sur {pct} % de ses passes.",
                              run="Il a déjà couru {dist} mètres.", top="À pleine vitesse, il atteint {top} km/h.",
                              shot="Il a aussi eu une grosse occasion."),
        ),
        "pt": dict(
            press_up=["{team} pressiona lá no alto do campo.",
                      "Recuperam a bola perto do gol adversário e {opp} não consegue sair.",
                      "Agora são {m1} pressões a cada minuto."],
            flank_overload=["{team} insiste pela {side}, de novo e de novo.",
                            "Já são {n1}% dos ataques saindo desse lado.",
                            "E ali sempre sobra um jogador a mais: {m1} vezes ultimamente."],
            fatigue=["Atenção em {player}.",
                     "O fôlego acabou: a velocidade máxima dele agora é de só {s1} km/h.",
                     "{opp} vai pra cima dele."],
            deep_block=["{team} se fechou lá atrás e defende perto do próprio gol.",
                        "No terço final, {m1}% dos passes já são do {opp}.",
                        "A linha defensiva está a {n1} metros do gol."],
            tempo_up=["{team} acelerou o jogo.", "Toque rápido e preciso: {n1} passes por minuto com a bola."],
            goal=["Gol de {player}, aos {minute} minutos.", "Chance clara, e ele não perdoou."],
            big_chance=["A melhor chance de {team} ficou com {player}.",
                        "Daquelas que a gente espera ver no fundo da rede."],
            player_focus=dict(intro="Atenção em {player}.", passes="Ele acerta {pct}% dos passes.",
                              run="Já correu {dist} metros.", top="E chegou a {top} km/h na maior arrancada.",
                              shot="E teve uma chance clara de gol."),
        ),
    },
}


def ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _ctx(md: MatchData, c: Candidate, lang: str) -> dict:
    t = T[lang]
    return dict(team=S._short(md, c.team), opp=S._short(md, md.opponent(c.team)),
                player=md.name(c.player) if c.player else "", side=t.get(c.side or "", c.side or ""))


def _xg(v: float, lang: str) -> str:
    s = f"{v:.2f}"
    return s.replace(".", ",") if lang in COMMA_DECIMAL else s


def _nums(md: MatchData, c: Candidate, lang: str) -> dict:
    """The numbers a template may use, each a fact value or detail value as stored."""
    f, n, d = c.facts, (lambda v: fmt(v, lang)), {}
    if c.kind in ("press_up", "flank_overload", "deep_block"):
        d = dict(n0=n(f[0].value), n1=n(f[1].value), m0=n(f[2].value), m1=n(f[3].value))
    elif c.kind == "fatigue":
        d = dict(n0=n(f[0].value), n1=n(f[1].value),
                 s0=n(f[0].detail["top_speed_kmh"]), s1=n(f[1].detail["top_speed_kmh"]))
    elif c.kind == "tempo_up":
        d = dict(n0=n(f[0].value), n1=n(f[1].value))
    elif c.kind == "player_focus":
        d = dict(pct=n(f[0].value), dist=n(f[1].detail["distance_m"]), top=n(f[1].detail["top_speed_kmh"]))
        shots = [q for q in f[2:] if q.stat == "shot_xg"]
        if shots:
            d["xg"] = _xg(max(q.value for q in shots), lang)
    else:
        minute = int(md.by_id[f[0].args["event_id"]]["minute"]) + 1
        d = dict(xg=_xg(f[0].value, lang), minute=ordinal(minute) if lang == "en" else minute)
    return d


def title(md, c, lang="en") -> str:
    return T[lang][c.kind][0].format(**_ctx(md, c, lang))


def subtitle(md, c, lang="en") -> str:
    return T[lang][c.kind][1].format(**_ctx(md, c, lang))


def card_title(md, c, lang="en") -> str:
    return S._short(md, c.team) if c.kind not in ("fatigue", "player_focus") else md.name(c.player)


def stat_rows(md: MatchData, c: Candidate, lang: str, depth: str = "analyst") -> tuple[list[list[str]], list[S.Fact]]:
    t = T[lang]
    f = c.facts
    n = lambda v: fmt(v, lang)
    pc = lambda v: f"{n(v)} %" if lang == "fr" else f"{n(v)}%"
    arrow = lambda a, b, unit="": f"{a} → {b}{unit}"
    if depth == "casual":
        L = LABELS["casual"][lang]
        if c.kind == "press_up":
            return ([[L["press"], arrow(n(f[2].value), n(f[3].value))],
                     [L["high"], n(f[3].detail["in_opp_half"])]], f[2:4])
        if c.kind == "flank_overload":
            return ([[L["flank"], arrow(pc(f[0].value), pc(f[1].value))], [L["over"], n(f[3].value)]], f[:4])
        if c.kind == "fatigue":
            return ([[L["speed"], arrow(n(f[0].detail["top_speed_kmh"]), n(f[1].detail["top_speed_kmh"]), " km/h")],
                     [L["sprints"], arrow(n(f[0].value), n(f[1].value))]], f[:2])
        if c.kind == "deep_block":
            return ([[L["line"], arrow(n(f[0].value), n(f[1].value), " m")],
                     [L["tilt"], arrow(pc(f[2].value), pc(f[3].value))]], f[:4])
        if c.kind == "tempo_up":
            return ([[L["tempo"], arrow(n(f[0].value), n(f[1].value))]], f[:2])
        if c.kind == "player_focus":
            return ([[L["passes"], pc(f[0].value)], [L["dist"], f"{n(f[1].detail['distance_m'])} m"]], f[:2])
        sh = f[0]
        ev = md.by_id[sh.args["event_id"]]
        return ([[L["minute"], f"{int(ev['minute']) + 1}'"], [L["chance"], _xg(sh.value, lang)]], [sh])

    L = LABELS["analyst"][lang]
    if c.kind == "press_up":
        return ([[L["ppda"] + " " + t["card_before"].lower(), n(f[0].value)],
                 [L["ppda"] + " " + t["card_now"].lower(), n(f[1].value)],
                 [L["press"], arrow(n(f[2].value), n(f[3].value))]], f[:4])
    if c.kind == "flank_overload":
        return ([[L["flank"], arrow(pc(f[0].value), pc(f[1].value))], [L["over"], n(f[3].value)]], f[:4])
    if c.kind == "fatigue":
        return ([[L["speed"], arrow(n(f[0].detail["top_speed_kmh"]), n(f[1].detail["top_speed_kmh"]), " km/h")],
                 [L["sprints"], arrow(n(f[0].value), n(f[1].value))]], f[:2])
    if c.kind == "deep_block":
        return ([[L["line"], arrow(n(f[0].value), n(f[1].value), " m")],
                 [L["tilt"], arrow(pc(f[2].value), pc(f[3].value))]], f[:4])
    if c.kind == "tempo_up":
        return ([[L["tempo"] + ", " + t["card_before"].lower(), n(f[0].value)],
                 [L["tempo"] + ", " + t["card_now"].lower(), n(f[1].value)]], f[:2])
    if c.kind == "player_focus":
        ps, ph = f[0], f[1]
        rows = [[L["passes"], pc(ps.value)],
                [L["dist"], f"{n(ph.detail['distance_m'])} m"],
                [L["speed"], f"{n(ph.detail['top_speed_kmh'])} km/h"]]
        shots = [q for q in f[2:] if q.stat == "shot_xg"]
        if shots:
            rows.append(["xG", _xg(max(q.value for q in shots), lang)])
        return rows, f
    sh = f[0]
    ev = md.by_id[sh.args["event_id"]]
    return ([["xG", _xg(sh.value, lang)], [L["minute"], f"{int(ev['minute']) + 1}'"]], [sh])


def script(md: MatchData, c: Candidate, lang: str = "en", depth: str = "analyst") -> list[tuple[str, list[str]]]:
    depth = "casual" if depth == "casual" else "analyst"
    fields = {**_ctx(md, c, lang), **_nums(md, c, lang)}
    k = [q.key for q in c.facts]
    tpl = SCRIPTS[depth][lang][c.kind]
    if c.kind == "player_focus":
        ps, ph = c.facts[0], c.facts[1]
        out = [(tpl["intro"].format(**fields), [])]
        if ps.detail["attempted"]:
            out.append((tpl["passes"].format(**fields), [ps.key]))
        out.append((tpl["run"].format(**fields), [ph.key]))
        if "top" in tpl:
            out.append((tpl["top"].format(**fields), [ph.key]))
        shots = [q for q in c.facts[2:] if q.stat == "shot_xg"]
        if shots:
            best = max(shots, key=lambda q: q.value)
            out.append((tpl["shot"].format(**fields), [best.key]))
        return out
    return [(line.format(**fields), [k[i] for i in idx]) for line, idx in zip(tpl, KEYS[depth][c.kind])]
