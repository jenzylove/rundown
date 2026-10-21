"""Rules Writer: titles, stat cards and presenter lines from facts, in English
and Spanish. Every number comes from a Fact; each line carries its fact keys.
The agent Writer replaces these lines; this is its fallback."""

from __future__ import annotations

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
        card_before="Antes", card_now="Últimos 10 min",
        left="izquierda", right="derecha",
    ),
}


def ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _ctx(md: MatchData, c: Candidate, lang: str) -> dict:
    t = T[lang]
    return dict(team=S._short(md, c.team), opp=S._short(md, md.opponent(c.team)),
                player=md.name(c.player) if c.player else "", side=t.get(c.side or "", c.side or ""))


def title(md, c, lang="en") -> str:
    return T[lang][c.kind][0].format(**_ctx(md, c, lang))


def subtitle(md, c, lang="en") -> str:
    return T[lang][c.kind][1].format(**_ctx(md, c, lang))


def card_title(md, c, lang="en") -> str:
    return S._short(md, c.team) if c.kind != "fatigue" else md.name(c.player)


def stat_rows(md: MatchData, c: Candidate, lang: str) -> tuple[list[list[str]], list[S.Fact]]:
    t = T[lang]
    f = c.facts
    es = lang == "es"
    if c.kind == "press_up":
        return ([["PPDA " + t["card_before"].lower(), f"{f[0].value}"], ["PPDA " + t["card_now"].lower(), f"{f[1].value}"],
                 ["Presiones/min" if es else "Pressures/min", f"{f[2].value} → {f[3].value}"]], f[:4])
    if c.kind == "flank_overload":
        return ([["Ataques por ese lado" if es else "Attacks that side", f"{f[0].value}% → {f[1].value}%"],
                 ["Superioridades" if es else "Overloads", f"{f[3].value}"]], f[:4])
    if c.kind == "fatigue":
        return ([["Velocidad punta" if es else "Top speed", f"{f[0].detail['top_speed_kmh']} → {f[1].detail['top_speed_kmh']} km/h"],
                 ["Sprints", f"{f[0].value} → {f[1].value}"]], f[:2])
    if c.kind == "deep_block":
        return ([["Altura de la línea" if es else "Line height", f"{f[0].value} → {f[1].value} m"],
                 [("Dominio territorial rival" if es else "Opponent field tilt"), f"{f[2].value}% → {f[3].value}%"]], f[:4])
    if c.kind == "tempo_up":
        return ([["Pases por minuto con balón" if es else "Passes per minute on the ball", f"{f[0].value} → {f[1].value}"]], f[:2])
    sh = f[0]
    ev = md.by_id[sh.args["event_id"]]
    return ([["xG", f"{sh.value:.2f}"], ["Minuto" if es else "Minute", f"{int(ev['minute']) + 1}'"]], [sh])


def script(md: MatchData, c: Candidate, lang: str = "en") -> list[tuple[str, list[str]]]:
    x = _ctx(md, c, lang)
    f = c.facts
    k = [q.key for q in f]
    es = lang == "es"
    if c.kind == "press_up":
        return [
            (f"Mira cómo ha cambiado {x['team']} en los últimos diez minutos." if es else
             f"Look at how {x['team']} have changed in the last ten minutes.", []),
            (f"Dejaban {f[0].value} pases por acción defensiva. Ahora solo {f[1].value}." if es else
             f"They were allowing {f[0].value} passes per defensive action. Now it's {f[1].value}.", k[:2]),
            (f"Presionan más alto y {x['opp']} no tiene tiempo para salir." if es else
             f"They're pressing higher, and {x['opp']} have no time to play out.", k[2:4]),
        ]
    if c.kind == "flank_overload":
        return [
            (f"{x['team']} lo tienen claro: todo por la {x['side']}." if es else
             f"{x['team']} have made their plan obvious. Everything goes down the {x['side']}.", []),
            (f"El {f[1].value}% de sus ataques en el último tercio nace por ahí, antes era el {f[0].value}%." if es else
             f"{f[1].value}% of their final-third attacks start there, up from {f[0].value}%.", k[:2]),
            (f"Y llegan con superioridad: {f[3].value} veces en diez minutos." if es else
             f"And they arrive with the extra man: {f[3].value} times in ten minutes.", k[3:4]),
        ]
    if c.kind == "fatigue":
        return [
            (f"Atención a {x['player']}." if es else f"Keep an eye on {x['player']}.", []),
            (f"Su velocidad punta ha bajado de {f[0].detail['top_speed_kmh']} a {f[1].detail['top_speed_kmh']} km/h." if es else
             f"His top speed has dropped from {f[0].detail['top_speed_kmh']} to {f[1].detail['top_speed_kmh']} kilometres an hour.", k[:2]),
            (f"Y los sprints, de {f[0].value} a {f[1].value}. {x['opp']} lo va a buscar." if es else
             f"His sprints are down from {f[0].value} to {f[1].value}. {x['opp']} will go at him.", k[:2]),
        ]
    if c.kind == "deep_block":
        return [
            (f"{x['team']} han dado un paso atrás." if es else f"{x['team']} have taken a step back.", []),
            (f"Su línea defensiva ha bajado de {f[0].value} a {f[1].value} metros." if es else
             f"Their back line has dropped from {f[0].value} to {f[1].value} metres from goal.", k[:2]),
            (f"{x['opp']} ya tiene el {f[3].value}% de los pases en el último tercio." if es else
             f"{x['opp']} now have {f[3].value}% of the passes in the final third.", k[2:4]),
        ]
    if c.kind == "tempo_up":
        return [
            (f"{x['team']} han subido el ritmo." if es else f"{x['team']} have turned up the tempo.", []),
            (f"De {f[0].value} a {f[1].value} pases por minuto con el balón." if es else
             f"From {f[0].value} to {f[1].value} passes for every minute on the ball.", k[:2]),
        ]
    ev = md.by_id[f[0].args["event_id"]]
    if c.kind == "goal":
        return [(f"El gol de {x['player']}, minuto {int(ev['minute']) + 1}." if es else
                 f"{x['player']}'s goal, in the {ordinal(int(ev['minute']) + 1)} minute.", []),
                (f"Una ocasión de {f[0].value:.2f} goles esperados." if es else
                 f"A chance worth {f[0].value:.2f} expected goals.", k[:1])]
    return [(f"La mejor ocasión de {x['team']}: {x['player']}." if es else
             f"{x['team']}'s best chance fell to {x['player']}.", []),
            (f"Valía {f[0].value:.2f} goles esperados." if es else f"It was worth {f[0].value:.2f} expected goals.", k[:1])]
